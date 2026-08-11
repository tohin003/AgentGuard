"""RepoIndex — the deterministic evidence base (SPEC §32).

    Repository -> File map -> Symbol map -> Import graph -> Dependency graph
    -> Test map -> Configuration map -> Git history

Everything downstream queries this object:

* **Intent Gateway (§9)** resolves the identifiers in a prompt to real files and symbols,
  which is what makes intent extraction grounded rather than keyword matching.
* **Complexity Engine (§12)** reads blast radius straight off the reverse-import graph.
* **Evidence Engine (§14)** asks whether a claimed symbol, file, module or dependency
  actually exists.
* **Verification (§19)** asks which tests cover a changed file.

Two rules govern this module:

1. **Absence of evidence is not evidence of absence.** If a language has no extractor,
   the index holds no symbols for it and must report "unknown", never "does not exist".
   Reporting absence would manufacture false challenges.
2. **Nothing here may raise.** The agent edits files constantly, so the index is
   routinely asked to parse half-written code. Failures are skipped, not propagated.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from agentguard.core.config import IndexSettings
from agentguard.core.metrics import M_INDEX_BUILD, METRICS, Timer
from agentguard.repo import manifests, scanner, symbols_python, symbols_ts
from agentguard.repo.gitinfo import GitCache
from agentguard.repo.models import (
    DependencyInfo,
    FileRecord,
    GitState,
    ImportRecord,
    SymbolRecord,
)

log = logging.getLogger(__name__)

# Prefixes stripped when turning a file path into an importable Python module name,
# so `src/agentguard/core/store.py` resolves for `agentguard.core.store`.
_PY_SOURCE_ROOTS: tuple[str, ...] = ("", "src/", "lib/", "app/", "source/")

_JS_EXTENSIONS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_JS_INDEX_FILES: tuple[str, ...] = tuple(f"/index{ext}" for ext in _JS_EXTENSIONS)


@dataclass(slots=True)
class ParsedFile:
    symbols: list[SymbolRecord] = field(default_factory=list)
    imports: list[ImportRecord] = field(default_factory=list)
    # True only when the file parsed cleanly. Symbols found in a partially-broken file
    # are still real (positive evidence is always safe to trust), but only a clean parse
    # licenses concluding that something is *absent* from the file.
    parsed: bool = False


class RepoIndex:
    """Warm, incrementally-refreshed picture of one repository."""

    def __init__(self, root: Path, settings: IndexSettings | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.settings = settings or IndexSettings()
        self._lock = threading.RLock()

        self.files: dict[str, FileRecord] = {}
        self.parsed: dict[str, ParsedFile] = {}

        self.symbols_by_name: dict[str, list[SymbolRecord]] = defaultdict(list)
        self.symbols_by_qualname: dict[str, SymbolRecord] = {}
        self.attributes_by_owner: dict[str, set[str]] = defaultdict(set)

        self.module_map: dict[str, str] = {}
        self.dependents: dict[str, set[str]] = defaultdict(set)
        self.tests_by_source: dict[str, set[str]] = defaultdict(set)

        self.dependencies = DependencyInfo()
        self._git = GitCache(self.root)

        self.built_at: float = 0.0
        self.build_ms: float = 0.0
        self.is_built: bool = False
        self.last_refresh: float = 0.0
        self._build_thread: threading.Thread | None = None
        self._built = threading.Event()

    # -- building -----------------------------------------------------------------
    #
    # Measured on a real 1,887-file monorepo: a full build takes ~5.6s. That is fine
    # once per session but must never sit in front of the developer, so SessionStart
    # kicks off `build_async()` and every query degrades to "I don't know yet" until it
    # finishes. "Don't know" always resolves to ALLOW upstream (SPEC §39).

    def build_async(self) -> RepoIndex:
        """Start building in the background. Returns immediately."""
        with self._lock:
            if self._build_thread is not None and self._build_thread.is_alive():
                return self
            self._built.clear()
            thread = threading.Thread(target=self._build_quietly, name="agentguard-index", daemon=True)
            self._build_thread = thread
        thread.start()
        return self

    def _build_quietly(self) -> None:
        try:
            self.build()
        except Exception:
            log.exception("agentguard: index build failed for %s", self.root)
        finally:
            self._built.set()

    def ready(self, timeout: float = 0.0) -> bool:
        """Whether the index can answer questions. Optionally waits."""
        if self.is_built:
            return True
        if timeout > 0:
            self._built.wait(timeout)
        return self.is_built

    def build(self) -> RepoIndex:
        with self._lock, Timer() as t:
            self.files = scanner.scan(self.root, self.settings)
            self.parsed = {}
            for path, record in self.files.items():
                self.parsed[path] = self._parse(path, record)
            self.dependencies = manifests.read_dependencies(self.root, self.files)
            self._rebuild_derived()
            self.built_at = time.time()
            self.last_refresh = time.monotonic()
            self.is_built = True
            self._built.set()

        self.build_ms = t.ms
        METRICS.observe(M_INDEX_BUILD, t.ms)
        log.info("agentguard: indexed %d files in %.0fms", len(self.files), t.ms)
        return self

    def refresh_path(self, path: str) -> bool:
        """Re-parse exactly one file.

        The hot path after an edit. A full `refresh()` costs ~100ms on a large repo
        because it re-stats every file; this costs ~1ms, and PostToolUse already tells
        us precisely which file changed.
        """
        rel = self.normalize(path)
        full = self.root / rel
        with self._lock:
            if not full.is_file():
                if rel in self.files:  # deleted
                    self.files.pop(rel, None)
                    self.parsed.pop(rel, None)
                    self._rebuild_derived()
                    return True
                return False

            try:
                st = full.stat()
            except OSError:
                return False

            existing = self.files.get(rel)
            record = FileRecord(
                path=rel,
                size=st.st_size,
                mtime_ns=st.st_mtime_ns,
                lang=scanner.detect_language(rel),
                is_test=scanner.is_test_path(rel),
                is_config=scanner.is_config_path(rel),
            )
            if existing and existing.mtime_ns == record.mtime_ns and existing.size == record.size:
                return False

            self.files[rel] = record
            self.parsed[rel] = self._parse(rel, record)
            if record.is_config:
                self.dependencies = manifests.read_dependencies(self.root, self.files)
            self._rebuild_derived()
            return True

    def refresh(self, min_interval: float = 0.0) -> dict[str, int]:
        """Re-parse only what changed. Returns counts for logging/tests.

        `min_interval` guards against re-statting a large tree on every event.
        """
        if not self.is_built:
            self.build()
            return {"added": len(self.files), "modified": 0, "removed": 0}

        if min_interval and (time.monotonic() - self.last_refresh) < min_interval:
            return {"added": 0, "modified": 0, "removed": 0}

        with self._lock:
            self.last_refresh = time.monotonic()
            current = scanner.scan(self.root, self.settings)
            added = current.keys() - self.files.keys()
            removed = self.files.keys() - current.keys()
            modified = {
                path
                for path in current.keys() & self.files.keys()
                if current[path].mtime_ns != self.files[path].mtime_ns
                or current[path].size != self.files[path].size
            }

            for path in removed:
                self.parsed.pop(path, None)
            for path in added | modified:
                self.parsed[path] = self._parse(path, current[path])

            self.files = current
            if added | removed | modified:
                self.dependencies = manifests.read_dependencies(self.root, self.files)
                self._rebuild_derived()

        return {"added": len(added), "modified": len(modified), "removed": len(removed)}

    def _parse(self, path: str, record: FileRecord) -> ParsedFile:
        if not record.parsable:
            return ParsedFile()
        try:
            source = (self.root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ParsedFile()

        try:
            if record.lang == "python":
                syms, imps, clean = symbols_python.extract(source, path)
                return ParsedFile(symbols=syms, imports=imps, parsed=clean)
            if symbols_ts.available(record.lang):
                syms, imps, clean = symbols_ts.extract(source, path, record.lang)
                return ParsedFile(symbols=syms, imports=imps, parsed=clean)
        except Exception:
            log.debug("agentguard: failed to parse %s", path, exc_info=True)
        return ParsedFile()

    def _rebuild_derived(self) -> None:
        """Rebuild every derived structure from per-file data.

        Cheap enough (pure dict work over already-parsed results) that incremental
        maintenance of the graphs would be complexity without benefit.
        """
        self.symbols_by_name = defaultdict(list)
        self.symbols_by_qualname = {}
        self.attributes_by_owner = defaultdict(set)
        self.dependents = defaultdict(set)
        self.tests_by_source = defaultdict(set)

        for parsed in self.parsed.values():
            for symbol in parsed.symbols:
                self.symbols_by_name[symbol.name].append(symbol)
                self.symbols_by_qualname.setdefault(symbol.qualname, symbol)
                if symbol.parent:
                    self.attributes_by_owner[symbol.parent].add(symbol.name)
                    # Also index by the bare owner name so `UserRepo.method` resolves
                    # when the class is referenced without its module path.
                    self.attributes_by_owner[symbol.parent.rsplit(".", 1)[-1]].add(symbol.name)

        self.module_map = self._build_module_map()

        for path, parsed in self.parsed.items():
            for record in parsed.imports:
                resolved = self._resolve_import(record, path)
                record.resolved = resolved
                if resolved and resolved != path:
                    self.dependents[resolved].add(path)

        self._build_test_map()

    def _build_module_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for path, record in self.files.items():
            if record.lang != "python":
                continue
            for root_prefix in _PY_SOURCE_ROOTS:
                if root_prefix and not path.startswith(root_prefix):
                    continue
                relative = path[len(root_prefix) :]
                module = relative.removesuffix(".py").removesuffix(".pyi")
                module = module.removesuffix("/__init__")
                dotted = module.replace("/", ".")
                if dotted:
                    mapping.setdefault(dotted, path)
        return mapping

    def _resolve_import(self, record: ImportRecord, importer: str) -> str | None:
        lang = self.files.get(importer)
        if lang is None:
            return None
        if lang.lang == "python":
            return self._resolve_python_import(record, importer)
        return self._resolve_js_import(record, importer)

    def _resolve_python_import(self, record: ImportRecord, importer: str) -> str | None:
        if record.level > 0:
            package = importer.rsplit("/", 1)[0] if "/" in importer else ""
            for _ in range(record.level - 1):
                package = package.rsplit("/", 1)[0] if "/" in package else ""
            candidate = f"{package}/{record.raw.replace('.', '/')}" if record.raw else package
            candidate = candidate.strip("/")
            for suffix in (".py", "/__init__.py"):
                if (candidate + suffix) in self.files:
                    return candidate + suffix
            return None

        if not record.raw:
            return None
        if record.raw in self.module_map:
            return self.module_map[record.raw]
        # `from package.module import thing` where `thing` is itself a module.
        for name in record.names:
            joined = f"{record.raw}.{name}"
            if joined in self.module_map:
                return self.module_map[joined]
        return None

    def _resolve_js_import(self, record: ImportRecord, importer: str) -> str | None:
        raw = record.raw
        if not raw.startswith("."):
            return None  # bare specifier: an external package, checked against manifests

        base = importer.rsplit("/", 1)[0] if "/" in importer else ""
        target = str(Path(base) / raw) if base else raw
        target = str(Path(target).as_posix())
        # Normalise ./ and ../ segments.
        parts: list[str] = []
        for part in target.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(part)
        candidate = "/".join(parts)

        if candidate in self.files:
            return candidate
        for ext in _JS_EXTENSIONS:
            if (candidate + ext) in self.files:
                return candidate + ext
        for index_file in _JS_INDEX_FILES:
            if (candidate + index_file) in self.files:
                return candidate + index_file
        return None

    def _build_test_map(self) -> None:
        for path, record in self.files.items():
            if not record.is_test:
                continue
            # A test covers whatever it imports from inside the repo.
            for imported in self.parsed.get(path, ParsedFile()).imports:
                if imported.resolved and not self.files[imported.resolved].is_test:
                    self.tests_by_source[imported.resolved].add(path)
            # Plus the naming convention, which holds even when imports are indirect.
            for source in self._conventional_targets(path):
                self.tests_by_source[source].add(path)

    def _conventional_targets(self, test_path: str) -> list[str]:
        name = test_path.rsplit("/", 1)[-1]
        stem = name
        for prefix in ("test_",):
            stem = stem.removeprefix(prefix)
        for suffix in ("_test.py", ".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.js", "_test.go"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        else:
            stem = stem.rsplit(".", 1)[0]

        if not stem:
            return []
        return [
            path
            for path, record in self.files.items()
            if not record.is_test and path.rsplit("/", 1)[-1].rsplit(".", 1)[0] == stem
        ]

    # -- queries ------------------------------------------------------------------

    def file_exists(self, path: str) -> bool:
        return self.normalize(path) in self.files

    def normalize(self, path: str) -> str:
        """Accept absolute paths, ./-prefixed paths and repo-relative paths alike."""
        candidate = Path(path)
        if candidate.is_absolute():
            try:
                return str(candidate.resolve().relative_to(self.root).as_posix())
            except ValueError:
                return path
        return str(Path(path).as_posix()).removeprefix("./")

    def find_symbol(self, name: str) -> list[SymbolRecord]:
        """Match on bare name or fully-qualified name."""
        if name in self.symbols_by_qualname:
            return [self.symbols_by_qualname[name]]
        results = list(self.symbols_by_name.get(name, ()))
        if results:
            return results
        if "." in name:
            owner, _, attribute = name.rpartition(".")
            return [
                symbol
                for symbol in self.symbols_by_name.get(attribute, ())
                if symbol.parent and symbol.parent.rsplit(".", 1)[-1] == owner.rsplit(".", 1)[-1]
            ]
        return []

    def has_symbol(self, name: str) -> bool:
        return bool(self.find_symbol(name))

    def attributes_of(self, owner: str) -> set[str]:
        bare = owner.rsplit(".", 1)[-1]
        return set(self.attributes_by_owner.get(owner, ())) | set(self.attributes_by_owner.get(bare, ()))

    def knows_type(self, name: str) -> bool:
        """Whether we have any symbol evidence about this type at all.

        The Evidence Engine must only challenge `X.y` when it actually knows `X` —
        otherwise it is reasoning from ignorance (see the module docstring, rule 1).
        """
        bare = name.rsplit(".", 1)[-1]
        return any(
            symbol.kind in ("class", "struct", "interface", "type", "enum", "module")
            for symbol in self.symbols_by_name.get(bare, ())
        )

    def resolve_module(self, module: str) -> str | None:
        return self.module_map.get(module)

    def search_names(self, fragment: str, limit: int = 10) -> list[SymbolRecord]:
        """Symbols whose name contains `fragment`, case-insensitively.

        Answers "does this repository already do X?" — the question behind SPEC §12's
        "Can existing architecture solve it?" branch and SPEC §33's existing-pagination-
        utility challenge. Exported symbols rank above private ones.
        """
        needle = fragment.lower()
        if len(needle) < 3:
            return []
        matches = [
            symbol
            for name, records in self.symbols_by_name.items()
            if needle in name.lower()
            for symbol in records
        ]
        matches.sort(key=lambda s: (s.is_private, len(s.name), s.path))
        return matches[:limit]

    def search_files(self, fragment: str, limit: int = 10) -> list[str]:
        """Files whose *name* contains `fragment` (not the directory part)."""
        needle = fragment.lower()
        if len(needle) < 3:
            return []
        matches = [
            path
            for path in self.files
            if needle in path.rsplit("/", 1)[-1].lower() and not self.files[path].is_test
        ]
        return sorted(matches)[:limit]

    def is_declared_dependency(self, package: str) -> bool:
        return self.dependencies.declares(package)

    def dependents_of(self, path: str) -> set[str]:
        return set(self.dependents.get(self.normalize(path), ()))

    def blast_radius(self, path: str, depth: int = 2) -> set[str]:
        """Transitive reverse-import closure — SPEC §12's blast-radius signal."""
        start = self.normalize(path)
        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        while queue:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for dependent in self.dependents.get(current, ()):
                if dependent not in seen:
                    seen.add(dependent)
                    queue.append((dependent, level + 1))
        seen.discard(start)
        return seen

    def tests_for(self, path: str) -> set[str]:
        return set(self.tests_by_source.get(self.normalize(path), ()))

    def has_tests(self, path: str) -> bool:
        return bool(self.tests_for(path))

    def imports_of(self, path: str) -> list[ImportRecord]:
        return list(self.parsed.get(self.normalize(path), ParsedFile()).imports)

    def symbols_in(self, path: str) -> list[SymbolRecord]:
        return list(self.parsed.get(self.normalize(path), ParsedFile()).symbols)

    def is_parsed(self, path: str) -> bool:
        """Whether we have real symbol evidence for this file."""
        return self.parsed.get(self.normalize(path), ParsedFile()).parsed

    @property
    def git(self) -> GitState:
        return self._git.get()

    # -- summary ------------------------------------------------------------------

    def summary(self) -> dict[str, object]:
        by_lang: dict[str, int] = defaultdict(int)
        for record in self.files.values():
            by_lang[record.lang] += 1
        return {
            "root": str(self.root),
            "files": len(self.files),
            "parsed_files": sum(1 for p in self.parsed.values() if p.parsed),
            "symbols": len(self.symbols_by_qualname),
            "tested_sources": len(self.tests_by_source),
            "test_files": sum(1 for r in self.files.values() if r.is_test),
            "config_files": sum(1 for r in self.files.values() if r.is_config),
            "dependencies": len(self.dependencies.all_names),
            "manifests": self.dependencies.manifests,
            "languages": dict(sorted(by_lang.items(), key=lambda kv: -kv[1])),
            "build_ms": round(self.build_ms, 1),
        }

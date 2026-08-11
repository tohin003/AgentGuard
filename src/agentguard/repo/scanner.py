"""File discovery (SPEC §32, first stage of the evidence base).

Uses `git ls-files` where available: it is one subprocess call, it already honours
.gitignore and nested ignore files, and it is dramatically faster than walking a tree
that contains node_modules. Falls back to a filtered walk outside git repositories.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agentguard.core.config import IndexSettings
from agentguard.repo.models import (
    CONFIG_DIRS,
    CONFIG_FILENAMES,
    CONFIG_SUFFIXES,
    LANGUAGE_BY_EXT,
    FileRecord,
)

TEST_DIR_NAMES: frozenset[str] = frozenset({"test", "tests", "__tests__", "spec", "specs", "e2e"})
TEST_FILE_PREFIXES: tuple[str, ...] = ("test_",)
TEST_FILE_SUFFIXES: tuple[str, ...] = (
    "_test.py", "_test.go", ".test.js", ".test.ts", ".test.tsx", ".test.jsx",
    ".spec.js", ".spec.ts", ".spec.tsx", ".spec.jsx", "_spec.rb", "Test.java",
)


def detect_language(path: str) -> str:
    return LANGUAGE_BY_EXT.get(Path(path).suffix.lower(), "unknown")


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if any(part in TEST_DIR_NAMES for part in parts[:-1]):
        return True
    if name.startswith(TEST_FILE_PREFIXES):
        return True
    return name.endswith(TEST_FILE_SUFFIXES)


def is_config_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if name in CONFIG_FILENAMES:
        return True
    if any(part in CONFIG_DIRS for part in parts[:-1]):
        return True
    # A bare .yaml at the root is config; one inside src/ is probably a fixture.
    return name.endswith(CONFIG_SUFFIXES) and len(parts) <= 2


def _git_listed_files(root: Path) -> list[str] | None:
    """One call, gitignore-aware. Returns None outside a git repo or on any failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]


def _walked_files(root: Path, settings: IndexSettings) -> list[str]:
    excluded = set(settings.exclude_dirs)
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in excluded and not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, root)
        prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/"
        for name in filenames:
            if name.startswith("."):
                continue
            out.append(prefix + name)
            if len(out) >= settings.max_files:
                return out
    return out


def scan(root: Path, settings: IndexSettings | None = None) -> dict[str, FileRecord]:
    """Return every indexable file, keyed by repo-relative posix path."""
    settings = settings or IndexSettings()
    root = root.resolve()

    paths = _git_listed_files(root)
    from_git = paths is not None
    if paths is None:
        paths = _walked_files(root, settings)

    excluded = set(settings.exclude_dirs)
    records: dict[str, FileRecord] = {}

    for rel in paths:
        rel = rel.replace(os.sep, "/")
        parts = rel.split("/")
        # `git ls-files` respects .gitignore but not our own exclusions (a vendored
        # node_modules can be committed), so filter regardless of source.
        if from_git and any(part in excluded for part in parts[:-1]):
            continue

        full = root / rel
        try:
            st = full.stat()
        except OSError:
            continue  # deleted between listing and stat, or a broken symlink
        if not os.path.isfile(full):
            continue
        if st.st_size > settings.max_file_bytes:
            continue

        records[rel] = FileRecord(
            path=rel,
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            lang=detect_language(rel),
            is_test=is_test_path(rel),
            is_config=is_config_path(rel),
        )
        if len(records) >= settings.max_files:
            break

    return records

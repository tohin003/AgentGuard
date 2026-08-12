"""Declared dependencies, read from package manifests (SPEC §14, §32).

This is the evidence behind the §16 DEPENDENCY challenge — "Is a new dependency actually
necessary?" — and behind not flagging `import react` as a hallucination in a repo whose
package.json declares react.

The readers take **text**, not a path. That is what lets the same parser answer both
questions AgentGuard needs to ask: what does this repository declare today, and what would
it declare if this proposed edit were applied? A second, edit-shaped implementation of
manifest parsing would drift from this one, and the two answers have to be comparable for
a diff between them to mean anything (SPEC §3, "introduce unnecessary dependencies").
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

from agentguard.repo.models import DependencyInfo

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(.*)$")
_GO_REQUIRE = re.compile(r"^\s*([\w./-]+)\s+(v[\w.\-+]+)")


def _strip_extras(spec: str) -> tuple[str, str]:
    """'fastapi[all]>=0.115' -> ('fastapi', '>=0.115')"""
    name = re.split(r"[<>=!~;\[\s]", spec, maxsplit=1)[0].strip()
    version = spec[len(name) :].strip()
    return name, version


def _read_pyproject(text: str, name: str, info: DependencyInfo) -> None:
    data = tomllib.loads(text)

    project = data.get("project", {})
    for spec in project.get("dependencies", []) or []:
        dep, version = _strip_extras(str(spec))
        if dep:
            info.runtime[dep] = version
    for group in (project.get("optional-dependencies", {}) or {}).values():
        for spec in group or []:
            dep, version = _strip_extras(str(spec))
            if dep:
                info.runtime[dep] = version

    for group in (data.get("dependency-groups", {}) or {}).values():
        for spec in group or []:
            if isinstance(spec, str):
                dep, version = _strip_extras(spec)
                if dep:
                    info.dev[dep] = version

    # Poetry keeps its own tree.
    poetry = data.get("tool", {}).get("poetry", {})
    for dep, version in (poetry.get("dependencies", {}) or {}).items():
        if dep.lower() != "python":
            info.runtime[dep] = str(version)
    for dep, version in (poetry.get("dev-dependencies", {}) or {}).items():
        info.dev[dep] = str(version)
    for group in (poetry.get("group", {}) or {}).values():
        for dep, version in (group.get("dependencies", {}) or {}).items():
            info.dev[dep] = str(version)


def _read_requirements(text: str, name: str, info: DependencyInfo) -> None:
    target = info.dev if "dev" in name.lower() or "test" in name.lower() else info.runtime
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        dep, version = _strip_extras(line)
        if dep and _REQ_LINE.match(dep):
            target[dep] = version


def _read_package_json(text: str, name: str, info: DependencyInfo) -> None:
    data = json.loads(text)
    for dep, version in (data.get("dependencies") or {}).items():
        info.runtime[dep] = str(version)
    for key in ("devDependencies", "peerDependencies", "optionalDependencies"):
        for dep, version in (data.get(key) or {}).items():
            info.dev[dep] = str(version)


def _read_go_mod(text: str, name: str, info: DependencyInfo) -> None:
    for line in text.splitlines():
        match = _GO_REQUIRE.match(line.replace("require ", ""))
        if match:
            info.runtime[match.group(1)] = match.group(2)


def _read_cargo(text: str, name: str, info: DependencyInfo) -> None:
    data = tomllib.loads(text)
    for dep, spec in (data.get("dependencies") or {}).items():
        info.runtime[dep] = spec if isinstance(spec, str) else str(spec.get("version", ""))
    for dep, spec in (data.get("dev-dependencies") or {}).items():
        info.dev[dep] = spec if isinstance(spec, str) else str(spec.get("version", ""))


_READERS: dict[str, Callable[[str, str, DependencyInfo], None]] = {
    "pyproject.toml": _read_pyproject,
    "package.json": _read_package_json,
    "go.mod": _read_go_mod,
    "Cargo.toml": _read_cargo,
}


def reader_for(filename: str) -> Callable[[str, str, DependencyInfo], None] | None:
    """The parser for a manifest filename, or None if it is not one."""
    reader = _READERS.get(filename)
    if reader is None and filename.startswith("requirements") and filename.endswith(".txt"):
        reader = _read_requirements
    return reader


def is_manifest(filename: str) -> bool:
    return reader_for(filename) is not None


def parse_text(filename: str, text: str) -> DependencyInfo | None:
    """Dependencies declared by one manifest's contents.

    `None` means the text could not be parsed as that manifest — which is different from
    an empty manifest, and the difference matters: only a clean parse licenses the claim
    that a package is *absent*. Empty text is treated as a valid empty manifest, because
    a file being created genuinely has no prior dependencies.
    """
    reader = reader_for(filename)
    if reader is None:
        return None
    info = DependencyInfo(manifests=[filename])
    if not text.strip():
        return info
    try:
        reader(text, filename, info)
    except Exception:  # noqa: BLE001 - a manifest mid-edit is routinely invalid
        return None
    return info


def read_dependencies(root: Path, files: dict[str, object]) -> DependencyInfo:
    """Read every manifest present. A malformed manifest is skipped, never fatal."""
    info = DependencyInfo()

    for rel in files:
        name = rel.rsplit("/", 1)[-1]
        reader = reader_for(name)
        if reader is None:
            continue
        # Only top-level and one-deep manifests; a vendored package.json 6 levels down
        # describes someone else's project, not this one.
        if rel.count("/") > 2:
            continue
        try:
            reader((root / rel).read_text(encoding="utf-8"), name, info)
            info.manifests.append(rel)
        except Exception:  # noqa: BLE001 - a broken manifest must not stop indexing
            continue

    return info

"""Declared dependencies, read from package manifests (SPEC §14, §32).

This is the evidence behind the §16 DEPENDENCY challenge — "Is a new dependency actually
necessary?" — and behind not flagging `import react` as a hallucination in a repo whose
package.json declares react.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from agentguard.repo.models import DependencyInfo

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(.*)$")
_GO_REQUIRE = re.compile(r"^\s*([\w./-]+)\s+(v[\w.\-+]+)")


def _strip_extras(spec: str) -> tuple[str, str]:
    """'fastapi[all]>=0.115' -> ('fastapi', '>=0.115')"""
    name = re.split(r"[<>=!~;\[\s]", spec, maxsplit=1)[0].strip()
    version = spec[len(name) :].strip()
    return name, version


def _read_pyproject(path: Path, info: DependencyInfo) -> None:
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    project = data.get("project", {})
    for spec in project.get("dependencies", []) or []:
        name, version = _strip_extras(str(spec))
        if name:
            info.runtime[name] = version
    for group in (project.get("optional-dependencies", {}) or {}).values():
        for spec in group or []:
            name, version = _strip_extras(str(spec))
            if name:
                info.runtime[name] = version

    for group in (data.get("dependency-groups", {}) or {}).values():
        for spec in group or []:
            if isinstance(spec, str):
                name, version = _strip_extras(spec)
                if name:
                    info.dev[name] = version

    # Poetry keeps its own tree.
    poetry = data.get("tool", {}).get("poetry", {})
    for name, version in (poetry.get("dependencies", {}) or {}).items():
        if name.lower() != "python":
            info.runtime[name] = str(version)
    for name, version in (poetry.get("dev-dependencies", {}) or {}).items():
        info.dev[name] = str(version)
    for group in (poetry.get("group", {}) or {}).values():
        for name, version in (group.get("dependencies", {}) or {}).items():
            info.dev[name] = str(version)


def _read_requirements(path: Path, info: DependencyInfo) -> None:
    target = info.dev if "dev" in path.name.lower() or "test" in path.name.lower() else info.runtime
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name, version = _strip_extras(line)
        if name and _REQ_LINE.match(name):
            target[name] = version


def _read_package_json(path: Path, info: DependencyInfo) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for name, version in (data.get("dependencies") or {}).items():
        info.runtime[name] = str(version)
    for key in ("devDependencies", "peerDependencies", "optionalDependencies"):
        for name, version in (data.get(key) or {}).items():
            info.dev[name] = str(version)


def _read_go_mod(path: Path, info: DependencyInfo) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _GO_REQUIRE.match(line.replace("require ", ""))
        if match:
            info.runtime[match.group(1)] = match.group(2)


def _read_cargo(path: Path, info: DependencyInfo) -> None:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for name, spec in (data.get("dependencies") or {}).items():
        info.runtime[name] = spec if isinstance(spec, str) else str(spec.get("version", ""))
    for name, spec in (data.get("dev-dependencies") or {}).items():
        info.dev[name] = spec if isinstance(spec, str) else str(spec.get("version", ""))


_READERS = {
    "pyproject.toml": _read_pyproject,
    "package.json": _read_package_json,
    "go.mod": _read_go_mod,
    "Cargo.toml": _read_cargo,
}


def read_dependencies(root: Path, files: dict[str, object]) -> DependencyInfo:
    """Read every manifest present. A malformed manifest is skipped, never fatal."""
    info = DependencyInfo()

    for rel in files:
        name = rel.rsplit("/", 1)[-1]
        reader = _READERS.get(name)
        if reader is None and name.startswith("requirements") and name.endswith(".txt"):
            reader = _read_requirements
        if reader is None:
            continue
        # Only top-level and one-deep manifests; a vendored package.json 6 levels down
        # describes someone else's project, not this one.
        if rel.count("/") > 2:
            continue
        try:
            reader(root / rel, info)
            info.manifests.append(rel)
        except Exception:  # noqa: BLE001 - a broken manifest must not stop indexing
            continue

    return info

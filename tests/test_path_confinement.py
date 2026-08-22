"""Regression tests for repository path confinement.

Hook payload paths are untrusted input.  They must never make the evidence layer read or
index a file outside the workspace, including through a symlinked directory or file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentguard.core.enums import EventType
from agentguard.core.events import AgentEvent
from agentguard.evidence import extractors
from agentguard.repo import RepoIndex


def _edit(root: Path, path: str) -> AgentEvent:
    return AgentEvent(
        event=EventType.PRE_TOOL_USE,
        agent="claude-code",
        workspace=str(root),
        session_id="path-test",
        tool="Edit",
        arguments={"file_path": path, "old_string": "secret", "new_string": "changed"},
    )


def test_absolute_outside_path_is_not_normalized_or_indexed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "secret.py"
    outside.write_text("secret = 1\n", encoding="utf-8")

    index = RepoIndex(root).build()

    assert index.normalize(str(outside)) == ""
    assert index.refresh_path(str(outside)) is False
    assert str(outside) not in index.files
    assert extractors.resolve_edit(_edit(root, str(outside)), root) is None


def test_initial_scan_skips_symlinked_file_and_directory_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("secret = 1\n", encoding="utf-8")

    try:
        (root / "linked.py").symlink_to(outside / "secret.py")
        (root / "linked-dir").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    index = RepoIndex(root).build()

    assert "linked.py" not in index.files
    assert "linked-dir/secret.py" not in index.files


def test_internal_symlink_keeps_its_repository_relative_name(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target.py"
    target.write_text("value = 1\n", encoding="utf-8")
    link = root / "linked.py"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    index = RepoIndex(root).build()

    assert index.normalize("linked.py") == "linked.py"
    assert "linked.py" in index.files
    assert index.refresh_path("linked.py") is False


def test_relative_path_through_symlink_to_outside_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.py"
    target.write_text("secret = 1\n", encoding="utf-8")

    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    index = RepoIndex(root).build()

    assert index.normalize("linked/secret.py") == ""
    assert "linked/secret.py" not in index.files
    assert index.refresh_path("linked/secret.py") is False
    assert "linked/secret.py" not in index.files
    assert extractors.resolve_edit(_edit(root, "linked/secret.py"), root) is None


def test_symlink_file_to_outside_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "secret.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    link = root / "secret.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    index = RepoIndex(root).build()

    assert index.normalize("secret.py") == ""
    assert "secret.py" not in index.files
    assert index.refresh_path("secret.py") is False
    assert extractors.resolve_edit(_edit(root, "secret.py"), root) is None

"""Git facts as evidence (SPEC §14, §32).

Read-only, via subprocess. The SPEC lists GitPython, but every query needed here is a
single plumbing command, and shelling out avoids a dependency plus a large import on the
daemon's startup path.

Git state changes constantly during a coding session (every edit dirties a file), so it is
cached with a short TTL rather than held for the life of the index.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from agentguard.repo.models import GitState

TTL_SECONDS = 2.0
_CHURN_COMMITS = 200
_RECENT_COMMITS = 25


def _git(root: Path, *args: str, timeout: float = 10.0) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def is_git_repo(root: Path) -> bool:
    return _git(root, "rev-parse", "--git-dir") is not None


def read_state(root: Path) -> GitState:
    state = GitState()
    if _git(root, "rev-parse", "--git-dir") is None:
        return state
    state.is_repo = True

    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    state.branch = branch.strip() if branch else ""

    head = _git(root, "rev-parse", "HEAD")
    state.head = head.strip() if head else ""

    status = _git(root, "status", "--porcelain=v1", "-z")
    if status:
        for entry in status.split("\0"):
            if len(entry) < 4:
                continue
            code, path = entry[:2], entry[3:]
            if code == "??":
                state.untracked.add(path)
            else:
                state.dirty.add(path)

    log = _git(root, "log", f"-{_RECENT_COMMITS}", "--format=%H%x1f%ct%x1f%s")
    if log:
        for line in log.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                try:
                    state.recent_commits.append((parts[0], int(parts[1]), parts[2]))
                except ValueError:
                    continue

    # Churn: how often each file has changed recently. A file that changes constantly is
    # a different kind of risk from one untouched for two years (SPEC §12 risk signals).
    names = _git(root, "log", f"-{_CHURN_COMMITS}", "--name-only", "--format=")
    if names:
        for line in names.splitlines():
            path = line.strip()
            if path:
                state.churn[path] = state.churn.get(path, 0) + 1

    return state


class GitCache:
    """Short-TTL cache; git state is queried on nearly every decision."""

    def __init__(self, root: Path, ttl: float = TTL_SECONDS) -> None:
        self.root = root
        self.ttl = ttl
        self._state: GitState | None = None
        self._fetched_at = 0.0

    def get(self, force: bool = False) -> GitState:
        now = time.monotonic()
        if force or self._state is None or (now - self._fetched_at) > self.ttl:
            self._state = read_state(self.root)
            self._fetched_at = now
        return self._state


def changed_files(root: Path, base: str = "HEAD") -> set[str]:
    """Files changed against a ref — the diff the Completion Gate reasons about (§19)."""
    out = _git(root, "diff", "--name-only", base)
    files = {line.strip() for line in (out or "").splitlines() if line.strip()}
    staged = _git(root, "diff", "--name-only", "--cached", base)
    files |= {line.strip() for line in (staged or "").splitlines() if line.strip()}
    return files

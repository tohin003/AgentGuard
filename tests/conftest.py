"""Shared fixtures.

Every test runs against an isolated ``AGENTGUARD_HOME`` so the suite can never touch the
developer's real daemon, token or config.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentguard.core.config import Settings
from agentguard.core.engine import Guard

REPO_ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh AGENTGUARD_HOME per test.

    Since Phase 3.5 there is one shared database per home directory, cached in a
    process-wide registry — so the cache has to be dropped when the home moves, or a test
    would keep writing into the previous test's database.
    """
    from agentguard.core.store import Database

    home = tmp_path / "agentguard-home"
    home.mkdir(parents=True, exist_ok=True)
    # Give every test its own port. Otherwise a daemon spawned without `--port` picks the
    # configured default (8787) and collides with the developer's real daemon, or with a
    # sibling test — making the suite depend on what else is running on the machine.
    home.joinpath("config.toml").write_text(f"[daemon]\nport = {free_port()}\n", encoding="utf-8")
    monkeypatch.setenv("AGENTGUARD_HOME", str(home))
    monkeypatch.delenv("AGENTGUARD_DISABLE", raising=False)
    Database.reset_shared()
    yield home
    Database.reset_shared()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def guard(workspace: Path) -> Iterator[Guard]:
    g = Guard(Settings())
    yield g
    g.close()


class DaemonProcess:
    """A real daemon subprocess, for tests that need the actual transport."""

    def __init__(self, home: Path, port: int) -> None:
        self.home = home
        self.port = port
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def token(self) -> str:
        return json.loads((self.home / "daemon.json").read_text())["token"]

    def start(self, timeout: float = 20.0) -> None:
        env = dict(os.environ, AGENTGUARD_HOME=str(self.home), PYTHONPATH=str(REPO_ROOT / "src"))
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "agentguard.daemon", "run", "--port", str(self.port)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                out = self.proc.stdout.read().decode() if self.proc.stdout else ""
                raise RuntimeError(f"daemon exited early:\n{out}")
            if (self.home / "daemon.json").exists():
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.2)
                    if s.connect_ex(("127.0.0.1", self.port)) == 0:
                        return
            time.sleep(0.05)
        raise TimeoutError("daemon did not start in time")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)

    def kill(self) -> None:
        """Hard kill, for testing fail-open behaviour."""
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)


@pytest.fixture
def daemon(isolated_home: Path) -> Iterator[DaemonProcess]:
    d = DaemonProcess(isolated_home, free_port())
    d.start()
    yield d
    d.stop()


# ---------------------------------------------------------------- payload builders


def pre_tool_use(tool: str = "Edit", cwd: str = "/tmp/ws", **tool_input) -> dict:
    return {
        "session_id": "sess-1",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input or {"file_path": "/tmp/ws/a.py", "old_string": "x", "new_string": "y"},
        "tool_use_id": "toolu_test",
    }


def user_prompt_submit(text: str, cwd: str = "/tmp/ws") -> dict:
    return {
        "session_id": "sess-1",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "permission_mode": "default",
        "hook_event_name": "UserPromptSubmit",
        # `prompt`, not the documented `prompt_text` — see tests/fixtures/hook_payloads/.
        "prompt": text,
    }


def stop_event(message: str = "Done.", cwd: str = "/tmp/ws", stop_hook_active: bool = False) -> dict:
    return {
        "session_id": "sess-1",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "permission_mode": "default",
        "hook_event_name": "Stop",
        "last_assistant_message": message,
        "stop_hook_active": stop_hook_active,
    }


def session_start(cwd: str = "/tmp/ws", source: str = "startup") -> dict:
    return {
        "session_id": "sess-1",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "permission_mode": "default",
        "hook_event_name": "SessionStart",
        "source": source,
    }

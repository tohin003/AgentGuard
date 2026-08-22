"""Daemon transport and fail-open behaviour (SPEC §30, §39).

The most important property in this file is negative: **there is no way to make
AgentGuard impede the agent.** Bad auth, bad JSON, a crashed handler, a dead daemon — all
of them must produce "no decision" rather than an error the host has to deal with.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from agentguard.core.config import DaemonSettings, Settings, get_or_create_token
from agentguard.core.engine import Guard
from agentguard.daemon.app import create_app
from tests.conftest import REPO_ROOT, pre_tool_use, session_start, stop_event, user_prompt_submit

TOKEN = "test-token"


@pytest.fixture
def client(workspace):
    app = create_app(TOKEN, Settings(), Guard(Settings()))
    with TestClient(app) as c:
        yield c


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


class TestEndpoints:
    def test_health_needs_no_auth(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_metrics_needs_auth(self, client):
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers=auth()).status_code == 200

    def test_hook_round_trip(self, client, workspace):
        resp = client.post("/hook/claude-code", json=pre_tool_use(cwd=str(workspace)), headers=auth())
        assert resp.status_code == 200
        assert resp.json() == {}  # ALLOW is silent

    @pytest.mark.parametrize(
        "payload_fn", [pre_tool_use, user_prompt_submit, stop_event, session_start]
    )
    def test_every_event_type_round_trips(self, client, workspace, payload_fn):
        if payload_fn is user_prompt_submit:
            payload = payload_fn("x", cwd=str(workspace))
        else:
            payload = payload_fn(cwd=str(workspace))
        resp = client.post("/hook/claude-code", json=payload, headers=auth())
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_events_are_persisted(self, client, workspace):
        from agentguard.core.store import Store

        prompt = user_prompt_submit("Add pagination", cwd=str(workspace))
        client.post("/hook/claude-code", json=prompt, headers=auth())
        client.post("/hook/claude-code", json=pre_tool_use(cwd=str(workspace)), headers=auth())

        store = Store.for_workspace(workspace)
        assert len(store.recent_decisions()) == 2


class TestFailOpen:
    """Every one of these returns 200 + {} — "AgentGuard has no opinion"."""

    def test_bad_token(self, client, workspace):
        resp = client.post(
            "/hook/claude-code",
            json=pre_tool_use(cwd=str(workspace)),
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_no_token(self, client, workspace):
        resp = client.post("/hook/claude-code", json=pre_tool_use(cwd=str(workspace)))
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_malformed_body(self, client):
        resp = client.post(
            "/hook/claude-code",
            content=b"{not json",
            headers={**auth(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_non_object_body(self, client):
        resp = client.post("/hook/claude-code", json=["a", "list"], headers=auth())
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_unknown_adapter(self, client, workspace):
        resp = client.post("/hook/nonexistent", json=pre_tool_use(cwd=str(workspace)), headers=auth())
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_unknown_hook_event(self, client, workspace):
        resp = client.post(
            "/hook/claude-code",
            json={"hook_event_name": "PreCompact", "cwd": str(workspace)},
            headers=auth(),
        )
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_a_crashing_handler_still_allows(self, workspace, monkeypatch):
        """An exception anywhere in the engine must degrade to ALLOW, not to a 500."""

        def explode(self, event):
            raise RuntimeError("engine on fire")

        guard = Guard(Settings())
        monkeypatch.setattr(Guard, "handle", explode)
        app = create_app(TOKEN, Settings(), guard)
        with TestClient(app) as c:
            resp = c.post("/hook/claude-code", json=pre_tool_use(cwd=str(workspace)), headers=auth())
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_a_crashing_internal_handler_is_caught_by_the_guard(self, workspace, monkeypatch):
        """Guard.handle() itself is the last line of defence and never raises."""
        from agentguard.adapters.claude_code import translate as claude

        guard = Guard(Settings())
        monkeypatch.setattr(
            Guard, "_on_pre_tool_use", lambda self, e, ws: (_ for _ in ()).throw(ValueError("boom"))
        )
        event = claude.to_event(pre_tool_use(cwd=str(workspace)))
        decision = guard.handle(event)
        assert decision.action == "allow"

    def test_kill_switch_short_circuits(self, workspace, monkeypatch):
        from agentguard.adapters.claude_code import translate as claude

        monkeypatch.setenv("AGENTGUARD_DISABLE", "1")
        guard = Guard(Settings.load())
        decision = guard.handle(claude.to_event(pre_tool_use(cwd=str(workspace))))
        assert decision.is_silent


class TestDaemonProcess:
    """Tests against a real subprocess and a real socket."""

    def test_handshake_file_is_written_and_private(self, daemon):
        path = daemon.home / "daemon.json"
        assert path.exists()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600, f"handshake carries a credential; mode was {oct(mode)}"
        data = json.loads(path.read_text())
        assert data["port"] == daemon.port
        assert data["token"]

    def test_token_file_is_private(self, isolated_home):
        path = isolated_home / "token"
        get_or_create_token()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_daemon_settings_reject_non_loopback_hosts(self):
        assert DaemonSettings(host="127.0.0.1").host == "127.0.0.1"
        assert DaemonSettings(host="localhost").host == "127.0.0.1"
        with pytest.raises(ValueError, match="loopback"):
            DaemonSettings(host="0.0.0.0")
        with pytest.raises(ValueError, match="loopback"):
            DaemonSettings(host="192.168.1.10")

    def test_serves_real_requests(self, daemon, workspace):
        resp = httpx.get(f"{daemon.url}/health", timeout=5)
        assert resp.json()["status"] == "ok"

        resp = httpx.post(
            f"{daemon.url}/hook/claude-code",
            json=pre_tool_use(cwd=str(workspace)),
            headers={"Authorization": f"Bearer {daemon.token}"},
            timeout=5,
        )
        assert resp.status_code == 200

    def test_handshake_is_cleared_on_clean_shutdown(self, daemon):
        daemon.stop()
        assert not (daemon.home / "daemon.json").exists()


class TestShim:
    """The command-hook fallback (SPEC §26)."""

    def run_shim(self, payload: dict, home, extra_args=()) -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            AGENTGUARD_HOME=str(home),
            PYTHONPATH=str(REPO_ROOT / "src"),
        )
        return subprocess.run(
            [sys.executable, "-m", "agentguard.adapters.claude_code.shim", *extra_args],
            input=json.dumps(payload).encode(),
            capture_output=True,
            env=env,
            timeout=30,
        )

    def test_forwards_to_a_live_daemon(self, daemon, workspace):
        result = self.run_shim(pre_tool_use(cwd=str(workspace)), daemon.home)
        assert result.returncode == 0

    def test_fails_open_when_no_daemon_exists(self, isolated_home, workspace):
        result = self.run_shim(pre_tool_use(cwd=str(workspace)), isolated_home)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_fails_open_when_the_daemon_was_killed(self, daemon, workspace):
        """The handshake file still exists but the process is gone — the common case
        after a crash or a reboot."""
        daemon.kill()
        assert (daemon.home / "daemon.json").exists()  # stale handshake left behind
        result = self.run_shim(pre_tool_use(cwd=str(workspace)), daemon.home)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_fails_open_on_garbage_stdin(self, isolated_home):
        env = dict(os.environ, AGENTGUARD_HOME=str(isolated_home), PYTHONPATH=str(REPO_ROOT / "src"))
        result = subprocess.run(
            [sys.executable, "-m", "agentguard.adapters.claude_code.shim"],
            input=b"not json at all",
            capture_output=True,
            env=env,
            timeout=30,
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_respects_the_kill_switch(self, daemon, workspace):
        env = dict(
            os.environ,
            AGENTGUARD_HOME=str(daemon.home),
            PYTHONPATH=str(REPO_ROOT / "src"),
            AGENTGUARD_DISABLE="1",
        )
        result = subprocess.run(
            [sys.executable, "-m", "agentguard.adapters.claude_code.shim"],
            input=json.dumps(pre_tool_use(cwd=str(workspace))).encode(),
            capture_output=True,
            env=env,
            timeout=30,
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_ensure_daemon_starts_one(self, isolated_home, workspace):
        """SessionStart uses this to guarantee the daemon is warm."""
        result = self.run_shim(session_start(cwd=str(workspace)), isolated_home, ["--ensure-daemon"])
        assert result.returncode == 0
        handshake = isolated_home / "daemon.json"
        assert handshake.exists(), result.stderr.decode()

        data = json.loads(handshake.read_text())
        try:
            assert httpx.get(f"http://{data['host']}:{data['port']}/health", timeout=5).status_code == 200
        finally:
            os.kill(data["pid"], 15)

"""End-to-end: install the hooks, then drive the daemon exactly as Claude Code would.

Every other test builds its own request. This one reads the URL, headers and timeouts out
of the **installed settings.json** and uses them verbatim, so it fails if the installer and
the daemon ever disagree about the wire — the one bug that would silently disable
AgentGuard in production while every unit test stayed green.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import httpx
import pytest

from agentguard.adapters.claude_code import install as inst
from agentguard.core.config import DaemonSettings, Settings
from agentguard.core.store import Store
from tests.conftest import REPO_ROOT, free_port, pre_tool_use, session_start, stop_event, user_prompt_submit


@pytest.fixture
def installed(tmp_path, isolated_home):
    """Write a real settings.json pointed at a real daemon, and start that daemon."""
    port = free_port()
    settings = Settings(daemon=DaemonSettings(host="127.0.0.1", port=port))
    settings_file = tmp_path / ".claude" / "settings.json"
    inst.install(settings_file, settings=settings)

    env = dict(os.environ, AGENTGUARD_HOME=str(isolated_home), PYTHONPATH=str(REPO_ROOT / "src"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentguard.daemon", "run", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        proc.kill()
        raise TimeoutError("daemon never became healthy")

    yield json.loads(settings_file.read_text()), proc, port

    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=5)


def hook_for(config: dict, event: str) -> dict:
    return config["hooks"][event][0]["hooks"][0]


def fire(hook: dict, payload: dict) -> httpx.Response:
    """Do what Claude Code does with an `http` hook entry."""
    return httpx.post(
        hook["url"],
        json=payload,
        headers=hook["headers"],
        timeout=hook["timeout"],
    )


class TestInstalledWiring:
    def test_a_full_session_flows_through_the_installed_hooks(self, installed, workspace):
        config, _proc, _port = installed
        ws = str(workspace)

        exchanges = [
            ("UserPromptSubmit", user_prompt_submit("Add pagination to /users", cwd=ws)),
            ("PreToolUse", pre_tool_use("Edit", cwd=ws, file_path="api.py", old_string="a", new_string="b")),
            (
                "PostToolUse",
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "sess-1",
                    "cwd": ws,
                    "tool_name": "Edit",
                    "tool_input": {"file_path": "api.py"},
                    "tool_output": "ok",
                },
            ),
            ("Stop", stop_event(cwd=ws)),
            ("SessionEnd", {"hook_event_name": "SessionEnd", "session_id": "sess-1", "cwd": ws}),
        ]

        for event_name, payload in exchanges:
            resp = fire(hook_for(config, event_name), payload)
            assert resp.status_code == 200, f"{event_name} -> {resp.status_code}"
            body = resp.json()

            if event_name == "UserPromptSubmit":
                # The planning budget rides in here (SPEC §9, §13). It is context for
                # the agent, not output for the developer.
                context = body["hookSpecificOutput"]["additionalContext"]
                assert "[AgentGuard]" in context
                assert "complexity" in context
            else:
                assert body == {}, f"{event_name} must stay silent on an ordinary action"

        store = Store.for_workspace(workspace)
        decisions = store.recent_decisions(50)
        assert len(decisions) == len(exchanges)
        assert {d["action"] for d in decisions} == {"allow"}, "nothing here warrants an objection"

    def test_the_prompt_creates_a_task_that_later_events_attach_to(self, installed, workspace):
        """Session/task lifecycle: tool calls must be attributable to the prompt that
        caused them, or scope tracking (SPEC §18) has nothing to compare against."""
        config, _proc, _port = installed
        ws = str(workspace)

        fire(hook_for(config, "UserPromptSubmit"), user_prompt_submit("Fix login validation", cwd=ws))
        fire(hook_for(config, "PreToolUse"), pre_tool_use("Edit", cwd=ws, file_path="auth.py"))

        store = Store.for_workspace(workspace)
        task_id = store.current_task_id("sess-1")
        assert task_id
        decisions = store.recent_decisions(10)
        assert all(d["task_id"] == task_id for d in decisions), "events must attach to the open task"

    def test_hot_path_is_within_budget_through_the_installed_hook(self, installed, workspace):
        """SPEC §8, measured through the real installed configuration."""
        config, _proc, _port = installed
        hook = hook_for(config, "PreToolUse")
        payload = pre_tool_use("Edit", cwd=str(workspace), file_path="a.py")

        for _ in range(10):
            fire(hook, payload)
        samples = []
        for _ in range(50):
            start = time.perf_counter()
            fire(hook, payload)
            samples.append((time.perf_counter() - start) * 1000)

        samples.sort()
        p95 = samples[int(len(samples) * 0.95) - 1]
        print(f"\n  installed hot path p50={samples[len(samples)//2]:.2f}ms p95={p95:.2f}ms")
        assert p95 < 100.0


class TestFailOpenInProduction:
    def test_a_dead_daemon_does_not_break_the_hook_contract(self, installed, workspace):
        """The scenario that matters most: AgentGuard crashes mid-session.

        Claude Code gets a connection error, which is a non-blocking hook error — the
        tool call proceeds. What must never happen is a response that *denies* something.
        """
        config, proc, _port = installed
        proc.kill()
        proc.wait(timeout=5)

        hook = hook_for(config, "PreToolUse")
        with pytest.raises(httpx.HTTPError):
            fire(hook, pre_tool_use("Edit", cwd=str(workspace)))

    def test_session_start_hook_revives_the_daemon(self, installed, workspace, isolated_home):
        """SessionStart is a command hook precisely so a dead daemon self-heals."""
        config, proc, _port = installed
        proc.kill()
        proc.wait(timeout=5)

        hook = config["hooks"]["SessionStart"][0]["hooks"][0]
        assert hook["type"] == "command"

        env = dict(os.environ, AGENTGUARD_HOME=str(isolated_home), PYTHONPATH=str(REPO_ROOT / "src"))
        result = subprocess.run(
            [hook["command"], *hook["args"]],
            input=json.dumps(session_start(cwd=str(workspace))).encode(),
            capture_output=True,
            env=env,
            timeout=30,
        )
        assert result.returncode == 0

        handshake = json.loads((isolated_home / "daemon.json").read_text())
        try:
            resp = httpx.get(f"http://{handshake['host']}:{handshake['port']}/health", timeout=5)
            assert resp.status_code == 200
        finally:
            os.kill(handshake["pid"], 15)

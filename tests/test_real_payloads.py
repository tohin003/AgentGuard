"""The adapter, tested against payloads captured from a live Claude Code session.

Every other test in this suite feeds the adapter payloads *I* wrote, which means they
only ever test my reading of the documentation. That reading was wrong twice, and both
errors silently disabled an entire phase of work:

* the prompt arrives as ``prompt``; the docs say ``prompt_text``. The Intent Gateway was
  scoring an empty string on every real prompt, so Phase 2 did nothing in production.
* tool results arrive as ``tool_response``, a **dict**; the docs say ``tool_output``, a
  string. The Completion Gate never saw a test result, so Phase 4's central mechanism —
  catching an agent that claims passing tests it did not earn — never fired.

The fixtures in `tests/fixtures/hook_payloads/` are real payloads, captured by installing
a hook that dumps stdin and running `claude -p` against it. Paths and session ids are
scrubbed; the *shape* is untouched. Re-capture them whenever Claude Code changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentguard.adapters.claude_code import translate as claude
from agentguard.core.enums import EventType
from agentguard.verify import runners

PAYLOADS = Path(__file__).parent / "fixtures" / "hook_payloads"


def load(name: str) -> dict:
    return json.loads((PAYLOADS / f"{name}.json").read_text())


class TestCapturedPayloads:
    @pytest.mark.parametrize(
        "name", ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]
    )
    def test_every_captured_event_translates(self, name):
        event = claude.to_event(load(name))
        assert event is not None, f"{name} did not translate"
        assert event.agent == "claude-code"
        assert event.workspace
        assert event.session_id

    def test_the_prompt_is_actually_read(self):
        """The bug: `prompt`, not `prompt_text`. Without this the whole Intent Gateway,
        Complexity Engine and Planning Governor operate on an empty string."""
        event = claude.to_event(load("UserPromptSubmit"))
        assert event.event is EventType.USER_PROMPT
        assert event.prompt_text, "the prompt must not be empty"
        assert "hello.py" in event.prompt_text

    def test_tool_arguments_are_read(self):
        event = claude.to_event(load("PreToolUse"))
        assert event.tool == "Write"
        assert event.arguments, "tool_input must survive translation"
        assert event.arg("file_path")

    def test_tool_results_are_read(self):
        """The other bug: `tool_response`, and it is a dict rather than a string."""
        event = claude.to_event(load("PostToolUse"))
        assert event.result is not None, "tool results must survive translation"
        text = runners.output_text(event.result)
        assert text, "the output text must be recoverable for test parsing"
        assert "test output here" in text

    def test_the_stop_payload_carries_its_loop_breaker(self):
        payload = load("Stop")
        event = claude.to_event(payload)
        assert event.last_assistant_message
        assert "stop_hook_active" in event.raw, "the gate's loop-safety flag must be present"


class TestEndToEndWithRealShapes:
    def test_a_real_prompt_produces_a_real_planning_budget(self, workspace):
        """The regression that matters: a live prompt must reach the Intent Gateway."""
        from agentguard.core.config import Settings
        from agentguard.core.engine import Guard

        payload = {**load("UserPromptSubmit"), "cwd": str(workspace)}
        guard = Guard(Settings())
        try:
            guard.workspace(workspace).index.ready(timeout=30)
            decision = guard.handle(claude.to_event(payload))
            task = guard.workspace(workspace).store.recent_decisions(1)
        finally:
            guard.close()

        assert decision.additional_context, "a real prompt must produce a planning budget"
        assert "[AgentGuard]" in decision.additional_context
        assert "complexity" in decision.additional_context
        assert task

    def test_a_real_bash_result_reaches_the_completion_gate(self, workspace):
        """A live `tool_response` dict must be parseable as test output."""
        payload = {
            **load("PostToolUse"),
            "cwd": str(workspace),
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"},
            "tool_response": {
                "stdout": "F.\n====== 1 failed, 1 passed in 0.2s ======\n",
                "stderr": "",
                "interrupted": False,
            },
        }
        event = claude.to_event(payload)
        outcome = runners.parse_output("pytest -q", runners.output_text(event.result))
        assert outcome.passed is False
        assert outcome.failed == 1

"""Claude Code adapter translation (SPEC §23)."""

from __future__ import annotations

import pytest

from agentguard.adapters.claude_code import translate as claude
from agentguard.core.enums import DecisionAction, EventType
from agentguard.core.models import Decision
from tests.conftest import pre_tool_use, session_start, stop_event, user_prompt_submit


class TestToEvent:
    def test_maps_every_subscribed_hook(self):
        for hook_name, expected in claude.EVENT_MAP.items():
            event = claude.to_event({"hook_event_name": hook_name, "cwd": "/tmp/ws"})
            assert event is not None, hook_name
            assert event.event is expected

    def test_ignores_unsubscribed_hooks(self):
        assert claude.to_event({"hook_event_name": "PreCompact"}) is None
        assert claude.to_event({}) is None

    def test_extracts_tool_call(self):
        event = claude.to_event(pre_tool_use("Edit", file_path="/tmp/ws/x.py", new_string="hello"))
        assert event.event is EventType.PRE_TOOL_USE
        assert event.agent == "claude-code"
        assert event.tool == "Edit"
        assert event.arg("new_string") == "hello"
        assert event.is_mutating
        assert not event.is_read_only

    def test_extracts_prompt(self):
        event = claude.to_event(user_prompt_submit("Add pagination to /users"))
        assert event.prompt_text == "Add pagination to /users"

    def test_read_only_tools_are_flagged(self):
        event = claude.to_event(pre_tool_use("Read", file_path="/tmp/ws/x.py"))
        assert event.is_read_only

    def test_keeps_raw_payload_for_lossless_round_trip(self):
        payload = stop_event(stop_hook_active=True)
        event = claude.to_event(payload)
        assert event.raw["stop_hook_active"] is True


class TestFromDecision:
    def test_allow_is_silent(self):
        """SPEC §39: normal operation produces no output at all."""
        event = claude.to_event(pre_tool_use())
        out = claude.from_decision(event, Decision.allow(), "PreToolUse")
        assert out == {}

    def test_agentguard_never_grants_permission(self):
        """A guard that auto-approves would override the developer's own permission rules.

        No decision path may ever emit permissionDecision == "allow".
        """
        event = claude.to_event(pre_tool_use())
        for action in DecisionAction:
            decision = Decision(action=action, reason="r", updated_arguments={"command": "ls"})
            out = claude.from_decision(event, decision, "PreToolUse")
            got = out.get("hookSpecificOutput", {}).get("permissionDecision")
            assert got != "allow", f"{action} produced permissionDecision=allow"

    def test_challenge_denies_with_reason(self):
        """SPEC §21: the reason goes back to the host, which re-reasons."""
        event = claude.to_event(pre_tool_use())
        decision = Decision(action=DecisionAction.CHALLENGE, reason="No such method exists.")
        out = claude.from_decision(event, decision, "PreToolUse")
        spec = out["hookSpecificOutput"]
        assert spec["hookEventName"] == "PreToolUse"
        assert spec["permissionDecision"] == "deny"
        assert spec["permissionDecisionReason"] == "No such method exists."

    def test_request_review_asks_the_human(self):
        event = claude.to_event(pre_tool_use())
        decision = Decision(action=DecisionAction.REQUEST_REVIEW, reason="Needs a human.")
        out = claude.from_decision(event, decision, "PreToolUse")
        assert out["hookSpecificOutput"]["permissionDecision"] == "ask"

    def test_modify_rewrites_input_and_announces_itself(self):
        """SPEC §18 MODIFY. Silent rewrites would be a trust hazard, so it is announced."""
        event = claude.to_event(pre_tool_use("Bash", command="rm -rf /"))
        decision = Decision(
            action=DecisionAction.MODIFY,
            reason="Narrowed to the intended directory.",
            updated_arguments={"command": "rm -rf ./build"},
        )
        out = claude.from_decision(event, decision, "PreToolUse")
        spec = out["hookSpecificOutput"]
        assert spec["updatedInput"] == {"command": "rm -rf ./build"}
        assert spec["permissionDecision"] == "defer"
        assert spec["additionalContext"]

    def test_user_prompt_injects_context(self):
        """SPEC §9/§13: the planning budget rides in on additionalContext."""
        event = claude.to_event(user_prompt_submit("Rename getUser"))
        decision = Decision(additional_context="Complexity: 2/100. Plan: find refs, rename, test.")
        out = claude.from_decision(event, decision, "UserPromptSubmit")
        assert "Complexity: 2/100" in out["hookSpecificOutput"]["additionalContext"]

    def test_stop_block_is_the_completion_gate(self):
        """SPEC §19: a blocked Stop forces the agent to keep working."""
        event = claude.to_event(stop_event())
        decision = Decision(action=DecisionAction.BLOCK, reason="No tests were run.")
        out = claude.from_decision(event, decision, "Stop")
        assert out == {"decision": "block", "reason": "No tests were run."}

    def test_post_tool_use_block(self):
        event = claude.to_event(pre_tool_use())
        decision = Decision(action=DecisionAction.BLOCK, reason="Broke the build.")
        out = claude.from_decision(event, decision, "PostToolUse")
        assert out["decision"] == "block"
        assert out["reason"] == "Broke the build."

    def test_session_start_context(self):
        event = claude.to_event(session_start())
        out = claude.from_decision(event, Decision(additional_context="1200 files indexed."), "SessionStart")
        assert out["hookSpecificOutput"]["additionalContext"] == "1200 files indexed."

    @pytest.mark.parametrize(
        "hook_name", ["PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit", "SessionStart"]
    )
    def test_silence_for_every_hook_on_plain_allow(self, hook_name):
        event = claude.to_event(pre_tool_use())
        assert claude.from_decision(event, Decision.allow(), hook_name) == {}

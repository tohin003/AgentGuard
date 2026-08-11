"""Claude Code ⇄ AgentGuard translation (SPEC §23, §26).

    "Every adapter translates its native events into this common representation."

This module is the *only* place in the codebase that knows Claude Code's JSON shape.

A safety property worth stating explicitly
------------------------------------------
**AgentGuard never returns ``permissionDecision: "allow"``.**

"allow" in Claude Code short-circuits the user's own permission rules. A guard layer that
silently granted permissions the developer had chosen to be asked about would make the
system *less* safe, which is the exact opposite of the point. AgentGuard therefore only
ever: stays silent (no decision — normal permission flow proceeds), objects ("deny" with a
reason the host can reason about), or escalates to the human ("ask").

Hook contract verified against https://code.claude.com/docs/en/hooks on 2026-08-11.
"""

from __future__ import annotations

from typing import Any

from agentguard.core.enums import DecisionAction, EventType
from agentguard.core.events import AgentEvent
from agentguard.core.models import Decision

AGENT_NAME = "claude-code"

# Claude Code hook event name -> normalized event type.
EVENT_MAP: dict[str, EventType] = {
    "SessionStart": EventType.SESSION_START,
    "UserPromptSubmit": EventType.USER_PROMPT,
    "PreToolUse": EventType.PRE_TOOL_USE,
    "PostToolUse": EventType.POST_TOOL_USE,
    "PostToolUseFailure": EventType.POST_TOOL_FAILURE,
    "Stop": EventType.STOP,
    "SubagentStop": EventType.SUBAGENT_STOP,
    "SessionEnd": EventType.SESSION_END,
}

# The events AgentGuard asks to be installed for. Anything else is noise.
SUBSCRIBED_EVENTS: tuple[str, ...] = tuple(EVENT_MAP)


def to_event(payload: dict[str, Any]) -> AgentEvent | None:
    """Claude Code hook payload -> AgentEvent. Returns None for events we ignore."""
    hook_name = payload.get("hook_event_name", "")
    event_type = EVENT_MAP.get(hook_name)
    if event_type is None:
        return None

    return AgentEvent(
        event=event_type,
        agent=AGENT_NAME,
        workspace=payload.get("cwd") or ".",
        session_id=payload.get("session_id") or "",
        tool=payload.get("tool_name"),
        arguments=payload.get("tool_input") or {},
        tool_use_id=payload.get("tool_use_id"),
        result=payload.get("tool_output"),
        prompt_text=payload.get("prompt_text"),
        last_assistant_message=payload.get("last_assistant_message"),
        raw=payload,
    )


def _spec_out(hook_name: str, **fields: Any) -> dict[str, Any]:
    body = {"hookEventName": hook_name}
    body.update({k: v for k, v in fields.items() if v is not None})
    return {"hookSpecificOutput": body}


def from_decision(event: AgentEvent, decision: Decision, hook_name: str) -> dict[str, Any]:
    """Decision -> Claude Code hook output JSON.

    An empty dict means "AgentGuard has no opinion", which is the desired outcome for
    the overwhelming majority of actions (SPEC §39).
    """
    if hook_name == "PreToolUse":
        return _pre_tool_use_out(decision)
    if hook_name in ("PostToolUse", "PostToolUseFailure"):
        return _post_tool_use_out(decision, hook_name)
    if hook_name == "UserPromptSubmit":
        return _context_only_out(decision, hook_name)
    if hook_name in ("Stop", "SubagentStop"):
        return _stop_out(decision, hook_name)
    if hook_name == "SessionStart":
        return _session_start_out(decision)
    return {}


def _pre_tool_use_out(decision: Decision) -> dict[str, Any]:
    action = decision.action

    if action in (DecisionAction.CHALLENGE, DecisionAction.BLOCK):
        # "deny" hands the reason back to Claude, which then re-reasons with its own
        # intelligence and proposes something else (SPEC §21). This *is* the loop.
        return _spec_out(
            "PreToolUse",
            permissionDecision="deny",
            permissionDecisionReason=decision.reason,
        )

    if action is DecisionAction.REQUEST_REVIEW:
        return _spec_out(
            "PreToolUse",
            permissionDecision="ask",
            permissionDecisionReason=decision.reason,
        )

    if action is DecisionAction.MODIFY and decision.updated_arguments is not None:
        # "defer" keeps the user's normal permission flow intact while still rewriting
        # the arguments; the change is always announced rather than made silently.
        return _spec_out(
            "PreToolUse",
            permissionDecision="defer",
            updatedInput=decision.updated_arguments,
            additionalContext=decision.additional_context or decision.reason or None,
        )

    if decision.additional_context:
        return _spec_out("PreToolUse", additionalContext=decision.additional_context)

    return {}  # silence


def _post_tool_use_out(decision: Decision, hook_name: str) -> dict[str, Any]:
    if decision.action in (DecisionAction.BLOCK, DecisionAction.CHALLENGE):
        out: dict[str, Any] = {"decision": "block", "reason": decision.reason}
        if decision.additional_context:
            out.update(_spec_out(hook_name, additionalContext=decision.additional_context))
        return out
    if decision.additional_context:
        return _spec_out(hook_name, additionalContext=decision.additional_context)
    return {}


def _context_only_out(decision: Decision, hook_name: str) -> dict[str, Any]:
    """UserPromptSubmit: inject the task spec + planning budget (SPEC §9, §13)."""
    if decision.action is DecisionAction.BLOCK:
        return {"decision": "block", "reason": decision.reason}
    if decision.additional_context:
        return _spec_out(hook_name, additionalContext=decision.additional_context)
    return {}


def _stop_out(decision: Decision, hook_name: str) -> dict[str, Any]:
    """Completion Gate (SPEC §19). Blocking Stop forces the agent to keep working."""
    if decision.action in (DecisionAction.BLOCK, DecisionAction.CHALLENGE):
        return {"decision": "block", "reason": decision.reason}
    if decision.additional_context:
        return _spec_out(hook_name, additionalContext=decision.additional_context)
    return {}


def _session_start_out(decision: Decision) -> dict[str, Any]:
    # `watchPaths` is wired in Phase 1, once there is an index whose freshness depends
    # on specific files (manifests, lockfiles, config).
    if decision.additional_context:
        return _spec_out("SessionStart", additionalContext=decision.additional_context)
    return {}

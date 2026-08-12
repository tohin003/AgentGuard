"""MODIFY: rewriting a proposed action, under a narrowing invariant (SPEC §18).

`updatedInput` lets AgentGuard rewrite a tool's arguments before it runs. That is a real
capability and a real hazard, because the rewrite path is the one place AgentGuard may
emit `permissionDecision: "allow"` — skipping the prompt the developer would otherwise
have seen for that call.

The decision to allow it rests on one property: **the rewrite is a narrowing.** The host
LLM proposed the action; AgentGuard only ever makes it reach less far. This module makes
that property something the code proves rather than something the design assumes.

Three rules, in order of importance:

1. **Never broaden.** A rewrite must reduce reach. If the result could touch anything the
   original could not, it is not a MODIFY.
2. **Re-check the result.** The rewritten arguments go back through the risk checks. A bug
   here must not be able to launder a dangerous command through a bypassed prompt.
3. **Never silent.** Every rewrite announces what changed and why.

Anything that fails a rule is not rewritten — it is challenged or escalated, which are
both safe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentguard.core.enums import (
    ChallengeCategory,
    EscalationLevel,
    FailureMode,
    Severity,
    Verdict,
)
from agentguard.core.events import AgentEvent
from agentguard.core.models import EvidenceRef, Finding
from agentguard.validate import checks


@dataclass(slots=True)
class Rewrite:
    arguments: dict
    summary: str  # what changed, in one line
    reason: str  # why it is safe


# `rm -rf $VAR` expands to `rm -rf /` when VAR is unset. Quoting the variable and refusing
# to proceed on an empty value is the standard fix, and it is a strict narrowing: the
# command can now only affect what it already named.
_UNGUARDED_RM = re.compile(
    r"\brm\s+(?P<flags>-[a-zA-Z]*[rR][a-zA-Z]*f?|-[a-zA-Z]*f[a-zA-Z]*[rR])\s+"
    r"(?P<target>\$\{?\w+\}?)(?P<tail>/?[\w./-]*)"
)


def propose(event: AgentEvent) -> Rewrite | None:
    """A safe narrowing of this action, or None.

    Returning None is always acceptable — it simply means the action is judged as-is.
    """
    if event.tool != "Bash":
        return None
    command = event.arg("command")
    if not isinstance(command, str) or not command.strip():
        return None

    match = _UNGUARDED_RM.search(command)
    if match:
        variable = match.group("target")
        bare = variable.strip("${}")
        guarded = f'[ -n "{variable}" ] && rm {match.group("flags")} "{variable}{match.group("tail")}"'
        rewritten = command[: match.start()] + guarded + command[match.end() :]

        candidate = dict(event.arguments)
        candidate["command"] = rewritten
        if not _is_narrowing(command, rewritten) or not _still_safe(event, candidate):
            return None

        return Rewrite(
            arguments=candidate,
            summary=f"guarded `{bare}` against being unset",
            reason=(
                f"`rm -rf {variable}` deletes `/` if `{bare}` is empty. The rewrite refuses "
                "to run rather than expanding to a root path; it can only ever affect what "
                "the command already named."
            ),
        )

    return None


def _is_narrowing(original: str, rewritten: str) -> bool:
    """Rule 1: the rewrite must not extend reach.

    Deliberately crude and deliberately strict — every check here is a reason to *refuse*
    to rewrite, and refusing is always safe.
    """
    if rewritten == original:
        return False
    # A rewrite must not introduce a new command separator: that would be a way to append
    # something the original never contained.
    for separator in (";", "&&", "||", "|", "\n"):
        if rewritten.count(separator) > original.count(separator) + (
            2 if separator == "&&" else 0
        ):
            return False
    # It must not introduce recursion or force that was not already there.
    for dangerous in ("sudo", "--no-preserve-root", "chmod", "chown", "mkfs", "dd "):
        if dangerous in rewritten and dangerous not in original:
            return False
    return True


def _still_safe(event: AgentEvent, candidate: dict) -> bool:
    """Rule 2: run the rewritten arguments back through the risk checks."""
    probe = event.model_copy(update={"arguments": candidate})
    return not checks.risky_command(probe)


def finding_for(rewrite: Rewrite, event: AgentEvent) -> Finding:
    """Rule 3: the rewrite explains itself."""
    return Finding(
        category=ChallengeCategory.RISK,
        verdict=Verdict.SUPPORTED_WITH_RISK,
        # A narrowing rewrite is a thing AgentGuard did, not a failure the agent made.
        # SPEC §3 has no entry for it and the census must not invent one.
        failure_mode=FailureMode.NOT_A_FAILURE,
        severity=Severity.MEDIUM,
        subject="modified command",
        summary=f"AgentGuard narrowed this command: {rewrite.summary}",
        detail=rewrite.reason,
        evidence=[EvidenceRef(source="runtime", note=str(event.arg("command"))[:200])],
        suggestion="Run it as written if the original was intended.",
        level=EscalationLevel.DETERMINISTIC,
    )

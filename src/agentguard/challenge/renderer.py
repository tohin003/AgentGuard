"""Rendering a challenge for the host agent (SPEC §14, §16, §21).

SPEC §14 gives the shape:

    AGENTGUARD:
    The proposed method UserRepository.get_active_users() was not found in the repository.
    Evidence: src/repositories/user.py
    Re-evaluate the proposed implementation and use an existing verified interface unless
    you can provide evidence that this method should be introduced.

Three things make a challenge work, and all three are about respecting the host's
intelligence rather than overriding it (SPEC §21):

1. **State the evidence, with file and line.** The host must be able to check.
2. **Say what does exist.** "There is no `get_active_users`" is a complaint; "there is no
   `get_active_users`; the type has `get_by_id`, `list_all`, `count`" is usable.
3. **Leave the door open.** The agent may have a good reason — it might be adding the
   method deliberately. The challenge asks it to proceed *with* justification, not to
   obey. AgentGuard is not always right, and a challenge that admits that gets better
   answers than one that does not.

This module deals only in `Finding`, a core model. Turning a `Resolution` into a
`Finding` belongs to the evidence layer that produced it — keeping that direction
one-way is what stops evidence and challenge importing each other.
"""

from __future__ import annotations

from agentguard.core.enums import ChallengeCategory, Severity, Verdict
from agentguard.core.models import Finding

HEADER = "AgentGuard — evidence check"


def render(findings: list[Finding]) -> str:
    """The message the host agent receives as its denial reason."""
    if not findings:
        return ""

    lines = [HEADER, ""]

    for index, finding in enumerate(findings, start=1):
        prefix = f"{index}. " if len(findings) > 1 else ""
        lines.append(f"{prefix}{finding.summary}")

        if finding.detail:
            lines.append(f"   {finding.detail}")

        for ref in finding.evidence[:3]:
            lines.append(f"   Evidence: {ref.render()}")

        if finding.suggestion:
            lines.append(f"   {finding.suggestion}")
        lines.append("")

    lines.append(_closing(findings))
    return "\n".join(lines).strip()


def _closing(findings: list[Finding]) -> str:
    """The instruction, and the escape hatch.

    SPEC §17 is explicit that AgentGuard must not simply tell the host it is wrong. The
    host is the one with the intelligence; this asks it to look again, and says exactly
    what would satisfy the objection.
    """
    contradicted = any(f.verdict is Verdict.CONTRADICTED for f in findings)
    dependency = any(f.category is ChallengeCategory.DEPENDENCY for f in findings)

    if dependency and not contradicted:
        return (
            "Adding a dependency is a permanent decision. Either use what the repository "
            "already has, or state what this dependency provides that existing code does "
            "not, and add it to the project manifest rather than installing it ad hoc."
        )

    if contradicted:
        return (
            "Re-check the interface before writing this. Use an existing verified member, "
            "or — if you are deliberately introducing it — say so and define it in the "
            "same change, so the call and the definition land together."
        )

    return (
        "Confirm this against the repository before relying on it, or state the evidence "
        "that supports it."
    )


def severity_at_least(finding: Finding, minimum: str) -> bool:
    order = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
    try:
        return order.index(finding.severity) >= order.index(Severity(minimum))
    except ValueError:
        return True

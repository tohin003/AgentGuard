"""Observe-only mode — AgentGuard as a sensor (Phase 7, the failure-mode census).

The benchmark (`docs/BENCH-mutation.md`) established that the evidence engine works and
that the failure it detects is largely absent from current models. What it could not say
is which of SPEC §3's *other* sixteen failures actually happen during real work. Guessing
is how the project ended up building an excellent detector for a solved problem, so the
next step counts instead.

Counting requires observing without interfering, for two reasons:

1. **A guard changes what it guards.** An injected planning budget shapes the agent's
   approach; a challenge changes the next action. Measuring failure rates while steering
   the agent measures a steered agent, and the number would say nothing about the
   unguarded baseline the census exists to establish.
2. **A week of real work is the corpus.** It has to be safe to leave on. Anything that can
   interrupt, slow, or annoy will be switched off before the week is out.

So observe-only makes exactly one guarantee, at exactly one place:

    Every decision leaving the Guard is silent. All of them. Always.

`silence()` is that place. Findings survive — they are the census — and the action that
*would* have been taken is preserved on `would_have`, so the log can still answer "what
would AgentGuard have done here?" without the agent ever hearing it.

**The price, stated plainly:** while observe-only is on, AgentGuard guards nothing. No
challenges, no completion gate, no planning budget. That is the cost of an uncontaminated
census, and it is why this is a mode you turn on deliberately rather than a default.
"""

from __future__ import annotations

from agentguard.core.enums import DecisionAction
from agentguard.core.models import Decision


def silence(decision: Decision) -> Decision:
    """Strip everything the agent could perceive, keep everything the census needs.

    Idempotent, and total: there is no decision shape this cannot silence, because it
    builds a fresh ALLOW rather than editing fields it happens to know about. A future
    channel added to `Decision` is silent here by default and has to be *deliberately*
    let through — the safe direction for a mode whose entire promise is silence.
    """
    would_have = decision.would_have or decision.action
    return Decision(
        action=DecisionAction.ALLOW,
        # Kept: the census is the findings, and the level says how hard we looked.
        findings=decision.findings,
        level=decision.level,
        latency_ms=decision.latency_ms,
        decision_id=decision.decision_id,
        would_have=would_have,
        # Dropped: reason, additional_context, updated_arguments — every path by which
        # AgentGuard reaches the agent. `reason` never reaches the wire on an ALLOW
        # anyway, but relying on that would make silence a property of the adapter
        # rather than of this function.
    )

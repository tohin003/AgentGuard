"""Rationing challenges (SPEC §17, §39).

    AgentGuard must NOT become an annoying system that constantly tells the host:
    "You're wrong." That would reduce the intelligence of the overall system.

This module is the mechanism that makes that guarantee real. Detecting a problem is not
sufficient reason to raise it; the ledger decides whether raising it is *useful*.

Three rules:

1. **Once per concern, per task.** The same finding about the same subject is raised at
   most once. If the agent proceeds anyway, it has heard the objection and made a
   decision — repeating it is nagging, and nagging gets AgentGuard uninstalled.
2. **A hard ceiling per task.** After a handful of challenges the problem is not the
   individual claims, it is that AgentGuard and the agent disagree about the task. More
   objections will not fix that; a human might.
3. **Below-threshold findings are recorded, not raised.** They appear in
   `agentguard log` and shape nothing else.

The second encounter is also the SPEC §17 acceptance path: an agent that was challenged,
reconsidered, and came back with the same approach is *allowed to proceed*. AgentGuard
supplied the evidence; the host applied its intelligence; that is the whole design.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentguard.challenge.renderer import severity_at_least
from agentguard.core.config import ChallengeSettings
from agentguard.core.models import Finding
from agentguard.core.store import Store


@dataclass(slots=True)
class LedgerVerdict:
    raise_now: list[Finding]
    suppressed: list[Finding]
    reason: str = ""

    @property
    def should_challenge(self) -> bool:
        return bool(self.raise_now)


class ChallengeLedger:
    def __init__(self, store: Store, settings: ChallengeSettings | None = None) -> None:
        self.store = store
        self.settings = settings or ChallengeSettings()

    def admit(self, task_id: str | None, findings: list[Finding]) -> LedgerVerdict:
        """Decide which findings are worth the host's attention right now."""
        if not findings:
            return LedgerVerdict([], [])

        eligible = [f for f in findings if severity_at_least(f, self.settings.min_severity)]
        below = [f for f in findings if f not in eligible]

        if not eligible:
            return LedgerVerdict([], below, "below severity threshold")

        if task_id is None:
            # No task context means no ledger, and no ledger means no way to avoid
            # repeating ourselves. Record only.
            return LedgerVerdict([], findings, "no task context")

        already = self.store.challenge_count(task_id)
        if already >= self.settings.max_per_task:
            return LedgerVerdict(
                [], findings, f"task challenge budget spent ({already}/{self.settings.max_per_task})"
            )

        raise_now: list[Finding] = []
        suppressed: list[Finding] = list(below)
        budget = self.settings.max_per_task - already

        for finding in sorted(eligible, key=lambda f: -_severity_rank(f)):
            fingerprint = finding.fingerprint()
            seen = self.store.challenge_count(task_id, fingerprint)
            if seen >= self.settings.max_per_fingerprint:
                # Already raised. The agent has heard it and chosen to proceed —
                # SPEC §17's "evidence sufficient -> ALLOW".
                suppressed.append(finding)
                continue
            if len(raise_now) >= budget:
                suppressed.append(finding)
                continue
            raise_now.append(finding)

        return LedgerVerdict(raise_now, suppressed)

    def record(self, task_id: str | None, findings: list[Finding]) -> None:
        if task_id is None:
            return
        for finding in findings:
            self.store.record_challenge(task_id, finding.fingerprint(), finding.subject)

    def resolve(self, task_id: str | None, findings: list[Finding]) -> None:
        """Mark concerns as settled — e.g. the agent defined the method it was calling."""
        if task_id is None:
            return
        for finding in findings:
            self.store.resolve_challenge(task_id, finding.fingerprint())


def _severity_rank(finding: Finding) -> int:
    return {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}.get(str(finding.severity), 2)

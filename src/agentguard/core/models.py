"""Cross-cutting models: evidence references, findings, decisions.

These are the things every engine produces and the adapters consume.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentguard.core.enums import (
    ChallengeCategory,
    DecisionAction,
    EscalationLevel,
    FailureMode,
    Severity,
    Verdict,
)


class EvidenceRef(BaseModel):
    """A pointer to something real (SPEC §14 evidence sources).

    A finding without evidence is an opinion, and AgentGuard does not deal in opinions.
    `source` names which deterministic system produced it: filesystem, ast, imports,
    manifest, git, tests, schema, runtime, build.
    """

    source: str
    path: str | None = None
    line: int | None = None
    symbol: str | None = None
    snippet: str | None = None
    note: str | None = None

    def render(self) -> str:
        if self.path and self.line:
            base = f"{self.path}:{self.line}"
        elif self.path:
            base = self.path
        elif self.symbol:
            base = self.symbol
        else:
            base = self.source
        return f"{base} — {self.note}" if self.note else base


class Finding(BaseModel):
    """One thing AgentGuard noticed about a proposed action."""

    model_config = ConfigDict(extra="forbid")

    category: ChallengeCategory
    verdict: Verdict
    # Which of SPEC §3's documented failure modes this is evidence of. Required, and with
    # no default on purpose: a detector that cannot say what it is detecting has no place
    # in a census. `NOT_A_FAILURE` is the honest answer for findings outside the §3 list.
    failure_mode: FailureMode
    severity: Severity = Severity.MEDIUM
    subject: str = ""
    summary: str = ""
    detail: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)
    suggestion: str = ""
    level: EscalationLevel = EscalationLevel.DETERMINISTIC

    def fingerprint(self) -> str:
        """Stable identity for challenge deduplication (SPEC §17, §39).

        The same concern about the same subject must only be raised once per task —
        otherwise AgentGuard becomes "AI that constantly interrupts AI".
        """
        raw = f"{self.category}|{self.verdict}|{self.subject}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]


class Decision(BaseModel):
    """What AgentGuard decided about one proposed action (SPEC §18)."""

    model_config = ConfigDict(extra="forbid")

    action: DecisionAction = DecisionAction.ALLOW
    reason: str = ""
    findings: list[Finding] = Field(default_factory=list)
    level: EscalationLevel = EscalationLevel.DETERMINISTIC
    latency_ms: float = 0.0
    updated_arguments: dict[str, Any] | None = None
    additional_context: str | None = None
    decision_id: str = ""
    # Set only in observe-only mode: the action that *would* have been taken. Without it
    # the census could count what AgentGuard saw but not what it would have said, and
    # "how noisy would guarding be here?" is the question that decides whether to turn it
    # back on. `None` means this decision was the real one.
    would_have: DecisionAction | None = None

    @classmethod
    def allow(cls, level: EscalationLevel = EscalationLevel.DETERMINISTIC) -> Decision:
        """The overwhelmingly common case. Silent by design (SPEC §39)."""
        return cls(action=DecisionAction.ALLOW, level=level)

    @property
    def is_silent(self) -> bool:
        """True when this decision carries nothing at all — not even an internal note.

        Stronger than "the agent heard nothing", and deliberately so. An ALLOW whose
        `reason` records why a finding was *not* raised ("below severity threshold") is
        already invisible to the agent — the adapter drops `reason` on an allow — but it
        is not nothing, and a property that called it silent would hide the difference.

        For the question that actually matters to a caller, ask the adapter:
        `translate.from_decision(event, decision, hook) == {}` is the ground truth about
        what reached the agent, because that dict *is* what reached the agent.
        """
        return (
            self.action is DecisionAction.ALLOW
            and not self.reason
            and not self.additional_context
        )

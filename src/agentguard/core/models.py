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

    @classmethod
    def allow(cls, level: EscalationLevel = EscalationLevel.DETERMINISTIC) -> Decision:
        """The overwhelmingly common case. Silent by design (SPEC §39)."""
        return cls(action=DecisionAction.ALLOW, level=level)

    @property
    def is_silent(self) -> bool:
        """True when the developer and the agent see nothing at all."""
        return (
            self.action is DecisionAction.ALLOW
            and not self.reason
            and not self.additional_context
        )

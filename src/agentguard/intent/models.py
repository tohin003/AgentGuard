"""TaskSpec — what the developer actually asked for (SPEC §9).

    Task / Domain / Complexity / Risk / Expected changes / Required verification /
    Unnecessary actions

This is the object the Complexity Engine scores, the Planning Governor renders, and the
Action Validator checks scope against. It is produced deterministically from the prompt
plus the repository index — no LLM (SPEC §6).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentguard.core.enums import Domain, PlanningDepth, RiskLevel
from agentguard.core.models import EvidenceRef


class Target(BaseModel):
    """Something the prompt referred to, and what the repository says about it."""

    token: str
    kind: str  # symbol | file | endpoint | module | unknown
    resolved: bool = False
    paths: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)

    def __hash__(self) -> int:
        return hash((self.token, self.kind))


class ComplexitySignal(BaseModel):
    """One dimension of SPEC §12's complexity sum, with the evidence behind it.

    The score is never reported without the reason: a number nobody can argue with is
    not evidence, and the host agent must be able to push back (SPEC §17).
    """

    name: str
    score: float
    max_score: float
    reason: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.score / self.max_score if self.max_score else 0.0


class ComplexityAssessment(BaseModel):
    score: float = 0.0
    depth: PlanningDepth = PlanningDepth.DIRECT
    risk: RiskLevel = RiskLevel.LOW
    signals: list[ComplexitySignal] = Field(default_factory=list)
    applied_rules: list[str] = Field(default_factory=list)
    raw_score: float = 0.0  # before override rules, for transparency

    def signal(self, name: str) -> ComplexitySignal | None:
        return next((s for s in self.signals if s.name == name), None)

    def top_signals(self, limit: int = 4) -> list[ComplexitySignal]:
        """Signals that actually drove the score.

        The threshold matters: every task has *some* scope, and reporting
        "scope: narrow, well-identified target" as a reason for deep planning would be
        actively misleading to the host agent.
        """
        return sorted(
            [s for s in self.signals if s.score >= 3.0], key=lambda s: -s.score
        )[:limit]


class TaskSpec(BaseModel):
    """The full picture of one developer request."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = ""
    goal: str = ""
    verbs: list[str] = Field(default_factory=list)

    targets: list[Target] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)

    primary_domain: Domain = Domain.UNKNOWN
    secondary_domains: list[Domain] = Field(default_factory=list)

    expected_scope: list[str] = Field(default_factory=list)
    unnecessary_actions: list[str] = Field(default_factory=list)
    verification_expected: list[str] = Field(default_factory=list)

    complexity: ComplexityAssessment = Field(default_factory=ComplexityAssessment)

    @property
    def resolved_targets(self) -> list[Target]:
        return [t for t in self.targets if t.resolved]

    @property
    def unresolved_targets(self) -> list[Target]:
        return [t for t in self.targets if not t.resolved]

    @property
    def primary_verb(self) -> str:
        return self.verbs[0] if self.verbs else "unknown"

    @property
    def domains(self) -> list[Domain]:
        return [self.primary_domain, *self.secondary_domains]

    def in_expected_scope(self, path: str) -> bool:
        """Whether touching `path` is consistent with what was asked (SPEC §18).

        An empty expected scope means "we could not tell", which must never be read as
        "nothing is allowed" — that would block ordinary work.
        """
        if not self.expected_scope:
            return True
        normalized = path.replace("\\", "/").lstrip("./")
        return any(
            normalized == entry or normalized.startswith(entry.rstrip("/") + "/")
            for entry in self.expected_scope
        )

    def summary_line(self) -> str:
        domains = "/".join(d.value for d in self.domains if d is not Domain.UNKNOWN)
        return (
            f"{self.primary_verb} · {domains or 'unknown domain'} · "
            f"complexity {self.complexity.score:.0f}/100 ({self.complexity.depth.value})"
        )

"""Claims — the unit the Evidence Engine works in (SPEC §14, §15).

    Claim -> Evidence -> Confidence -> Action

A *claim* is something the agent's proposed action asserts about the world: that a method
exists on a type, that a module can be imported, that a file is there. The engine's job
is to look each one up and report what the repository actually says.

The `confident` flag carries the rule established in Phase 1 and it is the most important
field here: a claim may only be reported as contradicted when we have complete evidence
about the thing it concerns. Absence of evidence is not evidence of absence, and a
challenge raised from ignorance is worse than no challenge at all (SPEC §39).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from agentguard.core.enums import ClaimKind, Severity, Verdict
from agentguard.core.models import EvidenceRef


@dataclass(slots=True)
class Claim:
    """Something the proposed action asserts to be true."""

    kind: ClaimKind
    subject: str  # "UserRepository.get_active_users", "shop.utils.pagination", "src/x.py"
    owner: str = ""  # the type/module the subject belongs to, when applicable
    path: str = ""  # file the claim was made in
    line: int = 0
    snippet: str = ""

    def fingerprint(self) -> str:
        raw = f"{self.kind}|{self.subject}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(slots=True)
class Resolution:
    """What the repository says about a claim."""

    claim: Claim
    verdict: Verdict
    severity: Severity = Severity.MEDIUM
    summary: str = ""
    detail: str = ""
    evidence: list[EvidenceRef] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)

    @property
    def is_problem(self) -> bool:
        return self.verdict in (
            Verdict.CONTRADICTED,
            Verdict.INSUFFICIENT_EVIDENCE,
            Verdict.REQUIRES_HUMAN,
        )

"""Complexity Engine (SPEC §12) and risk assessment.

    0-20    Direct implementation
    21-40   Lightweight plan
    41-70   Structured plan
    71-100  Deep architectural planning

Two stages: sum the eight signals, then apply the override rules that turn the sum into a
judgement. The pre-rule score is kept as `raw_score` so the effect of every rule is
visible rather than baked in.

Risk is computed separately from complexity, because they are different questions. SPEC
§9's pagination example is explicitly "Complexity: Low, Risk: Medium" — an easy change to
a public contract. Collapsing the two would lose that.
"""

from __future__ import annotations

from agentguard.complexity import rules as rule_module
from agentguard.complexity import signals as signal_module
from agentguard.complexity.rules import HIGH_CONSEQUENCE_DOMAINS
from agentguard.core.enums import PlanningDepth, RiskLevel
from agentguard.intent.models import ComplexityAssessment, ComplexitySignal, TaskSpec
from agentguard.repo.index import RepoIndex


def assess(spec: TaskSpec, index: RepoIndex | None = None) -> ComplexityAssessment:
    """Score a TaskSpec. Never raises; a failed signal contributes zero."""
    computed: list[ComplexitySignal] = []
    for signal_fn in signal_module.ALL_SIGNALS:
        try:
            computed.append(signal_fn(spec, index))
        except Exception:  # noqa: BLE001 - a broken signal must not break the assessment
            continue

    raw = sum(signal.score for signal in computed)
    by_name = {signal.name: signal for signal in computed}

    score = raw
    applied: list[str] = []
    for rule in rule_module.ALL_RULES:
        try:
            outcome = rule(spec, by_name, score, index)
        except Exception:  # noqa: BLE001
            continue
        if outcome is not None and outcome.new_score != score:
            score = outcome.new_score
            applied.append(f"{outcome.name}: {outcome.reason}")

    score = max(0.0, min(100.0, score))
    depth = PlanningDepth.from_score(score)

    return ComplexityAssessment(
        score=round(score, 1),
        raw_score=round(raw, 1),
        depth=depth,
        risk=assess_risk(spec, by_name, depth),
        signals=computed,
        applied_rules=applied,
    )


def assess_risk(
    spec: TaskSpec, signals: dict[str, ComplexitySignal], depth: PlanningDepth
) -> RiskLevel:
    """What could go wrong, as distinct from how hard it is.

    SPEC §9: "Add pagination to /users" is low complexity and medium risk, because it
    changes a contract other people depend on. SPEC §10 and §34 go the other way: tasks
    that are deep are also high-risk, because depth is driven by consequence.
    """
    security = signals["security_risk"].score if "security_risk" in signals else 0.0
    data = signals["data_risk"].score if "data_risk" in signals else 0.0
    reversibility = signals["reversibility"].score if "reversibility" in signals else 0.0
    blast = signals["blast_radius"].score if "blast_radius" in signals else 0.0

    if security >= 8 or data >= 8 or reversibility >= 4:
        return RiskLevel.HIGH

    # A task deep enough to need architectural planning, in a domain where mistakes are
    # expensive, is high-risk by construction — SPEC §10's "Change the prediction API"
    # and §34's "make our inference service production-ready" are both exactly this.
    if depth is PlanningDepth.DEEP:
        consequential = any(domain in HIGH_CONSEQUENCE_DOMAINS for domain in spec.domains)
        return RiskLevel.HIGH if consequential else RiskLevel.MEDIUM

    touches_public_contract = any(target.kind == "endpoint" for target in spec.targets)

    if security > 0 or data > 0 or blast >= 5 or touches_public_contract:
        return RiskLevel.MEDIUM

    return RiskLevel.LOW

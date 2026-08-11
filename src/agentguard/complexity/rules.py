"""Override rules — what makes SPEC §12 a decision system rather than a formula.

    "This should NOT be a rigid formula. It should be a decision system."

The SPEC gives two flowcharts and this module is their literal implementation:

    Simple task? -> Can existing architecture solve it? -> YES -> Use minimal approach

    Simple-looking task? -> Crosses service boundaries?
                         -> Security/data/architecture risk? -> YES -> Increase depth

A weighted sum alone cannot express either. "Make authentication horizontally scalable
across multiple services" scores in the forties by addition, because no single dimension
is extreme — but it is unambiguously a deep task, and the reason is the *combination*.
Conversely "rename getUser to fetchUser" must stay at DIRECT no matter how many
dependents the symbol happens to have.

Each rule is named, carries its SPEC reference, and reports itself in the assessment, so
`agentguard why` can explain exactly which judgement was applied and the host agent can
argue with it (SPEC §17).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from agentguard.core.enums import Domain
from agentguard.intent import lexicon as lex
from agentguard.intent.models import ComplexitySignal, TaskSpec
from agentguard.repo.index import RepoIndex

# Band boundaries from SPEC §12.
DIRECT_MAX = 20.0
LIGHT_MAX = 40.0
STRUCTURED_MAX = 70.0
DEEP_FLOOR = 75.0

# Domains where a mistake is expensive, slow to notice, or hard to undo. Used by the
# floor rules: the same words mean different things depending on what they are about.
HIGH_CONSEQUENCE_DOMAINS: frozenset[Domain] = frozenset(
    {
        Domain.ML_ENGINEERING,
        Domain.LLM,
        Domain.COMPUTER_VISION,
        Domain.MLOPS,
        Domain.DISTRIBUTED_SYSTEMS,
        Domain.AUTHENTICATION,
        Domain.SECRETS,
        Domain.DATABASE,
        Domain.CLOUD,
        Domain.KUBERNETES,
        Domain.DATA_PIPELINE,
    }
)

_STOPWORDS = frozenset(
    {
        "the", "this", "that", "with", "from", "into", "make", "made", "need", "want",
        "please", "should", "would", "could", "there", "their", "them", "then", "when",
        "where", "which", "while", "about", "across", "after", "before", "being",
        "have", "has", "and", "but", "for", "our", "your", "its", "add", "new", "use",
        "using", "code", "file", "files", "function", "class", "method", "service",
        "services", "system", "systems", "support", "change", "changes", "update",
    }
)


@dataclass(slots=True)
class RuleOutcome:
    name: str
    reason: str
    new_score: float


Rule = Callable[[TaskSpec, dict[str, ComplexitySignal], float, RepoIndex | None], RuleOutcome | None]


def _signal(signals: dict[str, ComplexitySignal], name: str) -> float:
    signal = signals.get(name)
    return signal.score if signal else 0.0


def crosses_service_boundaries(spec: TaskSpec) -> bool:
    lowered = spec.prompt.lower()
    if Domain.DISTRIBUTED_SYSTEMS in spec.domains:
        return True
    return any(
        phrase in lowered
        for phrase in (
            "across services", "multiple services", "between services", "other services",
            "microservice", "horizontally scalable", "horizontal scaling", "distributed",
            "cross-service", "service boundaries", "several services",
        )
    )


# -- the rules --------------------------------------------------------------------


def mechanical_narrow_cap(
    spec: TaskSpec, signals: dict[str, ComplexitySignal], score: float, index: RepoIndex | None
) -> RuleOutcome | None:
    """SPEC §2, §13: a rename is a rename.

        Find references -> Rename -> Run tests -> Done

    A mechanical verb against a small, identified target must not be inflated by
    incidental signals — a widely-imported symbol makes a rename *tedious*, not
    architecturally significant.
    """
    if spec.primary_verb not in lex.MECHANICAL_VERBS:
        return None
    if score <= DIRECT_MAX:
        return None
    if _signal(signals, "data_risk") > 3 or _signal(signals, "security_risk") > 3:
        return None
    if _signal(signals, "architectural_impact") > 6:
        return None
    if len({p for t in spec.resolved_targets for p in t.paths}) > 4:
        return None
    if crosses_service_boundaries(spec):
        return None

    return RuleOutcome(
        name="mechanical_narrow_cap",
        reason=(
            f"'{spec.primary_verb}' is mechanical work against a small, identified target; "
            f"depth capped at direct implementation (SPEC §2, §13)"
        ),
        new_score=min(score, DIRECT_MAX),
    )


def existing_capability_discount(
    spec: TaskSpec, signals: dict[str, ComplexitySignal], score: float, index: RepoIndex | None
) -> RuleOutcome | None:
    """SPEC §12: "Can existing architecture solve it? YES -> Use minimal approach."

    If the repository already contains something that does what is being asked for, the
    task is "use the existing thing", not "design a new thing" — which is exactly the
    §33 pagination scenario.
    """
    if index is None or not index.is_built:
        return None
    if score <= LIGHT_MAX or score > STRUCTURED_MAX:
        return None
    if crosses_service_boundaries(spec):
        return None
    if _signal(signals, "security_risk") > 4 or _signal(signals, "data_risk") > 4:
        return None

    existing = find_existing_capabilities(spec, index)
    if not existing:
        return None

    names = ", ".join(sorted({path for _, path in existing})[:3])
    return RuleOutcome(
        name="existing_capability_discount",
        reason=(
            f"the repository already implements this capability ({names}); "
            f"the minimal approach is to use it (SPEC §12, §33)"
        ),
        new_score=min(score, LIGHT_MAX),
    )


def cross_boundary_floor(
    spec: TaskSpec, signals: dict[str, ComplexitySignal], score: float, index: RepoIndex | None
) -> RuleOutcome | None:
    """SPEC §12's second flowchart, verbatim.

        Simple-looking task? -> Crosses service boundaries?
                             -> Security/data/architecture risk? -> YES -> Increase depth

    This is the rule that makes "make authentication horizontally scalable across
    multiple services" a deep task even though no single signal is extreme.
    """
    if not crosses_service_boundaries(spec):
        return None

    risky = (
        _signal(signals, "security_risk") >= 4
        or _signal(signals, "data_risk") >= 4
        or _signal(signals, "architectural_impact") >= 6
    )
    if not risky:
        return None
    if score >= DEEP_FLOOR:
        return None

    drivers = [
        name
        for name in ("security_risk", "data_risk", "architectural_impact")
        if _signal(signals, name) >= 4
    ]
    return RuleOutcome(
        name="cross_boundary_floor",
        reason=(
            "crosses service boundaries with "
            f"{', '.join(d.replace('_', ' ') for d in drivers)}; "
            "deep planning is justified (SPEC §12)"
        ),
        new_score=max(score, DEEP_FLOOR),
    )


def open_ended_scope_floor(
    spec: TaskSpec, signals: dict[str, ComplexitySignal], score: float, index: RepoIndex | None
) -> RuleOutcome | None:
    """SPEC §34: "Make our inference service production-ready."

        AgentGuard should NOT immediately say: "Keep it simple."

    An open-ended quality bar with no named target is high ambiguity and high scope at
    once. That is the textbook case for depth.
    """
    lowered = spec.prompt.lower()
    open_ended = [term for term in lex.OPEN_ENDED_TERMS if term in lowered]
    if not open_ended:
        return None
    if spec.primary_verb in lex.MECHANICAL_VERBS:
        return None
    if score >= DEEP_FLOOR:
        return None

    # Only when the request is genuinely unbounded: nothing specific named, or explicit
    # breadth language. "Make the retry logic robust" is not a deep task.
    unbounded = not spec.resolved_targets or any(
        term in lowered for term in lex.BREADTH_TERMS
    )
    if not unbounded:
        return None

    return RuleOutcome(
        name="open_ended_scope_floor",
        reason=(
            f"open-ended quality bar ({', '.join(open_ended[:2])}) with no bounded target; "
            "the task is mostly deciding what the task is (SPEC §34)"
        ),
        new_score=max(score, DEEP_FLOOR),
    )


def unspecified_interface_change_floor(
    spec: TaskSpec, signals: dict[str, ComplexitySignal], score: float, index: RepoIndex | None
) -> RuleOutcome | None:
    """SPEC §10: "Change the prediction API."

    The SPEC's own answer for that request is `"risk": "high", "planning_depth": "deep"`,
    and the reason is instructive: the sentence does not say *what* change. An
    unspecified change to a public interface of a high-consequence component is deep
    precisely because the first task is finding out what the task is — the backend view
    (contract, compatibility), the ML view (model version, feature compatibility, drift)
    and the MLOps view (deployment, rollback) each ask different questions, and no single
    one of them is sufficient.

    Deliberately narrow. "Add logging to the inference endpoint" touches the same files
    and the same domains, but says exactly what it wants, so it is not caught here.
    """
    if spec.primary_verb in lex.MECHANICAL_VERBS:
        return None
    if score >= DEEP_FLOOR:
        return None

    lowered = spec.prompt.lower()
    touches_interface = any(
        target.kind == "endpoint" for target in spec.targets
    ) or any(
        word in lowered
        for word in ("api", "endpoint", "interface", "contract", "schema", "protocol", "signature")
    )
    if not touches_interface:
        return None

    # "Unspecified" means either no recognisable action verb, or high measured uncertainty.
    unspecified = (
        spec.primary_verb == lex.VerbClass.UNKNOWN or _signal(signals, "uncertainty") >= 6
    )
    if not unspecified:
        return None

    consequential = [d for d in spec.domains if d in HIGH_CONSEQUENCE_DOMAINS]
    if not consequential:
        return None

    return RuleOutcome(
        name="unspecified_interface_change_floor",
        reason=(
            "an unspecified change to a public interface in "
            f"{'/'.join(d.value for d in consequential[:3])}; "
            "each domain in play asks different questions and the request answers none "
            "of them (SPEC §10)"
        ),
        new_score=max(score, DEEP_FLOOR),
    )


def irreversible_floor(
    spec: TaskSpec, signals: dict[str, ComplexitySignal], score: float, index: RepoIndex | None
) -> RuleOutcome | None:
    """Hard-to-undo work earns structure even when it is small."""
    if _signal(signals, "reversibility") < 4 and _signal(signals, "data_risk") < 8:
        return None
    if score > LIGHT_MAX:
        return None

    return RuleOutcome(
        name="irreversible_floor",
        reason="the change is hard to reverse; a structured plan is warranted before executing",
        new_score=max(score, LIGHT_MAX + 1),
    )


# Order matters: floors are applied after caps so that a genuinely dangerous mechanical
# task (a rename that crosses service boundaries) is not capped into invisibility.
ALL_RULES: tuple[Rule, ...] = (
    mechanical_narrow_cap,
    existing_capability_discount,
    irreversible_floor,
    cross_boundary_floor,
    open_ended_scope_floor,
    unspecified_interface_change_floor,
)


# -- helpers ----------------------------------------------------------------------


def find_existing_capabilities(spec: TaskSpec, index: RepoIndex) -> list[tuple[str, str]]:
    """Repository symbols/files that already implement what the prompt asks for.

    Returns (keyword, path) pairs. Matching is by word stem so "pagination" finds
    `paginate` and `pagination.py`.
    """
    words = {
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z-]{4,}", spec.prompt)
        if word.lower() not in _STOPWORDS
    }

    found: list[tuple[str, str]] = []
    for word in sorted(words):
        stem = word[:6]
        for symbol in index.search_names(stem, limit=3):
            # A test for a capability is not the capability.
            record = index.files.get(symbol.path)
            if record is not None and record.is_test:
                continue
            found.append((word, f"{symbol.path}:{symbol.line} ({symbol.qualname})"))
        for path in index.search_files(stem, limit=2):
            found.append((word, path))
    return found[:8]

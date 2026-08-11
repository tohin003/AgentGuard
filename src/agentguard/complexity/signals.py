"""The eight complexity signals of SPEC §12.

    Complexity = Scope + Dependency Count + Architectural Impact + Data Risk
               + Security Risk + Uncertainty + Blast Radius + Reversibility

Each signal returns a score, a maximum, and — non-negotiably — the reason and evidence
behind it. A complexity number the host agent cannot interrogate is not usable: SPEC §17
requires the agent to be able to push back with evidence, which it can only do if it can
see what the assessment was based on.

The maxima sum to 100 so the raw score lands directly on SPEC §12's bands.
"""

from __future__ import annotations

import math

from agentguard.core.enums import Domain
from agentguard.core.models import EvidenceRef
from agentguard.intent import lexicon as lex
from agentguard.intent.models import ComplexitySignal, TaskSpec
from agentguard.repo.index import RepoIndex

MAX_SCOPE = 15.0
MAX_DEPENDENCIES = 10.0
MAX_ARCHITECTURE = 15.0
MAX_DATA_RISK = 12.0
MAX_SECURITY_RISK = 12.0
MAX_UNCERTAINTY = 15.0
MAX_BLAST_RADIUS = 15.0
MAX_REVERSIBILITY = 6.0


def _matches(lowered: str, terms: tuple[str, ...]) -> list[str]:
    # Word-start matching, not substring: see lexicon.contains().
    return lex.matching_terms(lowered, terms)


def scope(spec: TaskSpec, index: RepoIndex | None) -> ComplexitySignal:
    """How much of the repository is in play."""
    lowered = spec.prompt.lower()
    files = {path for target in spec.resolved_targets for path in target.paths}
    directories = {path.rsplit("/", 1)[0] for path in files if "/" in path}

    score = 0.0
    reasons: list[str] = []

    if len(files) <= 1:
        score += 1.0
    elif len(files) <= 3:
        score += 3.0
        reasons.append(f"{len(files)} files referenced")
    elif len(files) <= 8:
        score += 6.0
        reasons.append(f"{len(files)} files referenced")
    else:
        score += 10.0
        reasons.append(f"{len(files)} files referenced")

    if len(directories) >= 3:
        score += 2.0
        reasons.append(f"spans {len(directories)} directories")

    breadth = _matches(lowered, lex.BREADTH_TERMS)
    if breadth:
        score += 5.0
        reasons.append(f"breadth language: {', '.join(breadth[:3])}")

    return ComplexitySignal(
        name="scope",
        score=min(score, MAX_SCOPE),
        max_score=MAX_SCOPE,
        reason="; ".join(reasons) or "narrow, well-identified target",
        evidence=[EvidenceRef(source="filesystem", path=p) for p in sorted(files)[:5]],
    )


def dependencies(spec: TaskSpec, index: RepoIndex | None) -> ComplexitySignal:
    """SPEC §16's DEPENDENCY question, asked before the agent proposes anything."""
    lowered = spec.prompt.lower()
    score = 0.0
    reasons: list[str] = []
    evidence: list[EvidenceRef] = []

    named = _matches(lowered, lex.TECH_NAMES)
    undeclared = []
    for tech in named:
        if index is not None and index.is_built and index.is_declared_dependency(tech):
            evidence.append(
                EvidenceRef(source="manifest", note=f"{tech} is already a declared dependency")
            )
        else:
            undeclared.append(tech)

    if undeclared:
        score += min(5.0 * len(undeclared), 8.0)
        reasons.append(f"names technology not currently declared: {', '.join(undeclared[:3])}")

    explicit_install = ("add a library", "new dependency", "install ", "pip install", "npm install")
    if any(word in lowered for word in explicit_install):
        score += 4.0
        reasons.append("explicitly proposes a new dependency")

    return ComplexitySignal(
        name="dependencies",
        score=min(score, MAX_DEPENDENCIES),
        max_score=MAX_DEPENDENCIES,
        reason="; ".join(reasons) or "no new dependency implied",
        evidence=evidence,
    )


def architecture(spec: TaskSpec, index: RepoIndex | None) -> ComplexitySignal:
    """Structural consequence — the difference between changing code and changing shape."""
    lowered = spec.prompt.lower()
    score = 0.0
    reasons: list[str] = []
    evidence: list[EvidenceRef] = []

    terms = _matches(lowered, lex.ARCHITECTURE_TERMS)
    if terms:
        score += min(3.0 * len(terms), 9.0)
        reasons.append(f"architectural language: {', '.join(terms[:4])}")

    if spec.primary_verb in lex.EXPLORATORY_VERBS or lex.VerbClass.DESIGN in spec.verbs:
        score += 4.0
        reasons.append(f"'{spec.primary_verb}' implies design work")

    top_level = {path.split("/")[0] for target in spec.resolved_targets for path in target.paths}
    if len(top_level) >= 3:
        score += 4.0
        reasons.append(f"crosses {len(top_level)} top-level packages")

    if index is not None and index.is_built:
        config_hits = [
            path
            for target in spec.resolved_targets
            for path in target.paths
            if index.files.get(path) and index.files[path].is_config
        ]
        if config_hits:
            score += 3.0
            reasons.append("touches project configuration")
            evidence.extend(EvidenceRef(source="filesystem", path=p) for p in config_hits[:3])

    return ComplexitySignal(
        name="architectural_impact",
        score=min(score, MAX_ARCHITECTURE),
        max_score=MAX_ARCHITECTURE,
        reason="; ".join(reasons) or "no structural change implied",
        evidence=evidence,
    )


def data_risk(spec: TaskSpec, index: RepoIndex | None) -> ComplexitySignal:
    lowered = spec.prompt.lower()
    score = 0.0
    reasons: list[str] = []

    terms = _matches(lowered, lex.DATA_RISK_TERMS)
    if terms:
        score += min(4.0 * len(terms), 9.0)
        reasons.append(f"data-affecting language: {', '.join(terms[:4])}")

    if Domain.DATABASE in spec.domains:
        score += 3.0
        reasons.append("database domain")

    return ComplexitySignal(
        name="data_risk",
        score=min(score, MAX_DATA_RISK),
        max_score=MAX_DATA_RISK,
        reason="; ".join(reasons) or "no data risk identified",
    )


def security_risk(spec: TaskSpec, index: RepoIndex | None) -> ComplexitySignal:
    lowered = spec.prompt.lower()
    score = 0.0
    reasons: list[str] = []

    terms = _matches(lowered, lex.SECURITY_RISK_TERMS)
    if terms:
        score += min(4.0 * len(terms), 8.0)
        reasons.append(f"security-sensitive language: {', '.join(terms[:4])}")

    if Domain.AUTHENTICATION in spec.domains or Domain.SECRETS in spec.domains:
        score += 4.0
        reasons.append("authentication/secrets domain")

    return ComplexitySignal(
        name="security_risk",
        score=min(score, MAX_SECURITY_RISK),
        max_score=MAX_SECURITY_RISK,
        reason="; ".join(reasons) or "no security risk identified",
    )


def uncertainty(spec: TaskSpec, index: RepoIndex | None) -> ComplexitySignal:
    """What we could not pin down. SPEC §12 treats not-knowing as complexity, correctly:
    the cost of a task you do not understand is the cost of understanding it first."""
    lowered = spec.prompt.lower()
    score = 0.0
    reasons: list[str] = []
    evidence: list[EvidenceRef] = []

    open_ended = _matches(lowered, lex.OPEN_ENDED_TERMS)
    if open_ended:
        score += min(4.0 * len(open_ended), 8.0)
        reasons.append(f"open-ended quality bar: {', '.join(open_ended[:3])}")

    vague = _matches(lowered, lex.AMBIGUITY_TERMS)
    if vague:
        score += min(1.5 * len(vague), 3.0)
        reasons.append(f"vague qualifiers: {', '.join(vague[:3])}")

    if index is not None and index.is_built:
        unresolved = spec.unresolved_targets
        if spec.targets and not spec.resolved_targets:
            score += 5.0
            reasons.append("nothing named in the request was found in the repository")
        elif unresolved and len(unresolved) > len(spec.resolved_targets):
            score += 3.0
            reasons.append(f"{len(unresolved)} referenced names not found in the repository")
        evidence.extend(
            EvidenceRef(source="ast", symbol=t.token, note="not found") for t in unresolved[:5]
        )
        if not spec.targets:
            score += 4.0
            reasons.append("request names no specific code")

    if spec.primary_verb in (lex.VerbClass.UNKNOWN, lex.VerbClass.INVESTIGATE):
        score += 3.0
        reasons.append("no clear action verb")

    return ComplexitySignal(
        name="uncertainty",
        score=min(score, MAX_UNCERTAINTY),
        max_score=MAX_UNCERTAINTY,
        reason="; ".join(reasons) or "request is specific and grounded",
        evidence=evidence,
    )


def blast_radius(spec: TaskSpec, index: RepoIndex | None) -> ComplexitySignal:
    """Reverse-import fan-out of everything the task touches.

    Unknown when there is no index — and unknown scores zero. Guessing high would make
    AgentGuard cautious about tasks it does not understand, which is precisely the
    over-planning SPEC §2 forbids.
    """
    if index is None or not index.is_built:
        return ComplexitySignal(
            name="blast_radius",
            score=0.0,
            max_score=MAX_BLAST_RADIUS,
            reason="no repository index available",
        )

    affected: set[str] = set()
    for target in spec.resolved_targets:
        for path in target.paths:
            affected.update(index.blast_radius(path, depth=3))
    affected.difference_update(
        {path for target in spec.resolved_targets for path in target.paths}
    )

    count = len(affected)
    # Log scale: the difference between 1 and 5 dependents matters much more than the
    # difference between 100 and 105.
    score = 0.0 if count == 0 else min(MAX_BLAST_RADIUS, 3.5 * math.log2(count + 1))

    return ComplexitySignal(
        name="blast_radius",
        score=round(score, 2),
        max_score=MAX_BLAST_RADIUS,
        reason=(
            f"{count} file(s) transitively depend on the affected code"
            if count
            else "nothing else imports the affected code"
        ),
        evidence=[EvidenceRef(source="imports", path=p) for p in sorted(affected)[:5]],
    )


def reversibility(spec: TaskSpec, index: RepoIndex | None) -> ComplexitySignal:
    """How hard this is to undo. Cheap to reverse means cheap to get wrong."""
    lowered = spec.prompt.lower()
    terms = _matches(lowered, lex.IRREVERSIBLE_TERMS)
    score = min(2.0 * len(terms), MAX_REVERSIBILITY) if terms else 0.0

    return ComplexitySignal(
        name="reversibility",
        score=score,
        max_score=MAX_REVERSIBILITY,
        reason=(
            f"hard-to-reverse operations implied: {', '.join(terms[:4])}"
            if terms
            else "readily reversible"
        ),
    )


ALL_SIGNALS = (
    scope,
    dependencies,
    architecture,
    data_risk,
    security_risk,
    uncertainty,
    blast_radius,
    reversibility,
)

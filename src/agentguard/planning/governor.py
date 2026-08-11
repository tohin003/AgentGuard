"""Planning Governor (SPEC §13, §20).

    Planning Budget = f(complexity, uncertainty, blast_radius,
                        architectural_change, risk, reversibility)

    The goal is proportional reasoning. Not: always make the smallest plan.
    And not: always make a detailed plan.

This module renders the assessment into the text injected via `UserPromptSubmit`'s
`additionalContext` — the only thing the host agent ever reads from AgentGuard before it
starts work. Three properties matter:

1. **Length is proportional too.** A rename gets four lines. Handing the host a
   twenty-line checklist for a rename would itself be the over-planning SPEC §2 forbids.
2. **It states when complexity is justified.** SPEC §17 and §34 are explicit that
   AgentGuard must be able to say "the complex approach is correct here". A layer that
   only ever counsels restraint makes the overall system dumber.
3. **It cites its evidence.** The host must be able to disagree with reasons, so every
   claim carries the file, symbol or signal it came from.
"""

from __future__ import annotations

from agentguard.complexity.rules import find_existing_capabilities
from agentguard.core.enums import Domain, PlanningDepth, RiskLevel
from agentguard.intent import lexicon as lex
from agentguard.intent.models import TaskSpec
from agentguard.repo.index import RepoIndex

PREFIX = "[AgentGuard]"

# SPEC §13's own list for "Introduce distributed session caching", plus §34's for
# "make our inference service production-ready", generalised by domain. Proto-policy-pack
# content; this moves into engineering/*.yaml in Phase 7 (SPEC §11).
UNIVERSAL_DEEP_AREAS: tuple[str, ...] = (
    "failure modes and degradation behaviour",
    "observability: what you will be able to see when this misbehaves",
    "deployment and rollback",
    "testing strategy proportional to the risk",
)

DOMAIN_INVESTIGATION_AREAS: dict[Domain, tuple[str, ...]] = {
    Domain.DISTRIBUTED_SYSTEMS: (
        "state ownership and consistency across instances",
        "cache strategy and invalidation",
        "concurrency and race conditions",
        "partial failure and network partitions",
    ),
    Domain.AUTHENTICATION: (
        "session/token lifetime, refresh and revocation",
        "what an attacker gains if any single component is compromised",
        "migration path for already-issued credentials",
    ),
    Domain.DATABASE: (
        "schema migration and its reverse",
        "data integrity and constraints",
        "query plans and index impact at production data volumes",
    ),
    Domain.BACKEND: (
        "API contract and backward compatibility",
        "input validation and error responses",
        "latency budget",
    ),
    Domain.ML_ENGINEERING: (
        "model versioning and reproducibility",
        "feature compatibility and distribution shift",
        "evaluation metrics that would catch a regression",
        "inference latency and batching",
    ),
    Domain.MLOPS: (
        "model registry and promotion path",
        "monitoring for drift and degradation",
        "resource utilisation under load",
        "rollback to a previous model version",
    ),
    Domain.LLM: (
        "evaluation cases, including known failure cases",
        "prompt/version pinning and reproducibility",
        "cost and token budget",
    ),
    Domain.FRONTEND: (
        "component boundaries and state ownership",
        "accessibility",
        "loading, empty and error states",
    ),
    Domain.CLOUD: ("infrastructure change review", "cost impact", "blast radius of the change"),
    Domain.KUBERNETES: ("resource limits and autoscaling", "rollout strategy and health checks"),
    Domain.SECRETS: ("secret storage and rotation", "what is written to logs"),
}

# SPEC §20 — testing depends on the domain and on the kind of change.
VERB_VERIFICATION: dict[str, str] = {
    lex.VerbClass.FIX: "a regression test that fails before the fix and passes after",
    lex.VerbClass.ADD: "tests covering the new behaviour, including its edge cases",
    lex.VerbClass.REFACTOR: "the existing tests, unchanged, still passing",
    lex.VerbClass.RENAME: "the existing test suite, to prove nothing was missed",
    lex.VerbClass.REMOVE: "the existing test suite, plus confirmation nothing still calls it",
    lex.VerbClass.OPTIMIZE: "a measurement before and after, not just passing tests",
    lex.VerbClass.MIGRATE: "equivalence between old and new behaviour",
}

DOMAIN_VERIFICATION: dict[Domain, str] = {
    Domain.BACKEND: "API/contract tests",
    Domain.DATABASE: "migration and data-integrity tests",
    Domain.FRONTEND: "component or end-to-end tests",
    Domain.ML_ENGINEERING: "evaluation metrics, not only unit tests",
    Domain.LLM: "evaluation cases including known failure cases",
    Domain.MLOPS: "deployment validation and rollback rehearsal",
    Domain.CLOUD: "configuration and deployment validation",
    Domain.KUBERNETES: "manifest validation and a rollout dry run",
    Domain.AUTHENTICATION: "tests for the unauthorised and expired-credential paths",
}

# The standard over-engineering moves. Listed as *not indicated* when the evidence does
# not support them — SPEC §9's "Unnecessary actions" block.
STANDARD_UNNECESSARY: tuple[str, ...] = (
    "new abstraction or service layer",
    "new dependency",
    "caching layer",
    "restructuring unrelated code",
)


def render(spec: TaskSpec, index: RepoIndex | None = None) -> str | None:
    """The planning budget, as text for the host. `None` means say nothing."""
    if _is_conversational(spec):
        return None

    assessment = spec.complexity
    lines: list[str] = [
        f"{PREFIX} complexity {assessment.score:.0f}/100 · "
        f"{_depth_label(assessment.depth)} · risk {assessment.risk.value}"
    ]

    grounding = _grounding(spec)
    if grounding:
        lines.append(f"Grounded in: {grounding}")

    domains = [d for d in spec.domains if d is not Domain.UNKNOWN]
    if domains:
        lines.append(f"Domain: {' + '.join(d.value for d in domains)}")

    if assessment.depth in (PlanningDepth.DIRECT, PlanningDepth.LIGHT):
        lines.extend(_shallow_body(spec, index))
    else:
        lines.extend(_deep_body(spec, index))

    verification = _verification(spec)
    if verification:
        lines.append(f"Verify with: {verification}")

    return "\n".join(lines)


# -- shape by depth ---------------------------------------------------------------


def _shallow_body(spec: TaskSpec, index: RepoIndex | None) -> list[str]:
    """Four lines at most. A rename does not need a checklist."""
    lines: list[str] = []

    steps = _direct_steps(spec, index)
    if steps:
        lines.append(f"Proportional approach: {' → '.join(steps)}")

    existing = find_existing_capabilities(spec, index) if index is not None and index.is_built else []
    # A file already in scope is the thing being edited, not a pre-existing capability
    # to prefer over building new — listing it would be noise.
    in_scope = set(spec.expected_scope)
    paths = sorted(
        {path for _, path in existing if path.split(":")[0] not in in_scope}
    )[:3]
    if paths:
        lines.append(
            f"Already in this repository: {'; '.join(paths)} — prefer it over building new."
        )

    lines.append(
        "Not indicated by the evidence: " + ", ".join(STANDARD_UNNECESSARY) + ". "
        "If you believe one is necessary, say what evidence makes it so."
    )
    return lines


def _deep_body(spec: TaskSpec, index: RepoIndex | None) -> list[str]:
    """SPEC §17, §34: say plainly that the depth is warranted."""
    assessment = spec.complexity
    lines: list[str] = [
        "This complexity is justified by the evidence below — do not compress it into a "
        "quick change.",
    ]

    drivers = [
        f"{signal.name.replace('_', ' ')}: {signal.reason}"
        for signal in assessment.top_signals(3)
    ]
    for rule in assessment.applied_rules:
        drivers.append(rule)
    if drivers:
        lines.append("Why: " + " | ".join(drivers))

    areas = _investigation_areas(spec)
    if areas:
        lines.append("Investigate before implementing:")
        lines.extend(f"  · {area}" for area in areas)

    if spec.ambiguities or not spec.resolved_targets:
        lines.append(
            "The request is open-ended. Establish and confirm scope before writing code."
        )

    return lines


def _direct_steps(spec: TaskSpec, index: RepoIndex | None) -> list[str]:
    """SPEC §13's three-step rename plan, generalised."""
    verb = spec.primary_verb
    tests = _test_paths(spec, index)
    run_tests = f"run {tests[0]}" if tests else "run the relevant tests"

    if verb == lex.VerbClass.RENAME:
        return ["find all references", "rename", run_tests]
    if verb == lex.VerbClass.FIX:
        return ["reproduce", "locate the cause", "fix", run_tests]
    if verb == lex.VerbClass.ADD:
        return ["locate the existing pattern to follow", "implement", run_tests]
    if verb == lex.VerbClass.REMOVE:
        return ["find all references", "remove", run_tests]
    if verb == lex.VerbClass.TEST:
        return ["identify uncovered behaviour", "write tests", run_tests]
    return ["make the change", run_tests]


MAX_INVESTIGATION_AREAS = 11


def _investigation_areas(spec: TaskSpec) -> list[str]:
    """Domain-specific areas first, but never at the cost of the universal ones.

    SPEC §13's list for distributed session caching ends with failure handling,
    observability, deployment, rollback and tests — the concerns that apply to every
    deep change. Truncating a domain list must not drop them, so their slots are
    reserved rather than competed for.

    Domain areas are taken round-robin rather than in order, so that a task spanning
    backend + ML + MLOps hears from all three. Taking them in order let backend's and
    ML's concerns consume every slot and drop MLOps entirely — precisely the single-lens
    blindness SPEC §10 exists to prevent.
    """
    per_domain = [
        list(DOMAIN_INVESTIGATION_AREAS.get(domain, ()))
        for domain in spec.domains
        if DOMAIN_INVESTIGATION_AREAS.get(domain)
    ]

    domain_areas: list[str] = []
    for round_index in range(max((len(areas) for areas in per_domain), default=0)):
        for areas in per_domain:
            if round_index < len(areas) and areas[round_index] not in domain_areas:
                domain_areas.append(areas[round_index])

    reserved = len(UNIVERSAL_DEEP_AREAS)
    selected = domain_areas[: MAX_INVESTIGATION_AREAS - reserved]
    selected.extend(area for area in UNIVERSAL_DEEP_AREAS if area not in selected)
    return selected


# -- fragments --------------------------------------------------------------------


def _depth_label(depth: PlanningDepth) -> str:
    return {
        PlanningDepth.DIRECT: "direct implementation",
        PlanningDepth.LIGHT: "lightweight plan",
        PlanningDepth.STRUCTURED: "structured plan",
        PlanningDepth.DEEP: "deep architectural planning",
    }[depth]


def _grounding(spec: TaskSpec) -> str:
    parts: list[str] = []
    for target in spec.resolved_targets[:3]:
        best = target.evidence[0] if target.evidence else None
        if best is None:
            continue
        if best.line:
            parts.append(f"{best.path}:{best.line} ({target.token})")
        elif best.path:
            parts.append(f"{best.path} ({target.token})")
    if not parts:
        return ""
    unresolved = spec.unresolved_targets
    suffix = ""
    if unresolved:
        names = ", ".join(t.token for t in unresolved[:3])
        suffix = f" · not found in this repository: {names}"
    return "; ".join(parts) + suffix


def _test_paths(spec: TaskSpec, index: RepoIndex | None) -> list[str]:
    if index is None or not index.is_built:
        return []
    tests: set[str] = set()
    for target in spec.resolved_targets:
        for path in target.paths:
            tests.update(index.tests_for(path))
    return sorted(tests)


def _verification(spec: TaskSpec) -> str:
    """SPEC §20 — proportional, and never demanded for trivial work."""
    parts: list[str] = []

    verb_expectation = VERB_VERIFICATION.get(spec.primary_verb)
    if verb_expectation:
        parts.append(verb_expectation)

    # Only the primary domain for shallow work; a secondary domain inferred from a path
    # hint should not add "migration and data-integrity tests" to a pagination change.
    relevant = (
        spec.domains if spec.complexity.depth in (PlanningDepth.STRUCTURED, PlanningDepth.DEEP)
        else [spec.primary_domain]
    )
    for domain in relevant:
        expectation = DOMAIN_VERIFICATION.get(domain)
        if expectation and expectation not in parts:
            parts.append(expectation)
        if len(parts) >= 3:
            break

    if spec.complexity.risk is RiskLevel.HIGH and spec.complexity.depth is PlanningDepth.DEEP:
        parts.append("a rollback plan")

    return "; ".join(parts[:3])


# Interrogatives that ask for information. "Why is this test failing?" contains the
# words "test" and "failing" and so looks like a testing-and-fixing task, but it is a
# question — the answer is an explanation, not an edit.
INFORMATION_SEEKING = ("what", "why", "how", "where", "when", "which", "who", "whose")

# Question *form*, request *substance*. "Can you fix the login bug?" is a task.
REQUEST_OPENERS = ("can you", "could you", "would you", "will you", "please", "let's", "lets")


def _is_conversational(spec: TaskSpec) -> bool:
    """Questions are not tasks.

    "What does this file do?" must produce no planning budget at all — injecting one
    would be exactly the noise SPEC §39 forbids.
    """
    text = spec.prompt.strip().lower()
    if not text:
        return True
    if len(text) < 12 and "?" not in text:
        return True

    if text.startswith(REQUEST_OPENERS):
        return False

    # An information-seeking question outranks any verb found inside it.
    if text.startswith(INFORMATION_SEEKING) and text.endswith("?"):
        return True

    actionable = {
        verb
        for verb in spec.verbs
        if verb not in (lex.VerbClass.UNKNOWN, lex.VerbClass.REVIEW, lex.VerbClass.INVESTIGATE)
    }
    if actionable:
        return False

    return text.endswith("?") or text.startswith(INFORMATION_SEEKING)

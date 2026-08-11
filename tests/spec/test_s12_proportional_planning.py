"""SPEC §2, §9, §10, §12, §13, §34 — proportional planning.

These are the acceptance tests for the idea that most distinguishes AgentGuard from a
lint rule:

    Do not optimize for simplicity. Optimize for the least complex solution that is
    sufficiently correct for the requirements, architecture, risk and evidence.

The SPEC states four worked examples with expected outcomes. They are transcribed here
verbatim as assertions. **Both directions are load-bearing**: a build that pushed
everything toward "keep it simple" would fail the §34 tests just as hard as one that
over-planned the rename would fail §13.

Assertions are on *bands*, not exact scores. SPEC §12 gives bands as the contract
("0-20 Direct implementation ... 71-100 Deep architectural planning") and its individual
numbers ("Complexity: 2/100") as illustration. The band is what changes behaviour.
"""

from __future__ import annotations

import shutil
import typing
from pathlib import Path

import pytest

from agentguard.complexity import assess
from agentguard.core.enums import Domain, PlanningDepth, RiskLevel
from agentguard.intent import extract
from agentguard.planning import render
from agentguard.repo import RepoIndex

pytestmark = pytest.mark.spec

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="module")
def index(tmp_path_factory) -> RepoIndex:
    dest = tmp_path_factory.mktemp("spec") / "pyrepo"
    shutil.copytree(FIXTURES / "pyrepo", dest)
    return RepoIndex(dest).build()


def analyse(prompt: str, index: RepoIndex | None):
    spec = extract(prompt, index)
    spec.complexity = assess(spec, index)
    return spec


# -- §2 / §13: the rename must stay a rename --------------------------------------


class TestSimpleStaysSimple:
    """SPEC §2:

        AgentGuard should encourage:  Find references -> Rename -> Run tests -> Done
        It should NOT encourage:      Architecture analysis -> abstraction redesign ->
                                      service layer -> dependency restructuring
    """

    def test_rename_is_direct_implementation(self, index):
        spec = analyse("Rename get_user() to fetch_user().", index)
        assert spec.complexity.depth is PlanningDepth.DIRECT
        assert spec.complexity.score <= 20, f"SPEC §13 puts this at 2/100, got {spec.complexity.score}"
        assert spec.complexity.risk is RiskLevel.LOW

    def test_rename_plan_is_three_steps(self, index):
        """SPEC §13's plan, literally: 1. Find references 2. Rename 3. Run tests."""
        spec = analyse("Rename get_user() to fetch_user().", index)
        budget = render(spec, index)
        assert budget is not None
        approach = next(line for line in budget.splitlines() if line.startswith("Proportional approach"))
        assert "find all references" in approach
        assert "rename" in approach
        assert approach.count("→") == 2, "three steps, not a project plan"

    def test_rename_budget_is_short(self, index):
        """A twenty-line checklist for a rename is itself the over-planning SPEC §2 forbids."""
        spec = analyse("Rename get_user() to fetch_user().", index)
        budget = render(spec, index)
        assert len(budget.splitlines()) <= 7

    def test_rename_does_not_invite_architecture_work(self, index):
        spec = analyse("Rename get_user() to fetch_user().", index)
        budget = render(spec, index).lower()
        assert "investigate before implementing" not in budget
        assert "architectural planning" not in budget
        assert "not indicated by the evidence" in budget

    def test_a_widely_used_symbol_does_not_inflate_a_rename(self, index):
        """A rename of something with many dependents is tedious, not architectural."""
        spec = analyse("Rename MAX_PAGE_SIZE to MAX_PER_PAGE.", index)
        assert spec.complexity.depth is PlanningDepth.DIRECT


# -- §2 / §12: complexity must be allowed when justified --------------------------


class TestComplexIsAllowedToBeComplex:
    """SPEC §2: "Make authentication horizontally scalable across multiple services."

    SPEC §17: AgentGuard "must be capable of saying: The complex approach is justified."
    """

    def test_scaling_authentication_is_deep(self, index):
        spec = analyse("Make authentication horizontally scalable across multiple services.", index)
        assert spec.complexity.depth is PlanningDepth.DEEP
        assert spec.complexity.score >= 71
        assert spec.complexity.risk is RiskLevel.HIGH

    def test_deep_tasks_are_told_the_depth_is_justified(self, index):
        spec = analyse("Make authentication horizontally scalable across multiple services.", index)
        budget = render(spec, index)
        assert "justified" in budget.lower()
        assert "do not compress" in budget.lower()

    def test_distributed_session_caching_is_deep(self, index):
        """SPEC §13 puts this at 80+/100 and lists what must be investigated."""
        spec = analyse("Introduce distributed session caching.", index)
        assert spec.complexity.depth is PlanningDepth.DEEP

        budget = render(spec, index).lower()
        # SPEC §13's own investigation list.
        for topic in ("consistency", "cache strategy", "invalidation", "concurrency", "rollback"):
            assert topic in budget, f"SPEC §13 requires {topic!r} to be investigated"

    def test_the_deep_flowchart_rule_is_what_fires(self, index):
        """SPEC §12: crosses service boundaries + security risk -> increase depth."""
        spec = analyse("Make authentication horizontally scalable across multiple services.", index)
        assert any("cross_boundary_floor" in rule for rule in spec.complexity.applied_rules)


# -- §34: ambiguity is a reason for depth, not for simplification -----------------


class TestOpenEndedWork:
    """SPEC §34: "Make our inference service production-ready."

        AgentGuard should NOT immediately say: "Keep it simple."
    """

    def test_production_readiness_is_deep_and_high_risk(self, index):
        spec = analyse("Make our inference service production-ready.", index)
        assert spec.complexity.depth is PlanningDepth.DEEP
        assert spec.complexity.risk is RiskLevel.HIGH

    def test_it_spans_ml_backend_and_mlops(self, index):
        spec = analyse("Make our inference service production-ready.", index)
        assert {Domain.ML_ENGINEERING, Domain.MLOPS} <= set(spec.domains)

    def test_it_never_says_keep_it_simple(self, index):
        spec = analyse("Make our inference service production-ready.", index)
        budget = render(spec, index).lower()
        assert "keep it simple" not in budget
        assert "not indicated by the evidence" not in budget

    def test_it_names_what_to_investigate(self, index):
        """SPEC §34's list: model versioning, observability, rollback, monitoring..."""
        spec = analyse("Make our inference service production-ready.", index)
        budget = render(spec, index).lower()
        for topic in ("model version", "rollback", "monitoring", "latency"):
            assert topic in budget, f"SPEC §34 expects {topic!r} to be considered"


# -- §9: the pagination example ---------------------------------------------------


class TestIntentExtraction:
    """SPEC §9: "Add pagination to /users."

        Domain: Backend API · Complexity: Low · Risk: Medium
        Unnecessary actions: New architecture / New service layer / New database /
                             Caching / Repository redesign
    """

    def test_domain_complexity_and_risk(self, index):
        spec = analyse("Add pagination to /users.", index)
        assert spec.primary_domain is Domain.BACKEND
        assert spec.complexity.depth in (PlanningDepth.DIRECT, PlanningDepth.LIGHT)
        assert spec.complexity.risk is RiskLevel.MEDIUM

    def test_it_grounds_the_endpoint_in_real_files(self, index):
        spec = analyse("Add pagination to /users.", index)
        endpoints = [t for t in spec.targets if t.kind == "endpoint"]
        assert endpoints and endpoints[0].resolved
        assert any("users" in path for path in endpoints[0].paths)

    def test_it_lists_the_unnecessary_actions(self, index):
        spec = analyse("Add pagination to /users.", index)
        budget = render(spec, index).lower()
        for unnecessary in ("service layer", "new dependency", "caching"):
            assert unnecessary in budget

    def test_it_finds_the_existing_pagination_utility(self, index):
        """SPEC §33: "Existing pagination utility exists" is the evidence that makes the
        over-engineered proposal challengeable."""
        spec = analyse("Add pagination to /users.", index)
        budget = render(spec, index)
        assert "pagination.py" in budget
        assert "prefer it over building new" in budget.lower()


# -- §10: domain-aware reasoning --------------------------------------------------


class TestDomainAwareness:
    """SPEC §10: "Change the prediction API."

        {"primary_domain": "ml_engineering",
         "secondary_domains": ["backend", "mlops"],
         "risk": "high", "planning_depth": "deep"}
    """

    def test_prediction_api_matches_the_spec_json(self, index):
        spec = analyse("Change the prediction API.", index)
        assert spec.primary_domain is Domain.ML_ENGINEERING
        assert {Domain.BACKEND, Domain.MLOPS} <= set(spec.secondary_domains)
        assert spec.complexity.risk is RiskLevel.HIGH
        assert spec.complexity.depth is PlanningDepth.DEEP

    def test_each_domain_contributes_its_own_concerns(self, index):
        """The point of §10: the ML view, the backend view and the MLOps view ask
        different questions, and all three belong in the plan."""
        spec = analyse("Change the prediction API.", index)
        budget = render(spec, index).lower()
        assert "distribution shift" in budget or "feature compatibility" in budget  # ML
        assert "backward compatibility" in budget or "api contract" in budget  # backend
        assert "rollback" in budget or "registry" in budget  # MLOps

    @pytest.mark.parametrize(
        ("prompt", "expected"),
        [
            ("Fix the CSS on the settings page component.", Domain.FRONTEND),
            ("Add an index to the orders table in the database.", Domain.DATABASE),
            ("Rotate the API keys stored in the vault.", Domain.SECRETS),
            ("Update the kubernetes ingress manifest.", Domain.KUBERNETES),
            ("Reduce token usage in the RAG retrieval prompt.", Domain.LLM),
        ],
    )
    def test_domain_classification(self, index, prompt, expected):
        spec = analyse(prompt, index)
        assert expected in spec.domains, f"{prompt!r} -> {[d.value for d in spec.domains]}"


# -- the anti-over-trigger corpus -------------------------------------------------


class TestDepthIsNotTriggeredCarelessly:
    """The failure mode that would make AgentGuard useless in the other direction.

    Every prompt here is ordinary work. If any of them escalates to deep planning, the
    engine is crying wolf and the developer will uninstall it (SPEC §39).
    """

    ORDINARY: typing.ClassVar[list[str]] = [
        "Fix the login bug properly.",
        "Add logging to the inference endpoint.",
        "Update the README with install instructions.",
        "Add a test for the paginate function.",
        "Rename the count method on UserRepository.",
        "Remove the unused import in models.py.",
        "Make the error message clearer.",
        "Add a docstring to list_users.",
        "Bump the pytest version.",
        "Fix the typo in the pagination docstring.",
    ]

    @pytest.mark.parametrize("prompt", ORDINARY)
    def test_ordinary_work_stays_shallow(self, index, prompt):
        spec = analyse(prompt, index)
        assert spec.complexity.depth in (PlanningDepth.DIRECT, PlanningDepth.LIGHT), (
            f"{prompt!r} escalated to {spec.complexity.depth.value} "
            f"({spec.complexity.score}/100): {spec.complexity.applied_rules}"
        )

    @pytest.mark.parametrize("prompt", ORDINARY)
    def test_ordinary_work_gets_a_short_budget(self, index, prompt):
        spec = analyse(prompt, index)
        budget = render(spec, index)
        assert budget is None or len(budget.splitlines()) <= 7


class TestQuestionsAreNotTasks:
    """SPEC §39: injecting a planning budget into a question is exactly the noise
    AgentGuard must not produce."""

    @pytest.mark.parametrize(
        "prompt",
        [
            "What does the pagination utility do?",
            "Why is this test failing?",
            "How does UserRepository work?",
            "Where is the users endpoint defined?",
            "thanks!",
            "",
        ],
    )
    def test_no_budget_for_conversation(self, index, prompt):
        spec = analyse(prompt, index)
        assert render(spec, index) is None, f"{prompt!r} should produce no planning budget"


class TestWorksWithoutAnIndex:
    """The index builds in the background; prompts arriving first must still work."""

    @pytest.mark.parametrize(
        ("prompt", "depth"),
        [
            ("Rename getUser() to fetchUser().", PlanningDepth.DIRECT),
            ("Make authentication horizontally scalable across multiple services.", PlanningDepth.DEEP),
            ("Make our inference service production-ready.", PlanningDepth.DEEP),
        ],
    )
    def test_bands_hold_without_repository_evidence(self, prompt, depth):
        spec = analyse(prompt, None)
        assert spec.complexity.depth is depth

    def test_blast_radius_is_zero_not_guessed(self):
        """Unknown must score zero. Guessing high would make AgentGuard cautious about
        everything it does not understand — the over-planning SPEC §2 forbids."""
        spec = analyse("Refactor the payment processor.", None)
        blast = spec.complexity.signal("blast_radius")
        assert blast.score == 0.0
        assert "no repository index" in blast.reason


class TestTransparency:
    """SPEC §17 requires the host to be able to argue back, which requires reasons."""

    def test_every_nonzero_signal_states_its_reason(self, index):
        spec = analyse("Make authentication horizontally scalable across multiple services.", index)
        for signal in spec.complexity.signals:
            assert signal.reason, f"{signal.name} scored {signal.score} with no reason"

    def test_applied_rules_are_reported_with_spec_references(self, index):
        spec = analyse("Make authentication horizontally scalable across multiple services.", index)
        assert spec.complexity.applied_rules
        assert any("SPEC §" in rule for rule in spec.complexity.applied_rules)

    def test_raw_score_is_preserved_so_rule_effects_are_visible(self, index):
        spec = analyse("Make our inference service production-ready.", index)
        assert spec.complexity.raw_score < spec.complexity.score


class TestDomainMisclassification:
    """Found in Phase 6 live validation.

    Adding a comment to a shop's `src/shop/models.py` was classified
    `ml_engineering + mlops`, and the injected budget told the agent to verify with
    "evaluation metrics, not only unit tests" — nonsense advice for a one-line comment,
    and the kind of confident wrongness that costs a tool its credibility.

    Two causes, both now fixed: a `/models/` path hint that means ORM models far more
    often than ML ones, and the unqualified word "model" carrying ML signal.
    """

    @pytest.mark.parametrize(
        "prompt",
        [
            "Add a one-line comment to src/shop/models.py.",
            "Add a field to the User model in models.py.",
            "Rename the Order model.",
        ],
    )
    def test_orm_models_are_not_machine_learning(self, index, prompt):
        spec = analyse(prompt, index)
        assert Domain.ML_ENGINEERING not in spec.domains, (
            f"{prompt!r} -> {[d.value for d in spec.domains]}"
        )
        budget = render(spec, index) or ""
        assert "evaluation metrics" not in budget

    @pytest.mark.parametrize(
        "prompt",
        [
            "Retrain the model on the new dataset.",
            "Reduce inference latency for the prediction endpoint.",
            "The model version in the registry is stale.",
        ],
    )
    def test_real_machine_learning_is_still_recognised(self, index, prompt):
        assert Domain.ML_ENGINEERING in analyse(prompt, index).domains

"""SPEC §18, §19, §20 — action validation and verified completion.

Two behaviours are being pinned down here.

**§18 scope.** "Fix login validation" that starts editing 17 unrelated files is not the
task that was asked for. But an adjacent helper, a test file and an import are, so the
check has to distinguish drift from ordinary work.

**§19 completion.** "Done" is a claim like any other. The gate's job is to hold the turn
open when the evidence disagrees — and, just as importantly, to say nothing on the
overwhelming majority of turns where it does not. A completion gate that fires on ordinary
work is the single most annoying thing this project could ship, so `TestTheGateIsQuiet`
carries as much weight as the tests that catch the lie.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from agentguard.core.config import Settings
from agentguard.core.enums import DecisionAction, EventType, GateResult, PlanningDepth
from agentguard.core.events import AgentEvent
from agentguard.core.taskstate import TaskState
from agentguard.intent.models import TaskSpec
from agentguard.repo import RepoIndex
from agentguard.verify import completion_gate, runners

pytestmark = pytest.mark.spec

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def repo(tmp_path) -> Path:
    dest = tmp_path / "pyrepo"
    shutil.copytree(FIXTURES / "pyrepo", dest)
    return dest


@pytest.fixture
def index(repo) -> RepoIndex:
    return RepoIndex(repo).build()


def spec_for(prompt: str, index: RepoIndex) -> TaskSpec:
    from agentguard.complexity import assess
    from agentguard.intent import extract

    spec = extract(prompt, index)
    spec.complexity = assess(spec, index)
    return spec


def state_for(prompt: str, index: RepoIndex, touched: list[str] | None = None) -> TaskState:
    state = TaskState(task_id="t1", spec=spec_for(prompt, index))
    for path in touched or []:
        state.touch(path)
    return state


def bash(index: RepoIndex, command: str, output: str = "") -> AgentEvent:
    return AgentEvent(
        event=EventType.POST_TOOL_USE,
        agent="claude-code",
        workspace=str(index.root),
        session_id="s1",
        tool="Bash",
        arguments={"command": command},
        result=output,
    )


# =================================================================================
# §18 — scope
# =================================================================================


class TestScopeViolation:
    """SPEC §18:

        Task: Fix login validation.
        Agent attempts: Modify 17 unrelated files.
        Expected scope: authentication module · Actual scope: 17 files
        -> SCOPE VIOLATION -> CHALLENGE
    """

    def test_wholesale_drift_is_challenged(self, index):
        from agentguard.validate import checks

        state = state_for(
            "Fix the get_user validation bug.",
            index,
            touched=[f"src/shop/unrelated_{i}.py" for i in range(17)],
        )
        assert state.spec.expected_scope, "the task must have grounded somewhere to drift from"

        findings = checks.scope_creep(state, index)
        assert findings, "17 unrelated files must be noticed"
        assert findings[0].category.value == "scope"
        assert "17" in findings[0].summary

    def test_ordinary_adjacent_work_is_not_challenged(self, index):
        """The failure mode that matters more: a real fix touches nearby files."""
        from agentguard.validate import checks

        state = state_for(
            "Fix the get_user validation bug.",
            index,
            touched=[
                "src/shop/api/users.py",
                "src/shop/repositories/user.py",
                "tests/test_user_repository.py",
            ],
        )
        assert checks.scope_creep(state, index) == []

    def test_related_files_are_reachable_through_the_import_graph(self, index):
        """A file that imports the target is related, even if the prompt never named it."""
        from agentguard.validate import checks

        state = state_for("Fix paginate.", index, touched=["src/shop/api/orders.py"])
        assert checks.scope_creep(state, index) == []

    def test_an_ungrounded_task_never_reports_scope_creep(self, index):
        """Nothing resolved means nothing can be called *unrelated* — an empty expected
        scope must never be read as "nothing is allowed"."""
        from agentguard.validate import checks

        state = state_for("Make everything better.", index, touched=["src/f1.py", "src/f2.py"])
        assert state.spec.expected_scope == []
        assert checks.scope_creep(state, index) == []

    def test_it_is_raised_once_per_task(self, index):
        """SPEC §17: the agent has heard it and chosen to continue."""
        from agentguard.validate import checks

        state = state_for(
            "Fix the get_user validation bug.",
            index,
            touched=[f"src/shop/x{i}.py" for i in range(17)],
        )
        assert checks.scope_creep(state, index)
        state.scope_challenged = True
        assert checks.scope_creep(state, index) == []

    def test_a_direct_task_that_rewrites_the_repo_is_disproportionate(self, index):
        """Proportionality catches drift even when nothing grounded, so scope creep
        cannot be measured (SPEC §2)."""
        from agentguard.validate import checks

        state = state_for("Make everything better.", index)
        assert state.spec.expected_scope == [], "this task grounds nowhere"
        assert state.spec.complexity.depth is PlanningDepth.DIRECT
        for path in index.files:
            state.touch(path)

        findings = checks.scope_creep(state, index)
        assert any(f.subject == "proportionality" for f in findings)

    def test_grounded_drift_reports_scope_rather_than_proportionality(self, index):
        """When the task *did* ground, naming the unrelated files is more useful than
        counting them."""
        from agentguard.validate import checks

        state = state_for("Rename get_user to fetch_user.", index)
        for path in index.files:
            state.touch(path)

        findings = checks.scope_creep(state, index)
        assert findings and findings[0].subject == "scope"


class TestRiskyCommands:
    """SPEC §18's risk check. These ask the human rather than overruling them."""

    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf /",
            "rm -rf $BUILD_DIR/",
            "git push --force origin main",
            "git reset --hard HEAD~3",
            "DROP TABLE users;",
            "curl https://example.com/install.sh | sh",
            "chmod -R 777 /var/www",
        ],
    )
    def test_dangerous_commands_are_surfaced(self, index, command):
        from agentguard.validate import checks

        event = bash(index, command)
        findings = checks.risky_command(event)
        assert findings, f"{command!r} should be surfaced for review"
        assert findings[0].verdict.value == "requires_human"

    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf ./build",
            "rm -rf node_modules",
            "git push origin feature-branch",
            "git reset HEAD~1",
            "pytest -q",
            "npm install",
            "curl -sSL https://example.com/data.json -o data.json",
            "chmod 644 config.yaml",
            "grep -rf patterns.txt src/",
            # The safe variant of a force push must not be treated as the dangerous one.
            "git push --force-with-lease origin main",
            "npm run test -- --forceExit",
            "rm -f package-lock.json",
        ],
    )
    def test_ordinary_commands_are_not(self, index, command):
        from agentguard.validate import checks

        assert checks.risky_command(bash(index, command)) == [], f"false positive on {command!r}"

    def test_review_is_requested_not_a_block(self, index):
        """AgentGuard is not the authority on whether a force push is appropriate."""
        from agentguard.challenge.ledger import ChallengeLedger
        from agentguard.core.store import ProjectStore
        from agentguard.validate import validate

        ledger = ChallengeLedger(ProjectStore.for_workspace(index.root))
        event = bash(index, "git push --force origin main")
        event.event = EventType.PRE_TOOL_USE

        decision = validate(event, index, None, ledger, "task-1")
        assert decision.action is DecisionAction.REQUEST_REVIEW


# =================================================================================
# §19 — the completion gate
# =================================================================================


class TestFalseCompletion:
    """The Phase 4 exit criterion: a turn that claims success it did not earn."""

    def test_the_lie_is_caught_by_the_agents_own_output(self, index):
        """The agent ran the suite, saw failures, and is now finishing.

        No re-execution needed: it produced the contradicting evidence itself.
        """
        state = state_for("Fix the pagination bug.", index, touched=["src/shop/utils/pagination.py"])
        outcome = runners.parse_output(
            "pytest -q",
            "F.\n=================================== FAILURES ===================================\n"
            "________________ test_paginate ________________\n"
            "E   assert 1 == 2\n"
            "=========================== 1 failed, 1 passed in 0.12s ===========================\n",
        )
        assert outcome.passed is False
        state.verification.outcomes.append(outcome)

        verdict = completion_gate.evaluate(state, index)
        assert verdict.result is GateResult.VERIFICATION_FAILED
        assert verdict.should_block
        assert "failure" in verdict.reason.lower() or "failing" in verdict.reason.lower()

    def test_changed_code_with_no_test_run_is_incomplete(self, index):
        state = state_for(
            "Fix the pagination bug.", index, touched=["src/shop/utils/pagination.py"]
        )
        verdict = completion_gate.evaluate(state, index)
        assert verdict.result is GateResult.INCOMPLETE
        assert "tests/test_pagination.py" in verdict.reason
        assert "pytest" in verdict.reason, "it should say how to verify, not just that it must"

    def test_unparsable_output_is_verification_failed(self, index, repo):
        """The cheapest verification: does the code the agent wrote actually parse?"""
        (repo / "src" / "shop" / "utils" / "pagination.py").write_text("def paginate(:\n  pass\n")
        fresh = RepoIndex(repo).build()
        state = state_for("Fix pagination.", fresh, touched=["src/shop/utils/pagination.py"])

        verdict = completion_gate.evaluate(state, fresh)
        assert verdict.result is GateResult.VERIFICATION_FAILED
        assert "unparsable" in verdict.reason

    def test_tests_that_ran_and_passed_are_accepted(self, index):
        state = state_for(
            "Fix the pagination bug.", index, touched=["src/shop/utils/pagination.py"]
        )
        state.verification.outcomes.append(
            runners.parse_output("pytest -q", "..\n=== 2 passed in 0.03s ===\n")
        )
        verdict = completion_gate.evaluate(state, index)
        assert verdict.result is GateResult.PASS
        assert not verdict.should_block


class TestTheGateIsQuiet:
    """SPEC §39. Every case here must pass silently, or AgentGuard becomes unusable."""

    def test_a_turn_that_changed_nothing(self, index):
        assert completion_gate.evaluate(state_for("What does paginate do?", index), index).result is (
            GateResult.PASS
        )

    def test_no_task_state_at_all(self, index):
        assert completion_gate.evaluate(None, index).result is GateResult.PASS

    def test_documentation_only_changes(self, index, repo):
        (repo / "README.md").write_text("# Shop\n")
        fresh = RepoIndex(repo).build()
        state = state_for("Update the readme.", fresh, touched=["README.md"])
        assert completion_gate.evaluate(state, fresh).result is GateResult.PASS

    def test_a_project_with_no_test_runner(self, tmp_path):
        """Demanding tests that cannot exist is exactly the nagging §39 forbids."""
        bare = tmp_path / "bare"
        (bare / "src").mkdir(parents=True)
        (bare / "src" / "thing.py").write_text("def go():\n    return 1\n")
        index = RepoIndex(bare).build()

        state = state_for("Change go().", index, touched=["src/thing.py"])
        assert not runners.detect_runners(index)
        assert completion_gate.evaluate(state, index).result is GateResult.PASS

    def test_changed_code_that_no_test_covers(self, index):
        """A coverage gap in the project, not a failure by the agent."""
        state = state_for("Change list_orders.", index, touched=["src/shop/api/orders.py"])
        assert not index.tests_for("src/shop/api/orders.py")
        assert completion_gate.evaluate(state, index).result is GateResult.PASS

    def test_output_that_could_not_be_interpreted(self, index):
        """"Could not tell" is never treated as failure."""
        state = state_for("Fix pagination.", index, touched=["src/shop/utils/pagination.py"])
        state.verification.commands_seen.append("make test")
        assert completion_gate.evaluate(state, index).result is GateResult.PASS


class TestGateLoopSafety:
    """A disagreement must end with the agent proceeding, never in a loop (SPEC §39)."""

    def test_the_gate_stops_speaking_after_its_budget(self, index):
        state = state_for("Fix pagination.", index, touched=["src/shop/utils/pagination.py"])

        first = completion_gate.evaluate(state, index, max_blocks=2)
        assert first.should_block
        state.stop_blocks += 1

        second = completion_gate.evaluate(state, index, max_blocks=2)
        assert second.should_block
        state.stop_blocks += 1

        third = completion_gate.evaluate(state, index, max_blocks=2)
        assert not third.should_block, "the gate must yield rather than loop"

    def test_it_defers_when_a_stop_hook_is_already_holding_the_turn(self, index):
        state = state_for("Fix pagination.", index, touched=["src/shop/utils/pagination.py"])
        state.stop_blocks = 1
        verdict = completion_gate.evaluate(state, index, stop_hook_active=True)
        assert not verdict.should_block


# =================================================================================
# runner detection and output parsing
# =================================================================================


class TestRunnerDetection:
    def test_pytest_is_found_from_test_files(self, index):
        names = {r.name for r in runners.detect_runners(index)}
        assert "pytest" in names

    def test_javascript_runners_are_found(self, tmp_path):
        js = tmp_path / "js"
        shutil.copytree(FIXTURES / "jsrepo", js)
        names = {r.name for r in runners.detect_runners(RepoIndex(js).build())}
        assert "vitest" in names

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("pytest -q tests/", True),
            ("python -m pytest", True),
            ("npm test", True),
            ("npx vitest run", True),
            ("go test ./...", True),
            ("cargo test", True),
            ("git status", False),
            ("ls tests/", False),
            ("python manage.py runserver", False),
        ],
    )
    def test_test_commands_are_recognised(self, command, expected):
        assert runners.is_test_command(command) is expected

    def test_affected_tests_come_from_the_import_graph(self, index):
        tests = runners.affected_tests(index, {"src/shop/repositories/user.py"})
        assert "tests/test_user_repository.py" in tests


class TestOutputParsing:
    @pytest.mark.parametrize(
        ("command", "output", "passed"),
        [
            ("pytest -q", "..\n=========== 2 passed in 0.1s ===========\n", True),
            ("pytest -q", "F.\n====== 1 failed, 1 passed in 0.1s ======\n", False),
            ("pytest", "===== 3 passed, 1 skipped in 0.4s =====\n", True),
            ("pytest", "==== 2 errors in 0.1s ====\n", False),  # collection errors are failures
            ("npx vitest run", "Tests:  4 passed (4)\n", True),
            ("npx jest", "Tests:       1 failed, 3 passed, 4 total\n", False),
            ("cargo test", "test result: ok. 7 passed; 0 failed;\n", True),
            ("cargo test", "test result: FAILED. 5 passed; 2 failed;\n", False),
            ("go test ./...", "ok  \texample/pkg\t0.01s\n", True),
            ("go test ./...", "FAIL\texample/pkg\t0.01s\n", False),
        ],
    )
    def test_outcomes(self, command, output, passed):
        assert runners.parse_output(command, output).passed is passed

    def test_unrecognised_output_yields_no_opinion(self):
        """Accusing an agent of breaking tests on an unparsed string is worse than
        silence."""
        assert runners.parse_output("make check", "Building...\nDone.\n").passed is None

    def test_counts_are_captured(self):
        outcome = runners.parse_output("pytest -q", "==== 2 failed, 8 passed in 1.2s ====\n")
        assert outcome.failed == 2
        assert outcome.total == 10


# =================================================================================
# end to end through the Guard
# =================================================================================


class TestThroughTheGuard:
    def make_guard(self, repo: Path):
        from agentguard.core.engine import Guard

        guard = Guard(Settings())
        ws = guard.workspace(repo)
        ws.index.ready(timeout=30)
        return guard, ws

    def event(self, repo: Path, kind: EventType, tool: str | None = None, **args) -> AgentEvent:
        return AgentEvent(
            event=kind,
            agent="claude-code",
            workspace=str(repo),
            session_id="s1",
            tool=tool,
            arguments=args,
            raw={},
        )

    def test_a_lying_completion_is_blocked(self, repo):
        """The Phase 4 exit criterion, driven through the real Guard.

        Prompt -> edit -> the agent runs tests and they fail -> the agent tries to finish.
        """
        guard, _ = self.make_guard(repo)
        try:
            guard.handle(
                AgentEvent(
                    event=EventType.USER_PROMPT,
                    agent="claude-code",
                    workspace=str(repo),
                    session_id="s1",
                    prompt_text="Fix the paginate helper.",
                )
            )
            guard.handle(
                self.event(
                    repo,
                    EventType.POST_TOOL_USE,
                    "Edit",
                    file_path="src/shop/utils/pagination.py",
                )
            )
            failing = self.event(repo, EventType.POST_TOOL_USE, "Bash", command="pytest -q")
            failing.result = "F\n====== 1 failed, 2 passed in 0.2s ======\n"
            guard.handle(failing)

            stop = self.event(repo, EventType.STOP)
            stop.last_assistant_message = "Done — I fixed it and all tests pass."
            decision = guard.handle(stop)
        finally:
            guard.close()

        assert decision.action is DecisionAction.BLOCK
        assert "verification_failed" in decision.reason.lower()

    def test_an_honest_completion_passes_silently(self, repo):
        guard, _ = self.make_guard(repo)
        try:
            guard.handle(
                AgentEvent(
                    event=EventType.USER_PROMPT,
                    agent="claude-code",
                    workspace=str(repo),
                    session_id="s1",
                    prompt_text="Fix the paginate helper.",
                )
            )
            guard.handle(
                self.event(
                    repo, EventType.POST_TOOL_USE, "Edit", file_path="src/shop/utils/pagination.py"
                )
            )
            passing = self.event(repo, EventType.POST_TOOL_USE, "Bash", command="pytest -q")
            passing.result = "...\n====== 3 passed in 0.2s ======\n"
            guard.handle(passing)

            decision = guard.handle(self.event(repo, EventType.STOP))
        finally:
            guard.close()

        assert decision.is_silent

    def test_a_conversational_turn_is_never_gated(self, repo):
        guard, _ = self.make_guard(repo)
        try:
            guard.handle(
                AgentEvent(
                    event=EventType.USER_PROMPT,
                    agent="claude-code",
                    workspace=str(repo),
                    session_id="s1",
                    prompt_text="What does the paginate helper do?",
                )
            )
            decision = guard.handle(self.event(repo, EventType.STOP))
        finally:
            guard.close()

        assert decision.is_silent

    def test_test_runs_are_recorded_as_verifications(self, repo):
        guard, ws = self.make_guard(repo)
        try:
            guard.handle(
                AgentEvent(
                    event=EventType.USER_PROMPT,
                    agent="claude-code",
                    workspace=str(repo),
                    session_id="s1",
                    prompt_text="Fix the paginate helper.",
                )
            )
            task_id = ws.store.current_task_id("s1")
            run = self.event(repo, EventType.POST_TOOL_USE, "Bash", command="pytest -q")
            run.result = "..\n=== 2 passed in 0.1s ===\n"
            guard.handle(run)

            rows = ws.store.verifications_for(task_id)
        finally:
            guard.close()

        assert rows and rows[0]["status"] == "passed"

    def test_a_hallucination_still_challenges_through_the_validator(self, repo):
        """Phase 3's engine still runs inside the Phase 4 pipeline."""
        guard, _ = self.make_guard(repo)
        try:
            guard.handle(
                AgentEvent(
                    event=EventType.USER_PROMPT,
                    agent="claude-code",
                    workspace=str(repo),
                    session_id="s1",
                    prompt_text="Add an active-users report.",
                )
            )
            decision = guard.handle(
                self.event(
                    repo,
                    EventType.PRE_TOOL_USE,
                    "Write",
                    file_path="src/shop/api/report.py",
                    content=textwrap.dedent(
                        """
                        from shop.repositories.user import UserRepository

                        def report(session):
                            return UserRepository(session).get_active_users()
                        """
                    ),
                )
            )
        finally:
            guard.close()

        assert decision.action is DecisionAction.CHALLENGE
        assert "get_active_users" in decision.reason

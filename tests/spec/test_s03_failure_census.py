"""SPEC §3 — the failure-mode census (Phase 7).

SPEC §3 lists the failures AgentGuard exists to prevent. The benchmark
(`docs/BENCH-mutation.md`) established that the project had built an excellent detector
for one of them that current models no longer commit. Phase 7 stops guessing which of the
others matter and counts them.

Three things are being pinned down, in descending order of how badly a regression would
hurt.

**Observe-only is silent.** Not "mostly silent", not "silent on the paths we remembered".
Every decision, every event, every action. `TestObserveOnlyIsSilent` drives the real Guard
and `TestObserveOnlyThroughTheInstalledHooks` drives the real daemon through the real
settings.json, because a mode that leaks would corrupt the very measurement it exists to
take — and because Phase 5's port-default bug proved that a thing only tests configure is
a thing nobody has tested.

**Modes with no detector are never reported as zero.** Six of the seventeen have no
deterministic signal. Printing "0" for them would repeat the mistake this whole phase is a
correction for: a confident number with nothing behind it.

**The new detectors never speak.** They are recorded and counted, and the agent hears
nothing. A detector that started challenging mid-census would change the behaviour being
counted.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import httpx
import pytest

from agentguard.census import collect, render, render_detectors
from agentguard.census.taxonomy import NOT_INSTRUMENTED_BECAUSE, TAXONOMY
from agentguard.core import observe
from agentguard.core.config import Settings
from agentguard.core.enums import DecisionAction, EventType, FailureMode
from agentguard.core.events import AgentEvent
from agentguard.core.models import Decision, Finding
from agentguard.core.store import ProjectStore
from agentguard.core.taskstate import TaskState
from agentguard.repo import RepoIndex
from agentguard.validate import checks
from agentguard.verify import completion_gate
from tests.conftest import REPO_ROOT, free_port, pre_tool_use, user_prompt_submit

pytestmark = pytest.mark.spec

FIXTURES = Path(__file__).parent.parent / "fixtures"
SPEC_DOCUMENT = (
    REPO_ROOT / "AgentGuard — Host-Powered AI Agent Reliability & Reasoning Layer.md"
)

HALLUCINATING_FILE = textwrap.dedent(
    """
    from shop.repositories.user import UserRepository

    def report(session):
        return UserRepository(session).get_active_users()
    """
)


@pytest.fixture
def repo(tmp_path) -> Path:
    dest = tmp_path / "pyrepo"
    shutil.copytree(FIXTURES / "pyrepo", dest)
    return dest


@pytest.fixture
def index(repo) -> RepoIndex:
    return RepoIndex(repo).build()


def guard_for(repo: Path, observe_only: bool = False):
    from agentguard.core.engine import Guard

    guard = Guard(Settings(observe_only=observe_only))
    guard.workspace(repo).index.ready(timeout=30)
    return guard


def event(repo: Path, kind: EventType, tool: str | None = None, **args) -> AgentEvent:
    return AgentEvent(
        event=kind,
        agent="claude-code",
        workspace=str(repo),
        session_id="s1",
        tool=tool,
        arguments=args,
        raw={},
    )


def prompt_event(repo: Path, text: str) -> AgentEvent:
    return AgentEvent(
        event=EventType.USER_PROMPT,
        agent="claude-code",
        workspace=str(repo),
        session_id="s1",
        prompt_text=text,
    )


def write_event(repo: Path, path: str, content: str) -> AgentEvent:
    return event(repo, EventType.PRE_TOOL_USE, "Write", file_path=path, content=content)


def spec_for(prompt: str, index: RepoIndex):
    from agentguard.complexity import assess
    from agentguard.intent import extract

    spec = extract(prompt, index)
    spec.complexity = assess(spec, index)
    return spec


HOOK_NAME = {
    EventType.SESSION_START: "SessionStart",
    EventType.USER_PROMPT: "UserPromptSubmit",
    EventType.PRE_TOOL_USE: "PreToolUse",
    EventType.POST_TOOL_USE: "PostToolUse",
    EventType.STOP: "Stop",
    EventType.SESSION_END: "SessionEnd",
}


def heard_by_agent(source: AgentEvent, decision: Decision) -> dict:
    """What this decision actually puts in front of the agent.

    `Decision.is_silent` asks whether the decision object is empty; this asks the only
    question that matters, by running the real adapter. An ALLOW carrying an internal
    note ("below severity threshold") is not `is_silent` and yet reaches nobody, and
    conflating the two would let a recorded-never-raised detector look like a failure —
    or, far worse, let a leak look like a pass.
    """
    from agentguard.adapters.claude_code import translate

    return translate.from_decision(source, decision, HOOK_NAME.get(source.event, ""))


# =================================================================================
# The taxonomy is the SPEC, not a paraphrase of it
# =================================================================================


def spec_section_3_bullets() -> list[str]:
    """The failure modes as SPEC §3 actually writes them."""
    text = SPEC_DOCUMENT.read_text(encoding="utf-8")
    section = text.split("# 3. Problem Being Solved", 1)[1]
    bullets: list[str] = []
    started = False
    for line in section.splitlines():
        if line.startswith("- "):
            started = True
            bullets.append(line[2:].strip())
        elif started and line.strip() and not line.startswith("- "):
            break
    return bullets


class TestTheTaxonomyIsTheSpec:
    """The census taxonomy must *be* SPEC §3, not a remembered version of it.

    This test exists because the count was wrong. `IMPLEMENTATION_PLAN.md` and the Phase 7
    brief both say fourteen failure modes; the document lists seventeen. A census built on
    a miscount would have quietly omitted three of them, and nothing would have said so.
    """

    def test_the_spec_lists_seventeen_failure_modes(self):
        assert len(spec_section_3_bullets()) == 17

    def test_every_bullet_has_a_taxonomy_entry_in_order(self):
        assert [entry.spec_text for entry in TAXONOMY] == spec_section_3_bullets()

    def test_every_failure_mode_is_in_the_taxonomy(self):
        """Otherwise a detector could record a mode the census does not know to report."""
        covered = {entry.mode for entry in TAXONOMY}
        missing = set(FailureMode) - covered - {FailureMode.NOT_A_FAILURE}
        assert not missing, f"failure modes with no taxonomy entry: {sorted(missing)}"

    def test_every_uninstrumented_mode_explains_itself(self):
        """"No detector" invites the reader to assume an oversight. Say which it is."""
        for entry in TAXONOMY:
            if not entry.instrumented:
                assert NOT_INSTRUMENTED_BECAUSE.get(entry.mode), entry.mode

    def test_every_instrumented_mode_states_what_it_proves(self):
        for entry in TAXONOMY:
            if entry.instrumented:
                assert entry.proves, entry.mode

    def test_a_finding_cannot_omit_its_failure_mode(self):
        """No default. A detector that cannot say what it detects is not census input."""
        assert Finding.model_fields["failure_mode"].is_required()


# =================================================================================
# Observe-only mode: total silence
# =================================================================================


class TestSilenceIsTotal:
    """`observe.silence()` builds a fresh ALLOW rather than blanking known fields.

    The difference matters the day someone adds a new way for a Decision to reach the
    agent: with a rebuild, the new channel is silent until it is deliberately let through.
    """

    def test_every_channel_to_the_agent_is_closed(self):
        loud = Decision(
            action=DecisionAction.CHALLENGE,
            reason="a challenge the agent must not see",
            additional_context="context it must not see either",
            updated_arguments={"command": "rewritten"},
        )
        quiet = observe.silence(loud)
        assert quiet.is_silent
        assert quiet.action is DecisionAction.ALLOW
        assert quiet.reason == ""
        assert quiet.additional_context is None
        assert quiet.updated_arguments is None

    def test_the_counterfactual_survives(self):
        quiet = observe.silence(Decision(action=DecisionAction.BLOCK, reason="x"))
        assert quiet.would_have is DecisionAction.BLOCK

    def test_findings_survive_because_they_are_the_census(self):
        finding = Finding(
            category="evidence",
            verdict="contradicted",
            failure_mode=FailureMode.HALLUCINATED_API,
            subject="Type.method",
        )
        quiet = observe.silence(Decision(action=DecisionAction.CHALLENGE, findings=[finding]))
        assert [f.failure_mode for f in quiet.findings] == [FailureMode.HALLUCINATED_API]

    def test_it_is_idempotent(self):
        once = observe.silence(Decision(action=DecisionAction.CHALLENGE, reason="x"))
        assert observe.silence(once).would_have is DecisionAction.CHALLENGE


class TestObserveOnlyThroughTheGuard:
    """The real Guard, the real engines, the real store.

    Phase 4 taught this the hard way: `evidence.check()` passed its own tests throughout
    while every challenge in the real pipeline was being dropped, because the wiring was
    what was broken. Every claim below is made against `Guard.handle()`.
    """

    def test_a_challenge_becomes_a_silent_allow_but_is_still_counted(self, repo):
        guard = guard_for(repo, observe_only=True)
        try:
            guard.handle(prompt_event(repo, "Add an active-users report."))
            decision = guard.handle(
                write_event(repo, "src/shop/api/report.py", HALLUCINATING_FILE)
            )
            store = guard.workspace(repo).store
            counts = {r["failure_mode"]: r for r in store.failure_mode_counts(0)}
        finally:
            guard.close()

        assert decision.is_silent, "observe-only must never reach the agent"
        assert decision.would_have is DecisionAction.CHALLENGE
        assert "hallucinated_api" in counts
        assert counts["hallucinated_api"]["occurrences"] == 1

    def test_the_same_action_is_challenged_when_guarding(self, repo):
        """The control: without observe-only this is a challenge, so the silence above
        is the mode working rather than the detector having missed."""
        guard = guard_for(repo, observe_only=False)
        try:
            guard.handle(prompt_event(repo, "Add an active-users report."))
            decision = guard.handle(
                write_event(repo, "src/shop/api/report.py", HALLUCINATING_FILE)
            )
        finally:
            guard.close()

        assert decision.action is DecisionAction.CHALLENGE
        assert "get_active_users" in decision.reason

    def test_the_planning_budget_is_withheld(self, repo):
        """Injected context steers the agent. A census of a steered agent measures the
        steering, so observe-only drops this too — it is not only about challenges."""
        guarded = guard_for(repo, observe_only=False)
        observing = guard_for(repo, observe_only=True)
        try:
            loud = guarded.handle(prompt_event(repo, "Make our inference service production-ready."))
            quiet = observing.handle(prompt_event(repo, "Make our inference service production-ready."))
        finally:
            guarded.close()
            observing.close()

        assert loud.additional_context and "[AgentGuard]" in loud.additional_context
        assert quiet.additional_context is None

    def test_a_blocked_completion_becomes_a_silent_allow(self, repo):
        guard = guard_for(repo, observe_only=True)
        try:
            guard.handle(prompt_event(repo, "Fix the paginate helper."))
            guard.handle(
                event(repo, EventType.POST_TOOL_USE, "Edit", file_path="src/shop/utils/pagination.py")
            )
            failing = event(repo, EventType.POST_TOOL_USE, "Bash", command="pytest -q")
            failing.result = "F\n====== 1 failed, 2 passed in 0.2s ======\n"
            guard.handle(failing)

            stop = event(repo, EventType.STOP)
            stop.last_assistant_message = "Done — all tests pass."
            decision = guard.handle(stop)
            counts = {r["failure_mode"] for r in guard.workspace(repo).store.failure_mode_counts(0)}
        finally:
            guard.close()

        assert decision.is_silent
        assert decision.would_have is DecisionAction.BLOCK
        assert "false_completion" in counts

    def test_observing_does_not_spend_the_gates_budget(self, repo):
        """A block the agent never saw must not count against the per-task cap, or a long
        observed session would stop producing gate observations part-way through."""
        guard = guard_for(repo, observe_only=True)
        try:
            guard.handle(prompt_event(repo, "Fix the paginate helper."))
            guard.handle(
                event(repo, EventType.POST_TOOL_USE, "Edit", file_path="src/shop/utils/pagination.py")
            )
            failing = event(repo, EventType.POST_TOOL_USE, "Bash", command="pytest -q")
            failing.result = "====== 1 failed, 2 passed in 0.2s ======\n"
            guard.handle(failing)

            modes = []
            for _ in range(4):
                stop = event(repo, EventType.STOP)
                decision = guard.handle(stop)
                assert decision.is_silent
                modes.append({str(f.failure_mode) for f in decision.findings})

            ws = guard.workspace(repo)
            state = ws.task_state(ws.resolve_task_id("s1"))
            spent = state.stop_blocks
        finally:
            guard.close()

        assert spent == 0, "observing spent the completion gate's budget"
        assert all("false_completion" in seen for seen in modes), modes

    def test_the_session_records_which_mode_it_ran_under(self, repo):
        """Findings from a guarded session describe a steered agent. The census has to be
        able to tell the two populations apart rather than averaging them."""
        guard = guard_for(repo, observe_only=True)
        try:
            guard.handle(event(repo, EventType.SESSION_START))
            activity = guard.workspace(repo).store.activity(0)
        finally:
            guard.close()

        assert activity["sessions"] == 1
        assert activity["observe_only_sessions"] == 1

    @pytest.mark.parametrize(
        "kind,tool,args",
        [
            (EventType.SESSION_START, None, {}),
            (EventType.PRE_TOOL_USE, "Bash", {"command": "git push --force origin main"}),
            (EventType.PRE_TOOL_USE, "Bash", {"command": 'rm -rf "$BUILD_DIR"'}),
            (EventType.PRE_TOOL_USE, "Edit", {"file_path": "src/shop/models.py"}),
            (EventType.POST_TOOL_USE, "Edit", {"file_path": "src/shop/models.py"}),
            (EventType.STOP, None, {}),
            (EventType.SESSION_END, None, {}),
        ],
    )
    def test_no_event_can_produce_a_word(self, repo, kind, tool, args):
        """Including the two that normally do not merely challenge: a force push asks the
        human, and an unguarded `rm -rf $VAR` gets rewritten."""
        guard = guard_for(repo, observe_only=True)
        try:
            guard.handle(prompt_event(repo, "Tidy up the models."))
            source = event(repo, kind, tool, **args)
            decision = guard.handle(source)
        finally:
            guard.close()

        assert decision.is_silent, f"{kind}/{tool} spoke: {decision!r}"
        assert decision.updated_arguments is None
        assert heard_by_agent(source, decision) == {}, f"{kind}/{tool} reached the agent"


class TestObserveOnlyThroughTheInstalledHooks:
    """Through the wire: real settings.json, real daemon, real HTTP.

    A mode configured only by tests is a mode nobody has tested — the port-default bug
    from Phase 5 would have been caught here and nowhere else. The daemon is started with
    `AGENTGUARD_OBSERVE=1`, which is how a developer would actually try this for one
    session.
    """

    @pytest.fixture
    def observing_daemon(self, tmp_path, isolated_home):
        from agentguard.adapters.claude_code import install as inst
        from agentguard.core.config import DaemonSettings

        port = free_port()
        settings_file = tmp_path / ".claude" / "settings.json"
        inst.install(
            settings_file,
            settings=Settings(daemon=DaemonSettings(host="127.0.0.1", port=port)),
        )

        env = dict(
            os.environ,
            AGENTGUARD_HOME=str(isolated_home),
            PYTHONPATH=str(REPO_ROOT / "src"),
            AGENTGUARD_OBSERVE="1",
        )
        proc = subprocess.Popen(
            [sys.executable, "-m", "agentguard.daemon", "run", "--port", str(port)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            proc.kill()
            raise TimeoutError("daemon never became healthy")

        yield json.loads(settings_file.read_text())

        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)

    @staticmethod
    def fire(config: dict, event_name: str, payload: dict) -> dict:
        """Do what Claude Code does with an `http` hook entry.

        Only the decision-carrying hooks are reachable this way. `SessionStart` installs a
        `command` hook — the shim that revives a dead daemon — so it has no HTTP entry to
        fire, which is why the index is waited on by polling below rather than by warming
        it through that event.
        """
        hooks = config["hooks"][event_name][0]["hooks"]
        hook = next(h for h in hooks if h["type"] == "http")
        response = httpx.post(
            hook["url"], json=payload, headers=hook["headers"], timeout=hook["timeout"]
        )
        assert response.status_code == 200
        return response.json()

    def test_the_env_flag_reaches_the_daemon(self, observing_daemon, repo):
        """`AGENTGUARD_OBSERVE=1` for one session — how a developer would actually try it.

        This prompt scores 75/100 DEEP and normally comes back carrying a substantial
        planning budget, so an empty body here is the flag working rather than the
        Planning Governor having nothing to say.
        """
        assert self.fire(
            observing_daemon,
            "UserPromptSubmit",
            user_prompt_submit("Make our inference service production-ready.", cwd=str(repo)),
        ) == {}, "the planning budget leaked through the wire"

    def test_the_wire_stays_empty_while_the_database_fills(self, observing_daemon, repo):
        """The two halves of observe-only, checked together over the real transport.

        The index builds in a background thread, so the first calls legitimately answer
        "unknown". Polling until the finding lands proves the detector really ran — and
        every response along the way had to be empty, so a leak cannot hide inside the
        warm-up window.
        """
        self.fire(
            observing_daemon,
            "UserPromptSubmit",
            user_prompt_submit("Add an active-users report.", cwd=str(repo)),
        )
        payload = pre_tool_use(
            "Write",
            cwd=str(repo),
            file_path="src/shop/api/report.py",
            content=HALLUCINATING_FILE,
        )

        store = ProjectStore.for_workspace(repo)
        deadline = time.time() + 30
        recorded: set[str] = set()
        while time.time() < deadline and "hallucinated_api" not in recorded:
            body = self.fire(observing_daemon, "PreToolUse", payload)
            assert body == {}, f"observe-only emitted a hook decision: {body}"
            recorded = {row["failure_mode"] for row in store.failure_mode_counts(0)}
            if "hallucinated_api" not in recorded:
                time.sleep(0.2)

        assert "hallucinated_api" in recorded, "silent is not the same as not looking"


# =================================================================================
# The new detectors — SPEC §3.11, §3.12, §3.15, §3.16
# =================================================================================


class TestUnnecessaryDependencies:
    """SPEC §3: "introduce unnecessary dependencies".

    The Evidence Engine already caught `pip install X`. It could not see the other half of
    the same act — editing the manifest — which is how a dependency actually becomes
    permanent.
    """

    def edit(self, repo: Path, old: str, new: str) -> AgentEvent:
        return event(
            repo,
            EventType.PRE_TOOL_USE,
            "Edit",
            file_path=str(repo / "pyproject.toml"),
            old_string=old,
            new_string=new,
        )

    def test_a_new_package_is_recorded(self, repo, index):
        findings = checks.dependency_added(
            self.edit(repo, '"pydantic>=2.9"]', '"pydantic>=2.9", "celery>=5.3"]'),
            index,
            "pyproject.toml",
        )
        assert [f.subject for f in findings] == ["celery"]
        assert findings[0].failure_mode is FailureMode.UNNECESSARY_DEPENDENCY

    def test_a_duplicate_of_an_existing_dependency_is_stronger_evidence(self, repo, index):
        """The only case where the evidence reaches the word "unnecessary": the repository
        already declares something that does the same job."""
        findings = checks.dependency_added(
            self.edit(repo, '"pydantic>=2.9"]', '"pydantic>=2.9", "flask>=3.0"]'),
            index,
            "pyproject.toml",
        )
        assert len(findings) == 1
        assert "fastapi" in findings[0].summary
        assert findings[0].severity.value == "low"

    def test_an_unrelated_manifest_edit_says_nothing(self, repo, index):
        assert not checks.dependency_added(
            self.edit(repo, 'version = "0.1.0"', 'version = "0.2.0"'), index, "pyproject.toml"
        )

    def test_a_manifest_left_unparsable_mid_edit_says_nothing(self, repo, index):
        """A manifest under edit is routinely invalid. "Complete evidence or silence"."""
        assert not checks.dependency_added(
            self.edit(repo, '"pydantic>=2.9"]', '"pydantic>=2.9",'), index, "pyproject.toml"
        )

    def test_ordinary_source_edits_are_not_manifests(self, repo, index):
        assert not checks.dependency_added(
            event(
                repo,
                EventType.PRE_TOOL_USE,
                "Edit",
                file_path=str(repo / "src/shop/models.py"),
                old_string="",
                new_string="# note\n",
            ),
            index,
            "src/shop/models.py",
        )

    def test_a_new_manifest_declares_everything_it_contains(self, tmp_path, index):
        """Creating package.json in a Python repo: every entry is new by definition."""
        findings = checks.dependency_added(
            event(
                index.root,
                EventType.PRE_TOOL_USE,
                "Write",
                file_path=str(index.root / "package.json"),
                content=json.dumps({"dependencies": {"react": "^19", "axios": "^1"}}),
            ),
            index,
            "package.json",
        )
        assert {f.subject for f in findings} == {"react", "axios"}


class TestIgnoredRepositoryPatterns:
    """SPEC §3: "ignore existing repository patterns".

    A convention has to be *proved* before it can be broken: five agreeing siblings, and
    unanimity rather than a majority. One dissenter means the repository tolerates both,
    and a house style that is merely popular is not one an agent can be said to have
    ignored.
    """

    @pytest.fixture
    def own_index(self) -> RepoIndex:
        """AgentGuard's own repository — real conventions, not ones written to be broken."""
        return RepoIndex(REPO_ROOT).build()

    def creation(self, root: Path, path: str) -> AgentEvent:
        return event(
            root, EventType.PRE_TOOL_USE, "Write", file_path=str(root / path), content="x = 1\n"
        )

    @pytest.mark.parametrize(
        "path",
        [
            "src/agentguard/core/NewThing.py",
            "src/agentguard/core/newThing.py",
        ],
    )
    def test_a_mixed_case_name_among_lowercase_siblings(self, own_index, path):
        findings = checks.pattern_consistency(
            self.creation(REPO_ROOT, path), own_index, path
        )
        assert findings, f"{path} should have broken the lowercase convention"
        assert findings[0].failure_mode is FailureMode.IGNORED_REPO_PATTERN
        assert findings[0].severity.value == "info", "this must never be raised"

    @pytest.mark.parametrize(
        "path",
        [
            "src/agentguard/core/new_thing.py",
            "src/agentguard/core/newthing.py",
            "tests/spec/test_s99_something.py",
        ],
    )
    def test_a_conforming_name_says_nothing(self, own_index, path):
        assert not checks.pattern_consistency(self.creation(REPO_ROOT, path), own_index, path)

    def test_a_test_written_outside_the_test_root(self, own_index):
        path = "src/agentguard/test_thing.py"
        findings = checks.pattern_consistency(self.creation(REPO_ROOT, path), own_index, path)
        assert findings and "outside `tests/`" in findings[0].summary

    def test_a_test_written_in_the_test_root_says_nothing(self, own_index):
        path = "tests/test_thing.py"
        assert not checks.pattern_consistency(self.creation(REPO_ROOT, path), own_index, path)

    def test_a_brand_new_directory_is_noted(self, own_index):
        path = "src/agentguard/somewhere_new/thing.py"
        findings = checks.pattern_consistency(self.creation(REPO_ROOT, path), own_index, path)
        assert findings and "no existing siblings" in findings[0].summary

    def test_too_few_siblings_prove_no_convention(self, repo, index):
        """The fixture repo has two test files and two-file directories. Nothing there is
        a convention, so nothing there can be violated."""
        path = "src/shop/NewThing.py"
        assert not checks._naming(index, path)

    def test_editing_an_existing_file_is_never_a_naming_violation(self, own_index):
        path = "src/agentguard/core/store.py"
        edit = event(
            REPO_ROOT, EventType.PRE_TOOL_USE, "Edit", file_path=str(REPO_ROOT / path),
            old_string="a", new_string="b",
        )
        assert not checks.pattern_consistency(edit, own_index, path)


class TestNamingPrecisionOnRealCode:
    """Phase 3's lesson, applied to a new detector.

    A hand-written false-positive corpus passed 26/26 while real code was producing a 2.2%
    false-challenge rate — a synthetic corpus only tests its author's imagination. So the
    check here is against real code: every file in this repository, replayed as though the
    agent were creating it now. Each one already follows whatever conventions exist around
    it by construction, so every finding is a false positive.
    """

    def test_no_existing_file_violates_its_own_conventions(self):
        index = RepoIndex(REPO_ROOT).build()
        offenders = [
            (path, finding.summary)
            for path in sorted(index.files)
            for finding in checks._naming(index, path)
        ]
        assert not offenders, f"{len(offenders)} false positive(s): {offenders[:5]}"


class TestInsufficientTests:
    """SPEC §3: "write insufficient tests".

    Recorded, never raised. What it proves is narrow and the census says so: untested code
    was changed, not that the tests that were written were inadequate.
    """

    def state_after(self, index: RepoIndex, touched: list[str], prompt: str = "Add a report.") -> TaskState:
        state = TaskState(task_id="t1", spec=spec_for(prompt, index))
        for path in touched:
            state.touch(path)
        return state

    def modes(self, verdict) -> set[str]:
        return {str(f.failure_mode) for f in verdict.findings}

    def test_changed_code_with_no_coverage_and_no_test_written(self, index):
        (index.root / "src/shop/api/report.py").write_text("def report():\n    return []\n")
        index.refresh()
        verdict = completion_gate.evaluate(
            self.state_after(index, ["src/shop/api/report.py"]), index
        )
        assert "insufficient_tests" in self.modes(verdict)

    def test_writing_a_test_stands_the_observation_down(self, index):
        """If the agent engaged with testing at all, whether it did so *sufficiently* is
        not a deterministic question and this detector must not pretend otherwise."""
        (index.root / "src/shop/api/report.py").write_text("def report():\n    return []\n")
        (index.root / "tests/test_report.py").write_text("def test_report():\n    assert True\n")
        index.refresh()
        verdict = completion_gate.evaluate(
            self.state_after(index, ["src/shop/api/report.py", "tests/test_report.py"]), index
        )
        assert "insufficient_tests" not in self.modes(verdict)

    def test_well_covered_code_says_nothing(self, index):
        verdict = completion_gate.evaluate(
            self.state_after(index, ["src/shop/utils/pagination.py"], "Fix the paginate helper."),
            index,
        )
        assert "insufficient_tests" not in self.modes(verdict)

    def test_a_project_with_no_tests_at_all_says_nothing(self, tmp_path):
        """Otherwise this reports on the repository rather than on the change."""
        (tmp_path / "app.py").write_text("x = 1\n")
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        bare = RepoIndex(tmp_path).build()
        state = TaskState(task_id="t1", spec=spec_for("Change app.", bare))
        state.touch("app.py")
        assert "insufficient_tests" not in self.modes(completion_gate.evaluate(state, bare))

    def test_it_never_holds_the_turn_open(self, index):
        (index.root / "src/shop/api/report.py").write_text("def report():\n    return []\n")
        index.refresh()
        verdict = completion_gate.evaluate(
            self.state_after(index, ["src/shop/api/report.py"]), index
        )
        assert not verdict.should_block


class TestUnverifiedCompletion:
    """SPEC §3: "fail to verify their own changes" and "incorrectly claim ... complete".

    Both already had a mechanism — the Completion Gate. What they lacked was a record: the
    gate produced a reason string and nothing countable.
    """

    def state_after(self, index: RepoIndex, touched: list[str]) -> TaskState:
        state = TaskState(task_id="t1", spec=spec_for("Fix the paginate helper.", index))
        for path in touched:
            state.touch(path)
        return state

    def test_covered_code_and_no_test_run(self, index):
        verdict = completion_gate.evaluate(
            self.state_after(index, ["src/shop/utils/pagination.py"]), index
        )
        assert verdict.result.value == "incomplete"
        assert FailureMode.UNVERIFIED_CHANGE in {f.failure_mode for f in verdict.findings}

    def test_finishing_on_failing_tests(self, index):
        from agentguard.verify.runners import TestOutcome

        state = self.state_after(index, ["src/shop/utils/pagination.py"])
        state.verification.outcomes.append(
            TestOutcome(runner="pytest", passed=False, failed=1, command="pytest -q")
        )
        verdict = completion_gate.evaluate(state, index)
        assert FailureMode.FALSE_COMPLETION in {f.failure_mode for f in verdict.findings}

    def test_finishing_on_unparsable_code(self, index):
        (index.root / "src/shop/utils/pagination.py").write_text("def broken(:\n")
        verdict = completion_gate.evaluate(
            self.state_after(index, ["src/shop/utils/pagination.py"]), index
        )
        assert FailureMode.FALSE_COMPLETION in {f.failure_mode for f in verdict.findings}

    def test_a_passing_run_produces_no_failure_finding(self, index):
        from agentguard.verify.runners import TestOutcome

        state = self.state_after(index, ["src/shop/utils/pagination.py"])
        state.verification.outcomes.append(
            TestOutcome(runner="pytest", passed=True, command="pytest -q")
        )
        verdict = completion_gate.evaluate(state, index)
        assert not verdict.should_block
        assert FailureMode.FALSE_COMPLETION not in {f.failure_mode for f in verdict.findings}

    def test_observations_survive_the_gates_own_rationing(self, index):
        """Loop safety governs whether the gate *speaks*. It must not govern what the
        census *sees*, or a long task would stop being observed part-way through."""
        state = self.state_after(index, ["src/shop/utils/pagination.py"])
        state.stop_blocks = 99
        verdict = completion_gate.evaluate(state, index, max_blocks=2)
        assert not verdict.should_block, "rationing must still silence the gate"
        assert FailureMode.UNVERIFIED_CHANGE in {f.failure_mode for f in verdict.findings}


class TestTheNewDetectorsAreRecordedNeverRaised:
    """The Phase 7 mandate, checked through the real Guard with guarding fully ON.

    A new detector that started challenging during the census would change the behaviour
    the census exists to count. So these three are below the severity threshold, and the
    proof is that the pipeline stays silent while the database fills up.
    """

    def run(self, repo: Path, events: list[AgentEvent]) -> tuple[list[dict], set[str], list[Decision]]:
        guard = guard_for(repo, observe_only=False)
        try:
            decisions = [guard.handle(e) for e in events]
            counts = {r["failure_mode"] for r in guard.workspace(repo).store.failure_mode_counts(0)}
        finally:
            guard.close()
        return [heard_by_agent(e, d) for e, d in zip(events, decisions, strict=True)], counts, decisions

    def test_a_new_dependency_is_recorded_silently(self, repo):
        heard, counts, decisions = self.run(
            repo,
            [
                prompt_event(repo, "Add background jobs."),
                event(
                    repo,
                    EventType.PRE_TOOL_USE,
                    "Edit",
                    file_path=str(repo / "pyproject.toml"),
                    old_string='"pydantic>=2.9"]',
                    new_string='"pydantic>=2.9", "celery>=5.3"]',
                ),
            ],
        )
        assert heard[-1] == {}, heard[-1]
        assert decisions[-1].action is DecisionAction.ALLOW
        assert "unnecessary_dependency" in counts

    def test_a_broken_convention_is_recorded_silently(self, repo):
        # Give the fixture enough same-extension siblings for a convention to exist.
        for name in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta"):
            (repo / "src/shop/utils" / f"{name}.py").write_text("x = 1\n")

        heard, counts, decisions = self.run(
            repo,
            [
                prompt_event(repo, "Add a formatting helper."),
                write_event(repo, str(repo / "src/shop/utils/NewHelper.py"), "x = 1\n"),
            ],
        )
        assert heard[-1] == {}, heard[-1]
        assert decisions[-1].action is DecisionAction.ALLOW
        assert "ignored_repo_pattern" in counts

    def test_untested_code_is_recorded_silently(self, repo):
        heard, counts, _ = self.run(
            repo,
            [
                prompt_event(repo, "Add an active-users report."),
                event(
                    repo, EventType.POST_TOOL_USE, "Write", file_path="src/shop/api/report.py"
                ),
                event(repo, EventType.STOP),
            ],
        )
        assert all(body == {} for body in heard[1:]), heard[1:]
        assert "insufficient_tests" in counts


# =================================================================================
# The report
# =================================================================================


class TestTheCensusReport:
    """Phase 7's exit criterion: a ranked table of observed failure frequencies."""

    @pytest.fixture
    def populated(self, repo):
        """A session with two distinct failure modes actually observed."""
        guard = guard_for(repo, observe_only=True)
        try:
            guard.handle(event(repo, EventType.SESSION_START))
            guard.handle(prompt_event(repo, "Add an active-users report."))
            guard.handle(write_event(repo, "src/shop/api/report.py", HALLUCINATING_FILE))
            guard.handle(
                event(repo, EventType.POST_TOOL_USE, "Write", file_path="src/shop/api/report.py")
            )
            guard.handle(event(repo, EventType.STOP))
        finally:
            guard.close()
        return collect(ProjectStore.for_workspace(repo), days=1)

    def test_it_ranks_what_was_observed(self, populated):
        assert populated.observed
        modes = [str(o.spec.mode) for o in populated.observed]
        assert "hallucinated_api" in modes
        assert "insufficient_tests" in modes

    def test_ranking_is_by_breadth_then_frequency(self, populated):
        widths = [(o.tasks, o.occurrences) for o in populated.observed]
        assert widths == sorted(widths, reverse=True)

    def test_it_carries_its_denominators(self, populated):
        assert populated.activity["tasks"] >= 1
        assert populated.activity["observe_only_sessions"] == 1
        assert populated.activity["would_have_spoken"] >= 1

    def test_uninstrumented_modes_are_never_reported_as_zero(self, populated):
        """The load-bearing property. "Nothing looks for it" and "it does not happen" are
        different facts, and reporting the first as the second is how this project came to
        build a detector for a problem that had gone away."""
        uninstrumented = {str(s.mode) for s in populated.uninstrumented}
        assert uninstrumented, "the six modes with no detector went missing"
        counted = {str(o.spec.mode) for o in populated.observed}
        silent = {str(s.mode) for s in populated.silent}
        assert not (uninstrumented & counted)
        assert not (uninstrumented & silent), "an uninstrumented mode was filed as never-seen"

    def test_the_three_groups_partition_the_taxonomy(self, populated):
        total = len(populated.observed) + len(populated.silent) + len(populated.uninstrumented)
        assert total == len(TAXONOMY) == 17

    def test_the_rendered_table_says_why_a_mode_has_no_number(self, populated):
        text = render(populated)
        assert "not instrumented — no detector, so no number" in text
        assert "misunderstand developer intent" in text
        assert "AgentGuard owns no LLM" in text
        # And it must never print a zero row for one of them.
        for line in text.splitlines():
            if "misunderstand developer intent" in line:
                assert "0" not in line.replace("§3", "")

    def test_it_warns_when_modes_are_mixed(self, repo):
        """Guarded and observed sessions describe different populations. Averaging them
        produces a number that is neither, so the report says so."""
        guard = guard_for(repo, observe_only=False)
        try:
            guard.handle(event(repo, EventType.SESSION_START))
            guard.handle(prompt_event(repo, "Add an active-users report."))
            guard.handle(write_event(repo, "src/shop/api/report.py", HALLUCINATING_FILE))
        finally:
            guard.close()
        text = render(collect(ProjectStore.for_workspace(repo), days=1))
        assert "mixed modes" in text

    def test_an_empty_project_reports_nothing_rather_than_zeroes(self, repo):
        census = collect(ProjectStore.for_workspace(repo), days=1)
        assert not census.observed
        text = render(census)
        assert "no recorded activity" in text
        assert len(census.silent) + len(census.uninstrumented) == 17

    def test_an_empty_project_does_not_claim_to_have_looked(self, repo):
        """"Never observed" asserts that we looked and saw nothing. With no recorded
        activity we did not look, and saying otherwise is the same overstatement as
        printing a zero for a mode nothing detects."""
        text = render(collect(ProjectStore.for_workspace(repo), days=1))
        assert "instrumented — but there was nothing to observe" in text
        assert "instrumented, never observed" not in text

    def test_a_populated_project_does_say_it_looked(self, populated):
        assert "instrumented, never observed" in render(populated)

    def test_the_json_shape_is_complete(self, populated):
        payload = populated.to_dict()
        assert set(payload) >= {
            "root", "window_days", "activity", "observed",
            "instrumented_never_observed", "not_instrumented",
        }
        assert len(payload["not_instrumented"]) == len(populated.uninstrumented)
        assert all(entry["because"] for entry in payload["not_instrumented"])

    def test_the_detector_notes_state_what_is_actually_proved(self):
        """So "insufficient tests: 40%" cannot be read as more than it is."""
        text = render_detectors()
        assert "proves:" in text
        assert "It says nothing about the quality of tests that were written" in text

    def test_the_cli_runs(self, populated, repo):
        from typer.testing import CliRunner

        from agentguard.cli.main import app

        result = CliRunner().invoke(app, ["census", "--workspace", str(repo), "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["observed"]


class TestCensusLatency:
    """SPEC §8 still applies. A census that slows the agent will be switched off."""

    @pytest.mark.latency
    def test_observing_does_not_cost_more_than_guarding(self, repo):
        samples: dict[bool, float] = {}
        for observe_only in (False, True):
            guard = guard_for(repo, observe_only=observe_only)
            try:
                guard.handle(prompt_event(repo, "Add an active-users report."))
                probe = write_event(repo, "src/shop/api/report.py", HALLUCINATING_FILE)
                for _ in range(5):
                    guard.handle(probe)
                timings = []
                for _ in range(40):
                    start = time.perf_counter()
                    guard.handle(probe)
                    timings.append((time.perf_counter() - start) * 1000)
            finally:
                guard.close()
            timings.sort()
            samples[observe_only] = timings[int(len(timings) * 0.95) - 1]

        print(f"\n  p95 guarding={samples[False]:.2f}ms  observing={samples[True]:.2f}ms")
        assert samples[True] < 100.0
        assert samples[False] < 100.0

    @pytest.mark.latency
    def test_the_new_detectors_stay_inside_the_budget(self, repo):
        """Both run on file creation, which scans the file map. Measured, not assumed."""
        guard = guard_for(repo, observe_only=True)
        try:
            guard.handle(prompt_event(repo, "Add a helper."))
            manifest = event(
                repo, EventType.PRE_TOOL_USE, "Edit",
                file_path=str(repo / "pyproject.toml"),
                old_string='"pydantic>=2.9"]', new_string='"pydantic>=2.9", "celery>=5.3"]',
            )
            creation = write_event(repo, str(repo / "src/shop/utils/NewHelper.py"), "x = 1\n")
            for probe in (manifest, creation):
                for _ in range(5):
                    guard.handle(probe)
                timings = []
                for _ in range(30):
                    start = time.perf_counter()
                    guard.handle(probe)
                    timings.append((time.perf_counter() - start) * 1000)
                timings.sort()
                p95 = timings[int(len(timings) * 0.95) - 1]
                print(f"\n  {probe.tool} p95={p95:.2f}ms")
                assert p95 < 100.0
        finally:
            guard.close()


def test_the_spec_document_is_where_this_test_thinks_it_is():
    """Guards the parser above: a renamed SPEC would make every taxonomy test vacuous."""
    assert SPEC_DOCUMENT.exists(), SPEC_DOCUMENT
    assert re.search(r"^# 3\. Problem Being Solved$", SPEC_DOCUMENT.read_text(), re.MULTILINE)

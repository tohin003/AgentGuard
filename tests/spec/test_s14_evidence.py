"""SPEC §14, §15, §16, §17, §39 — evidence-grounded challenges.

    Agent: "I'll use UserRepository.get_active_users()."
    -> No get_active_users() found -> Unsupported claim -> Challenge agent

Catching that is the easy half. The half that decides whether anyone keeps this tool
installed is `TestNoFalseChallenges` below: a corpus of ordinary, correct code that must
produce **zero** findings. One false challenge per session is an annoyance; two is an
uninstall.

The asymmetry is deliberate throughout. A missed hallucination costs one bad edit that
the tests would probably catch anyway. A false challenge costs the agent's trust in the
whole layer — and SPEC §39 is explicit that AgentGuard must be invisible until it isn't.
"""

from __future__ import annotations

import shutil
import textwrap
import typing
from pathlib import Path

import pytest

from agentguard import challenge, evidence
from agentguard.core.enums import EventType, Severity, Verdict
from agentguard.core.events import AgentEvent
from agentguard.repo import RepoIndex

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


def write_event(index: RepoIndex, path: str, content: str) -> AgentEvent:
    return AgentEvent(
        event=EventType.PRE_TOOL_USE,
        agent="claude-code",
        workspace=str(index.root),
        session_id="s1",
        tool="Write",
        arguments={"file_path": path, "content": textwrap.dedent(content)},
    )


def edit_event(index: RepoIndex, path: str, old: str, new: str) -> AgentEvent:
    return AgentEvent(
        event=EventType.PRE_TOOL_USE,
        agent="claude-code",
        workspace=str(index.root),
        session_id="s1",
        tool="Edit",
        arguments={"file_path": path, "old_string": old, "new_string": new},
    )


def bash_event(index: RepoIndex, command: str) -> AgentEvent:
    return AgentEvent(
        event=EventType.PRE_TOOL_USE,
        agent="claude-code",
        workspace=str(index.root),
        session_id="s1",
        tool="Bash",
        arguments={"command": command},
    )


# =================================================================================
# The SPEC's worked example
# =================================================================================


class TestTheSpecExample:
    def test_hallucinated_method_is_caught(self, index):
        findings = evidence.check(
            write_event(
                index,
                "src/shop/api/report.py",
                """
                from shop.repositories.user import UserRepository

                def active_report(session):
                    repo = UserRepository(session)
                    return repo.get_active_users()
                """,
            ),
            index,
        )
        assert len(findings) == 1
        finding = findings[0]
        assert finding.verdict is Verdict.CONTRADICTED
        assert finding.severity is Severity.HIGH
        assert finding.subject == "UserRepository.get_active_users"

    def test_the_challenge_cites_the_file_as_evidence(self, index):
        """SPEC §14's challenge includes `Evidence: src/repositories/user.py`."""
        findings = evidence.check(
            write_event(
                index,
                "src/shop/api/report.py",
                """
                from shop.repositories.user import UserRepository
                def go(session):
                    return UserRepository(session).get_active_users()
                """,
            ),
            index,
        )
        paths = [ref.path for f in findings for ref in f.evidence]
        assert "src/shop/repositories/user.py" in paths

    def test_the_challenge_says_what_does_exist(self, index):
        """A complaint is not actionable; a list of real members is."""
        findings = evidence.check(
            write_event(
                index,
                "src/shop/api/report.py",
                """
                from shop.repositories.user import UserRepository
                def go(session):
                    return UserRepository(session).get_active_users()
                """,
            ),
            index,
        )
        message = challenge.render(findings)
        for member in ("get_by_id", "list_all", "count"):
            assert member in message

    def test_the_challenge_leaves_the_door_open(self, index):
        """SPEC §17: AgentGuard must not simply tell the host it is wrong."""
        findings = evidence.check(
            write_event(
                index,
                "src/shop/api/report.py",
                """
                from shop.repositories.user import UserRepository
                def go(session):
                    return UserRepository(session).get_active_users()
                """,
            ),
            index,
        )
        message = challenge.render(findings).lower()
        assert "deliberately introducing it" in message or "unless" in message


# =================================================================================
# The corpus that matters: ordinary correct code must be silent
# =================================================================================


class TestNoFalseChallenges:
    """Every case here is legitimate. Any finding is a bug."""

    LEGITIMATE: typing.ClassVar[list[tuple[str, str]]] = [
        (
            "calls an existing method",
            """
            from shop.repositories.user import UserRepository
            def go(session):
                return UserRepository(session).list_all()
            """,
        ),
        (
            "defines the method it calls, in the same edit",
            """
            class Reporter:
                def collect(self):
                    return self.summarise()

                def summarise(self):
                    return {}
            """,
        ),
        (
            "subclasses a repository class and inherits its methods",
            """
            from shop.repositories.user import UserRepository

            class AuditedRepo(UserRepository):
                def audit(self):
                    return self.list_all()
            """,
        ),
        (
            "subclasses and adds the method being called",
            """
            from shop.repositories.user import UserRepository

            class ExtendedRepo(UserRepository):
                def get_active_users(self):
                    return []

            def go(session):
                return ExtendedRepo(session).get_active_users()
            """,
        ),
        (
            "receiver is an unannotated parameter",
            """
            def go(repo):
                return repo.get_active_users()
            """,
        ),
        (
            "receiver comes from a function call",
            """
            def make():
                return object()

            def go():
                return make().whatever_method()
            """,
        ),
        (
            "receiver is a loop variable",
            """
            def go(items):
                for item in items:
                    item.do_something()
            """,
        ),
        (
            "receiver is a context manager variable",
            """
            def go(ctx):
                with ctx as handle:
                    handle.write_everything()
            """,
        ),
        (
            "module attribute access",
            """
            import os
            import os.path
            def go():
                return os.path.join("a", "b")
            """,
        ),
        (
            "aliased module attribute access",
            """
            import json as j
            def go(x):
                return j.dumps(x)
            """,
        ),
        (
            "stdlib imports",
            """
            import json
            import dataclasses
            from pathlib import Path
            from collections.abc import Iterable
            """,
        ),
        (
            "declared third-party imports",
            """
            import fastapi
            import sqlalchemy
            from pydantic import BaseModel
            """,
        ),
        (
            "class inheriting from an external base",
            """
            from pydantic import BaseModel

            class Payload(BaseModel):
                name: str

            def go(p: Payload):
                return p.model_dump()
            """,
        ),
        (
            "rebinding a name to a different type",
            """
            from shop.repositories.user import UserRepository
            def go(session, flag):
                thing = UserRepository(session)
                if flag:
                    thing = object()
                return thing.get_active_users()
            """,
        ),
        (
            "annotated parameter of an unknown external type",
            """
            from fastapi import Request
            def go(request: Request):
                return request.anything_at_all()
            """,
        ),
        (
            "self-reference on a class with an unknown base",
            """
            from fastapi import FastAPI

            class App(FastAPI):
                def boot(self):
                    return self.include_router(None)
            """,
        ),
        (
            "existing module-level function",
            """
            from shop.utils.pagination import paginate, page_metadata
            def go(q):
                return paginate(q), page_metadata(1, 1, 1)
            """,
        ),
        (
            "dataclass attribute access",
            """
            from shop.models import User
            def go(u: User):
                return u.email
            """,
        ),
        (
            "chained attribute on unknown",
            """
            def go(client):
                return client.api.v1.users.list()
            """,
        ),
        (
            "dunder access",
            """
            from shop.models import User
            def go(u: User):
                return u.__class__, u.__dict__
            """,
        ),
        (
            "type annotations only",
            """
            from shop.models import User
            def go() -> list[User]:
                return []
            """,
        ),
        (
            "relative import within the package",
            """
            from ..models import User
            from .pagination import paginate
            def go():
                return User, paginate
            """,
        ),
        (
            "importing a submodule",
            """
            from shop import models
            def go():
                return models.User
            """,
        ),
        (
            "constant access on a known module",
            """
            from shop.models import MAX_PAGE_SIZE
            def go():
                return MAX_PAGE_SIZE
            """,
        ),
        (
            "decorator usage",
            """
            import functools

            @functools.lru_cache(maxsize=8)
            def go(x):
                return x
            """,
        ),
        (
            "exception handling",
            """
            def go():
                try:
                    raise ValueError("x")
                except ValueError as exc:
                    return exc.args
            """,
        ),
    ]

    @pytest.mark.parametrize(
        ("label", "source"), LEGITIMATE, ids=[label for label, _ in LEGITIMATE]
    )
    def test_legitimate_code_produces_no_findings(self, index, label, source):
        target = (
            "src/shop/utils/new_module.py"
            if "relative import" in label
            else "src/shop/api/new_module.py"
        )
        findings = evidence.check(write_event(index, target, source), index)
        assert findings == [], (
            f"false challenge on legitimate code ({label}): "
            f"{[f.summary for f in findings]}"
        )

    def test_re_exports_through_a_package_init(self, index, repo):
        """`from shop import User` is valid when __init__.py re-exports it, even though
        __init__.py defines no such symbol. Missing this would be a glaring false
        positive on any package that curates its public surface."""
        (repo / "src" / "shop" / "__init__.py").write_text(
            "from shop.models import User, Order\n"
        )
        fresh = RepoIndex(repo).build()
        findings = evidence.check(
            write_event(fresh, "src/shop/api/new.py", "from shop import User\n"),
            fresh,
        )
        assert findings == []

    def test_pre_existing_problems_are_not_blamed_on_this_edit(self, index, repo):
        """The file already calls a method that does not exist. The agent edits an
        unrelated line. AgentGuard must say nothing — being blamed for someone else's
        code is the fastest way to become noise."""
        target = repo / "src" / "shop" / "api" / "legacy.py"
        target.write_text(
            "from shop.repositories.user import UserRepository\n\n"
            "def old(session):\n"
            "    return UserRepository(session).get_active_users()\n\n"
            "VERSION = 1\n"
        )
        fresh = RepoIndex(repo).build()

        findings = evidence.check(
            edit_event(fresh, "src/shop/api/legacy.py", "VERSION = 1", "VERSION = 2"), fresh
        )
        assert findings == []

    def test_an_unparsable_baseline_produces_silence(self, index, repo):
        """Without a clean 'before' there is no way to tell a new claim from an old one."""
        target = repo / "src" / "shop" / "api" / "broken.py"
        target.write_text("def broken(:\n    pass\n")
        fresh = RepoIndex(repo).build()

        findings = evidence.check(
            write_event(
                fresh,
                "src/shop/api/broken.py",
                """
                from shop.repositories.user import UserRepository
                def go(session):
                    return UserRepository(session).get_active_users()
                """,
            ),
            fresh,
        )
        # The new content parses, but the baseline did not, so nothing is attributable.
        assert findings == []

    def test_nothing_is_said_before_the_index_is_ready(self, repo):
        unbuilt = RepoIndex(repo)
        assert not unbuilt.is_built
        findings = evidence.check(
            write_event(
                unbuilt,
                "src/shop/api/x.py",
                "from shop.repositories.user import UserRepository\n"
                "def go(s):\n    return UserRepository(s).get_active_users()\n",
            ),
            unbuilt,
        )
        assert findings == []

    def test_read_only_and_unrelated_tools_are_ignored(self, index):
        for tool, args in [
            ("Read", {"file_path": "src/shop/models.py"}),
            ("Grep", {"pattern": "get_active_users"}),
            ("Bash", {"command": "pytest -q"}),
            ("Bash", {"command": "git status"}),
        ]:
            event = AgentEvent(
                event=EventType.PRE_TOOL_USE,
                agent="claude-code",
                workspace=str(index.root),
                session_id="s",
                tool=tool,
                arguments=args,
            )
            assert evidence.check(event, index) == [], f"{tool} {args}"


# =================================================================================
# Hallucinations that must be caught
# =================================================================================


class TestHallucinationsAreCaught:
    def test_method_missing_from_a_known_type(self, index):
        findings = evidence.check(
            write_event(
                index,
                "src/shop/api/x.py",
                """
                from shop.repositories.user import UserRepository
                def go(s):
                    return UserRepository(s).find_by_email("a@b.c")
                """,
            ),
            index,
        )
        assert any(f.subject == "UserRepository.find_by_email" for f in findings)

    def test_method_missing_via_an_inferred_local(self, index):
        findings = evidence.check(
            write_event(
                index,
                "src/shop/api/x.py",
                """
                from shop.repositories.user import UserRepository
                def go(s):
                    repo = UserRepository(s)
                    return repo.bulk_delete()
                """,
            ),
            index,
        )
        assert any(f.subject == "UserRepository.bulk_delete" for f in findings)

    def test_method_missing_via_an_annotated_parameter(self, index):
        findings = evidence.check(
            write_event(
                index,
                "src/shop/api/x.py",
                """
                from shop.repositories.user import UserRepository
                def go(repo: UserRepository):
                    return repo.truncate()
                """,
            ),
            index,
        )
        assert any(f.subject == "UserRepository.truncate" for f in findings)

    def test_symbol_missing_from_a_known_module(self, index):
        findings = evidence.check(
            write_event(index, "src/shop/api/x.py", "from shop.utils.pagination import paginate_cursor\n"),
            index,
        )
        assert any("paginate_cursor" in f.summary for f in findings)
        assert any("paginate" in f.suggestion for f in findings)

    def test_undeclared_third_party_import(self, index):
        findings = evidence.check(
            write_event(index, "src/shop/api/x.py", "import fastapi_pagination\n"), index
        )
        assert any("fastapi_pagination" in f.summary for f in findings)

    def test_a_new_dependency_is_challenged(self, index):
        """SPEC §16's DEPENDENCY category."""
        findings = evidence.check(bash_event(index, "pip install redis-om"), index)
        assert any(f.subject == "redis-om" for f in findings)
        message = challenge.render(findings).lower()
        assert "permanent" in message or "manifest" in message

    def test_installing_an_already_declared_dependency_is_fine(self, index):
        assert evidence.check(bash_event(index, "pip install fastapi"), index) == []

    def test_multi_edit_is_analysed(self, index, repo):
        target = repo / "src" / "shop" / "api" / "multi.py"
        target.write_text("from shop.repositories.user import UserRepository\n\nA = 1\nB = 2\n")
        fresh = RepoIndex(repo).build()

        event = AgentEvent(
            event=EventType.PRE_TOOL_USE,
            agent="claude-code",
            workspace=str(fresh.root),
            session_id="s",
            tool="MultiEdit",
            arguments={
                "file_path": "src/shop/api/multi.py",
                "edits": [
                    {
                        "old_string": "A = 1",
                        "new_string": "A = 1\n\ndef go(s):\n    return UserRepository(s).nope()",
                    },
                    {"old_string": "B = 2", "new_string": "B = 3"},
                ],
            },
        )
        findings = evidence.check(event, fresh)
        assert any(f.subject == "UserRepository.nope" for f in findings)

    def test_an_edit_whose_old_string_is_absent_is_ignored(self, index):
        """The tool call will fail on its own; assessing it would be meaningless."""
        findings = evidence.check(
            edit_event(index, "src/shop/models.py", "THIS TEXT IS NOT PRESENT", "x"), index
        )
        assert findings == []


# =================================================================================
# SPEC §17 / §39 — the ledger
# =================================================================================


class TestChallengeRationing:
    """SPEC §17: AgentGuard must not become a system that constantly says "You're wrong"."""

    @pytest.fixture
    def ledger(self, tmp_path):
        from agentguard.challenge.ledger import ChallengeLedger
        from agentguard.core.config import ChallengeSettings
        from agentguard.core.store import ProjectStore

        return ChallengeLedger(ProjectStore.for_workspace(tmp_path), ChallengeSettings())

    def make_findings(self, n: int, severity: Severity = Severity.HIGH):
        from agentguard.core.enums import ChallengeCategory, FailureMode
        from agentguard.core.models import Finding

        return [
            Finding(
                category=ChallengeCategory.EVIDENCE,
                verdict=Verdict.CONTRADICTED,
                failure_mode=FailureMode.HALLUCINATED_API,
                severity=severity,
                subject=f"Type.method_{i}",
                summary=f"Type.method_{i} does not exist",
            )
            for i in range(n)
        ]

    def test_a_concern_is_raised_once_and_then_respected(self, ledger):
        """The §17 acceptance path: challenged, reconsidered, proceeding — so allow it."""
        findings = self.make_findings(1)

        first = ledger.admit("task-1", findings)
        assert first.should_challenge
        ledger.record("task-1", first.raise_now)

        second = ledger.admit("task-1", findings)
        assert not second.should_challenge, "repeating an objection is nagging"
        assert second.suppressed

    def test_a_hard_ceiling_per_task(self, ledger):
        for i in range(10):
            findings = self.make_findings(1)
            findings[0].subject = f"Type.unique_{i}"
            verdict = ledger.admit("task-1", findings)
            ledger.record("task-1", verdict.raise_now)

        final = ledger.admit("task-1", self.make_findings(1))
        assert not final.should_challenge
        assert "budget" in final.reason

    def test_low_severity_is_recorded_not_raised(self, ledger):
        verdict = ledger.admit("task-1", self.make_findings(3, Severity.LOW))
        assert not verdict.should_challenge
        assert len(verdict.suppressed) == 3

    def test_the_most_severe_concern_is_raised_first(self, ledger):
        low = self.make_findings(1, Severity.MEDIUM)
        low[0].subject = "Type.medium"
        high = self.make_findings(1, Severity.CRITICAL)
        high[0].subject = "Type.critical"

        verdict = ledger.admit("task-1", [*low, *high])
        assert verdict.raise_now[0].subject == "Type.critical"

    def test_separate_tasks_have_separate_budgets(self, ledger):
        findings = self.make_findings(1)
        first = ledger.admit("task-1", findings)
        ledger.record("task-1", first.raise_now)

        assert ledger.admit("task-2", findings).should_challenge

    def test_no_task_context_means_record_only(self, ledger):
        verdict = ledger.admit(None, self.make_findings(1))
        assert not verdict.should_challenge


class TestFalsePositiveRateOnRealCode:
    """SPEC §39 — the property that decides whether anyone keeps this installed.

    The hand-written corpus above tests the cases I thought of. This one tests the cases
    I did not: it replays **AgentGuard's own source** through the engine as if the agent
    were creating each file from scratch, so every claim in real, working, committed code
    is checked. Any challenge-level finding is by definition a false positive.

    Written after an audit found 2.2% false positives on real code while the hand-written
    corpus was passing 26/26 — a synthetic corpus only ever tests its author's
    imagination. This runs on every commit so the rate cannot silently regress.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def own_source_index(cls) -> RepoIndex:
        return RepoIndex(Path(__file__).resolve().parents[2]).build()

    def test_no_challenge_level_false_positives(self, own_source_index):
        from agentguard.evidence import extractors, resolvers

        index = own_source_index
        python_files = [p for p, r in index.files.items() if r.lang == "python"]
        assert len(python_files) > 40, "the corpus should be substantial"

        claims_checked = 0
        false_positives: list[str] = []

        for rel in python_files:
            try:
                content = (index.root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # before="" simulates the agent creating this file fresh, so every claim in
            # it is treated as newly introduced and actually gets checked.
            outcome = extractors.EditOutcome(rel, "", content, True)
            claims, facts = extractors.claims_from_edit(outcome)
            claims_checked += len(claims)

            for resolution in resolvers.resolve_all(claims, index, facts, rel):
                if not resolution.is_problem:
                    continue
                if resolution.severity in (Severity.LOW, Severity.INFO):
                    continue  # recorded in the log, never raised to the agent
                false_positives.append(
                    f"{rel}:{resolution.claim.line} [{resolution.severity}] {resolution.summary}"
                )

        assert claims_checked > 1000, f"only {claims_checked} claims exercised"
        assert not false_positives, (
            f"{len(false_positives)} false challenge(s) on working code "
            f"({len(false_positives) / claims_checked * 100:.2f}% of {claims_checked} claims):\n"
            + "\n".join(false_positives[:20])
        )

    def test_instance_attributes_are_known(self, own_source_index):
        """`self.root = ...` in `__init__` must be a known attribute of the class.

        Missing these was the largest single source of false positives in the audit:
        every `index.root`, `index.is_built`, `store.db_path` looked hallucinated.
        """
        attributes = own_source_index.attributes_of("RepoIndex")
        assert {"root", "files", "is_built", "dependencies"} <= attributes

    def test_container_annotations_do_not_leak_their_element_type(self, own_source_index):
        """`claims: list[Claim]` makes `claims` a list, not a Claim.

        Unwrapping the element type turned every `claims.append(...)` into a confident
        claim about `Claim.append`.
        """
        from agentguard.evidence import pyanalysis

        facts = pyanalysis.analyze(
            "from shop.repositories.user import UserRepository\n"
            "def go() -> None:\n"
            "    repos: list[UserRepository] = []\n"
            "    repos.append(1)\n"
            "    repos.sort()\n"
        )
        subjects = {claim.subject for claim in facts.claims}
        assert "UserRepository.append" not in subjects
        assert "UserRepository.sort" not in subjects


class TestSilentByDefault:
    """SPEC §39: 'AI that silently guards AI and intervenes only when necessary.'"""

    def test_a_hundred_ordinary_operations_produce_no_challenge(self, index, repo):
        """Ordinary work — reads, greps, correct edits, test runs — must be invisible."""
        from agentguard.core.config import Settings
        from agentguard.core.engine import Guard

        guard = Guard(Settings())
        workspace = guard.workspace(repo)
        workspace.index.ready(timeout=30)

        actions: list[tuple[str, dict]] = []
        for i in range(25):
            actions.append(("Read", {"file_path": "src/shop/models.py"}))
            actions.append(("Grep", {"pattern": f"pattern_{i}"}))
            actions.append(("Bash", {"command": "pytest -q"}))
            actions.append(
                (
                    "Write",
                    {
                        "file_path": f"src/shop/api/gen_{i}.py",
                        "content": (
                            "from shop.repositories.user import UserRepository\n\n"
                            f"def handler_{i}(session):\n"
                            "    return UserRepository(session).list_all()\n"
                        ),
                    },
                )
            )

        try:
            decisions = [
                guard.handle(
                    AgentEvent(
                        event=EventType.PRE_TOOL_USE,
                        agent="claude-code",
                        workspace=str(repo),
                        session_id="s1",
                        tool=tool,
                        arguments=args,
                    )
                )
                for tool, args in actions
            ]
        finally:
            guard.close()

        noisy = [d for d in decisions if not d.is_silent]
        assert len(decisions) == 100
        assert not noisy, f"{len(noisy)} of 100 ordinary operations produced output"


class TestChallengeRendering:
    def test_empty_findings_render_to_nothing(self):
        assert challenge.render([]) == ""

    def test_multiple_findings_are_numbered(self, index):
        findings = evidence.check(
            write_event(
                index,
                "src/shop/api/x.py",
                """
                from shop.repositories.user import UserRepository
                from shop.utils.pagination import paginate_cursor
                def go(s):
                    return UserRepository(s).nope()
                """,
            ),
            index,
        )
        assert len(findings) >= 2
        message = challenge.render(findings)
        assert "1." in message and "2." in message

    def test_the_message_names_itself(self, index):
        findings = evidence.check(
            write_event(
                index,
                "src/shop/api/x.py",
                "from shop.repositories.user import UserRepository\n"
                "def go(s):\n    return UserRepository(s).nope()\n",
            ),
            index,
        )
        assert challenge.render(findings).startswith("AgentGuard")

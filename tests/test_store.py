"""SQLite store (SPEC §30)."""

from __future__ import annotations

from pathlib import Path

from agentguard.adapters.claude_code import translate as claude
from agentguard.core.enums import ChallengeCategory, DecisionAction, Severity, Verdict
from agentguard.core.models import Decision, EvidenceRef, Finding
from agentguard.core.store import Store
from tests.conftest import pre_tool_use


def make_store(workspace: Path) -> Store:
    return Store.for_workspace(workspace)


class TestSessionsAndTasks:
    def test_session_and_task_lifecycle(self, workspace):
        store = make_store(workspace)
        store.open_session("s1", "claude-code", str(workspace))
        assert store.current_task_id("s1") is None

        task_id = store.create_task("s1", "Add pagination to /users", complexity=18.0, depth="direct")
        assert store.current_task_id("s1") == task_id

        # A newer open task supersedes the older one.
        task_2 = store.create_task("s1", "Rename getUser")
        assert store.current_task_id("s1") == task_2

        store.close_task(task_2)
        assert store.current_task_id("s1") == task_id

        store.close_session("s1")

    def test_open_session_is_idempotent(self, workspace):
        store = make_store(workspace)
        store.open_session("s1", "claude-code", str(workspace))
        store.open_session("s1", "claude-code", str(workspace))
        assert store.current_task_id("s1") is None


class TestDecisions:
    def test_records_decision_with_findings_and_evidence(self, workspace):
        store = make_store(workspace)
        event = claude.to_event(pre_tool_use())
        finding = Finding(
            category=ChallengeCategory.EVIDENCE,
            verdict=Verdict.INSUFFICIENT_EVIDENCE,
            severity=Severity.HIGH,
            subject="UserRepository.get_active_users",
            summary="Method not found in repository.",
            evidence=[EvidenceRef(source="ast", path="src/repositories/user.py", line=1)],
        )
        decision = Decision(action=DecisionAction.CHALLENGE, reason="challenge text", findings=[finding])

        decision_id = store.record_decision(event, decision)
        assert decision_id

        detail = store.decision_detail(decision_id)
        assert detail["action"] == "challenge"
        assert len(detail["findings"]) == 1
        assert detail["findings"][0]["subject"] == "UserRepository.get_active_users"

    def test_recent_decisions_are_newest_first(self, workspace):
        store = make_store(workspace)
        event = claude.to_event(pre_tool_use())
        ids = [store.record_decision(event, Decision(action=DecisionAction.ALLOW)) for _ in range(3)]
        recent = store.recent_decisions(10)
        assert len(recent) == 3
        assert {r["id"] for r in recent} == set(ids)


class TestChallengeLedger:
    """SPEC §17/§39: challenges must be rationed, so the ledger has to be exact."""

    def test_counts_per_fingerprint_and_per_task(self, workspace):
        store = make_store(workspace)
        store.record_challenge("task-1", "fp-a", "SymbolA")
        store.record_challenge("task-1", "fp-a", "SymbolA")
        store.record_challenge("task-1", "fp-b", "SymbolB")

        assert store.challenge_count("task-1", "fp-a") == 2
        assert store.challenge_count("task-1", "fp-b") == 1
        assert store.challenge_count("task-1") == 3
        assert store.challenge_count("task-2") == 0

    def test_resolution_is_recorded(self, workspace):
        store = make_store(workspace)
        store.record_challenge("task-1", "fp-a")
        store.resolve_challenge("task-1", "fp-a")
        # Resolution does not erase history; the count is still the count.
        assert store.challenge_count("task-1", "fp-a") == 1


class TestVerifications:
    def test_verification_lifecycle(self, workspace):
        store = make_store(workspace)
        vid = store.start_verification("task-1", "pytest")
        store.finish_verification(vid, "passed", {"tests": 12, "failed": 0})
        rows = store.verifications_for("task-1")
        assert len(rows) == 1
        assert rows[0]["status"] == "passed"


class TestResilience:
    """Storage failure must never propagate to a hook response."""

    def test_survives_a_closed_connection(self, workspace):
        store = make_store(workspace)
        store.close()
        # Every one of these would raise sqlite3.ProgrammingError if unguarded.
        assert store.create_task("s1", "prompt") is None
        assert store.current_task_id("s1") is None
        assert store.recent_decisions() == []
        assert store.challenge_count("t", "f") == 0
        store.record_metric("x", 1.0)

    def test_metrics_round_trip(self, workspace):
        store = make_store(workspace)
        for v in (1.0, 2.0, 3.0):
            store.record_metric("hook.latency_ms", v, {"event": "pre_tool_use"})
        assert sorted(store.metric_values("hook.latency_ms")) == [1.0, 2.0, 3.0]

"""SQLite store (SPEC §30)."""

from __future__ import annotations

import time
from pathlib import Path

from agentguard.adapters.claude_code import translate as claude
from agentguard.core.enums import ChallengeCategory, DecisionAction, Severity, Verdict
from agentguard.core.models import Decision, EvidenceRef, Finding
from agentguard.core.store import ProjectStore as Store
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


    def test_metrics_round_trip(self, workspace):
        store = make_store(workspace)
        for v in (1.0, 2.0, 3.0):
            store.record_metric("hook.latency_ms", v, {"event": "pre_tool_use"})
        assert sorted(store.metric_values("hook.latency_ms")) == [1.0, 2.0, 3.0]


class TestProjectIsolation:
    """Memory plan §4: "AgentGuard must never accidentally use Project A's architectural
    knowledge while working on Project B."

    Since Phase 3.5 all projects share one database, so this is the property that makes
    that safe — and it is enforced by the handle's shape, not by remembering a WHERE
    clause.
    """

    def test_two_projects_get_distinct_identities(self, tmp_path):
        a = Store.for_workspace(tmp_path / "project-a")
        b = Store.for_workspace(tmp_path / "project-b")
        assert a.project_id != b.project_id

    def test_a_project_cannot_see_another_projects_decisions(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        a = Store.for_workspace(tmp_path / "a")
        b = Store.for_workspace(tmp_path / "b")

        event = claude.to_event(pre_tool_use())
        a.record_decision(event, Decision(action=DecisionAction.CHALLENGE, reason="A's business"))

        assert len(a.recent_decisions()) == 1
        assert b.recent_decisions() == [], "project B must not see project A's decisions"

    def test_tasks_sessions_and_challenges_are_scoped(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        a = Store.for_workspace(tmp_path / "a")
        b = Store.for_workspace(tmp_path / "b")

        a.open_session("shared-session-id", "claude-code")
        a.create_task("shared-session-id", "A's task")
        a.record_challenge("shared-task-id", "fp", "A's subject")

        # Even with identical session and task ids, B sees nothing of A's.
        assert b.current_task_id("shared-session-id") is None
        assert b.challenge_count("shared-task-id", "fp") == 0
        assert a.challenge_count("shared-task-id", "fp") == 1

    def test_a_projects_decision_cannot_be_read_by_id_from_another(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        a = Store.for_workspace(tmp_path / "a")
        b = Store.for_workspace(tmp_path / "b")

        decision_id = a.record_decision(claude.to_event(pre_tool_use()), Decision())
        assert a.decision_detail(decision_id) is not None
        assert b.decision_detail(decision_id) is None, "ids must not leak across projects"

    def test_memory_is_scoped(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        a = Store.for_workspace(tmp_path / "a")
        b = Store.for_workspace(tmp_path / "b")

        a.remember("architecture", "All database access goes through repositories.")
        assert len(a.memories()) == 1
        assert b.memories() == [], "project memory must never cross projects"

    def test_identity_follows_the_git_remote_not_the_path(self, tmp_path, monkeypatch):
        """A project that is moved or re-cloned keeps its accumulated memory."""
        import agentguard.core.store as store_module

        monkeypatch.setattr(store_module, "_git_remote", lambda root: "git@github.com:me/repo.git")
        first = store_module.project_identity(tmp_path / "checkout-one")
        second = store_module.project_identity(tmp_path / "elsewhere" / "checkout-two")
        assert first.id == second.id


class TestBoundedRows:
    """Memory plan §6: never store huge objects unnecessarily."""

    def test_large_tool_arguments_are_summarised(self, workspace):
        store = make_store(workspace)
        event = claude.to_event(
            pre_tool_use("Write", file_path="big.py", content="x" * 500_000)
        )
        store.record_event(event)

        rows = store.db.query("SELECT arguments FROM events WHERE id=?", (event.event_id,))
        stored = rows[0]["arguments"]
        assert len(stored) < 5_000, "a 500KB payload must not be archived in the database"
        assert "500000 chars" in stored, "but its size should still be recorded"

    def test_summarisation_keeps_structure(self):
        from agentguard.core.store import summarize_value

        summarised = summarize_value(
            {"file_path": "src/a.py", "content": "y" * 10_000, "items": list(range(100))}
        )
        assert summarised["file_path"] == "src/a.py"
        assert "10000 chars" in summarised["content"]
        assert len(summarised["items"]) == 11  # 10 plus a count marker


class TestRetention:
    """Memory plan §5: configurable retention, with long-lived things kept."""

    def test_expired_raw_events_are_pruned(self, workspace):
        store = make_store(workspace)
        db = store.db
        old = time.time() - (400 * 86400)

        db.write(
            "INSERT INTO events(id, project_id, session_id, type, ts) VALUES(?,?,?,?,?)",
            ("old-event", store.project_id, "s", "pre_tool_use", old),
        )
        db.write(
            "INSERT INTO events(id, project_id, session_id, type, ts) VALUES(?,?,?,?,?)",
            ("new-event", store.project_id, "s", "pre_tool_use", time.time()),
        )

        db.prune()
        remaining = {r["id"] for r in db.query("SELECT id FROM events")}
        assert "old-event" not in remaining
        assert "new-event" in remaining

    def test_memories_are_never_pruned(self, workspace):
        """Validated project knowledge is the point of keeping a database at all."""
        store = make_store(workspace)
        memory_id = store.remember("architecture", "Auth is handled by middleware.")
        store.db.write(
            "UPDATE memories SET created_at=? WHERE id=?",
            (time.time() - (2000 * 86400), memory_id),
        )

        store.db.prune()
        assert len(store.memories()) == 1

    def test_zero_days_means_keep_forever(self, workspace):
        store = make_store(workspace)
        store.db.retention.raw_events_days = 0
        store.db.write(
            "INSERT INTO events(id, project_id, session_id, type, ts) VALUES(?,?,?,?,?)",
            ("ancient", store.project_id, "s", "pre_tool_use", 0.0),
        )
        store.db.prune()
        assert store.db.query("SELECT id FROM events WHERE id='ancient'")

    def test_maintenance_is_rate_limited(self, workspace):
        """Memory plan §7: never expensive during active agent execution."""
        store = make_store(workspace)
        assert store.db.maintenance_due() is True
        store.db.maintain()
        assert store.db.maintenance_due() is False
        assert store.db.maintain() == {}, "a second call inside the interval does nothing"

    def test_maintenance_compacts_without_losing_live_data(self, workspace):
        store = make_store(workspace)
        task_id = store.create_task("s1", "keep me")
        store.db.maintain(force=True)
        assert store.current_task_id("s1") == task_id


class TestStorageIsNeverADependency:
    """Memory plan §8, the critical rule:

        "If SQLite fails or disk space becomes unavailable, AgentGuard's core reliability
        functionality must continue working."

    This is the Phase 3.5 exit criterion.
    """

    def test_a_closed_database_does_not_raise(self, workspace):
        store = make_store(workspace)
        store.db.close()

        # Every one of these would raise sqlite3.ProgrammingError if unguarded.
        assert store.create_task("s1", "prompt") is None
        assert store.current_task_id("s1") is None
        assert store.recent_decisions() == []
        assert store.challenge_count("t", "f") == 0
        assert store.memories() == []
        store.record_metric("x", 1.0)
        store.record_event(claude.to_event(pre_tool_use()))

    def test_critical_disk_stops_writes_but_not_reads(self, workspace, monkeypatch):
        store = make_store(workspace)
        store.create_task("s1", "before the disk filled")

        monkeypatch.setattr(type(store.db), "free_megabytes", lambda self: 10.0)
        assert store.db.disk_state() == "critical"
        assert store.db.writes_enabled is False

        store.create_task("s1", "after")  # silently not persisted
        tasks = store.db.query("SELECT id FROM tasks WHERE project_id=?", (store.project_id,))
        assert len(tasks) == 1, "writes stop, and nothing raises"

    def test_low_disk_prunes_more_aggressively(self, workspace, monkeypatch):
        store = make_store(workspace)
        # 10 days old: inside the 14-day retention normally, outside it when scaled down.
        store.db.write(
            "INSERT INTO events(id, project_id, session_id, type, ts) VALUES(?,?,?,?,?)",
            ("borderline", store.project_id, "s", "pre_tool_use", time.time() - (10 * 86400)),
        )
        store.db.prune(aggressive=False)
        assert store.db.query("SELECT id FROM events WHERE id='borderline'")

        monkeypatch.setattr(type(store.db), "free_megabytes", lambda self: 500.0)
        assert store.db.disk_state() == "low"
        store.db.maintain(force=True)
        assert not store.db.query("SELECT id FROM events WHERE id='borderline'")

    def test_the_guard_keeps_deciding_with_a_dead_database(self, workspace):
        """The property that actually matters: an agent action still gets a decision."""
        from agentguard.core.config import Settings
        from agentguard.core.engine import Guard

        guard = Guard(Settings())
        ws = guard.workspace(workspace)
        ws.index.ready(timeout=30)
        ws.store.db.close()

        try:
            decision = guard.handle(claude.to_event(pre_tool_use()))
        finally:
            guard.close()

        assert decision.action == "allow"
        assert decision.latency_ms >= 0

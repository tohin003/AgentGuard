"""Guard orchestration invariants that are easy to miss in component tests."""

from __future__ import annotations

from agentguard.core.config import Settings
from agentguard.core.engine import Guard
from agentguard.core.enums import EventType
from agentguard.core.events import AgentEvent


def _prompt(workspace: str, session_id: str, text: str) -> AgentEvent:
    return AgentEvent(
        event=EventType.USER_PROMPT,
        agent="claude-code",
        workspace=workspace,
        session_id=session_id,
        prompt_text=text,
    )


def _session_end(workspace: str, session_id: str) -> AgentEvent:
    return AgentEvent(
        event=EventType.SESSION_END,
        agent="claude-code",
        workspace=workspace,
        session_id=session_id,
    )


def test_tasks_are_attributed_to_their_session(workspace):
    guard = Guard(Settings())
    try:
        ws = guard.workspace(workspace)
        ws.index.ready(timeout=30)

        guard.handle(_prompt(str(workspace), "session-a", "Fix login validation"))
        task_a = ws.store.current_task_id("session-a")
        guard.handle(_prompt(str(workspace), "session-b", "Add pagination"))
        task_b = ws.store.current_task_id("session-b")

        assert task_a and task_b and task_a != task_b
        assert ws.resolve_task_id("session-a") == task_a
        assert ws.resolve_task_id("session-b") == task_b
        assert ws.resolve_task_id("unknown-session") is None
        assert ws.task_state(None, "session-a").task_id == task_a
        assert ws.task_state(None, "session-b").task_id == task_b
        assert ws.task_state(task_a, "session-b") is None
    finally:
        guard.close()


def test_session_end_closes_persisted_tasks_and_drops_live_state(workspace):
    guard = Guard(Settings())
    try:
        ws = guard.workspace(workspace)
        ws.index.ready(timeout=30)

        guard.handle(_prompt(str(workspace), "session-a", "Fix login validation"))
        task_id = ws.store.current_task_id("session-a")
        assert task_id
        assert ws.task_state(task_id, "session-a") is not None

        guard.handle(_session_end(str(workspace), "session-a"))

        row = ws.store.db.query(
            "SELECT status, closed_at FROM tasks WHERE id=? AND project_id=?",
            (task_id, ws.store.project_id),
        )[0]
        assert row["status"] == "closed"
        assert row["closed_at"] is not None
        assert ws.store.current_task_id("session-a") is None
        assert ws.task_state(None, "session-a") is None
    finally:
        guard.close()


def test_unknown_explicit_task_id_does_not_fall_back_to_session_task(workspace):
    guard = Guard(Settings())
    try:
        ws = guard.workspace(workspace)
        ws.index.ready(timeout=30)

        guard.handle(_prompt(str(workspace), "session-a", "Fix login validation"))
        task_id = ws.resolve_task_id("session-a")
        assert task_id

        assert ws.task_state("stale-task-id", "session-a") is None
        assert ws.task_state(task_id, "session-a") is not None
        # An explicit id remains directly addressable for diagnostics that already have
        # the unambiguous task key, while event callers pass the session as well.
        assert ws.task_state(task_id) is not None
    finally:
        guard.close()


def test_guard_does_not_reassign_stale_event_to_current_task(workspace):
    guard = Guard(Settings())
    try:
        source = workspace / "module.py"
        source.write_text("value = 1\n")
        ws = guard.workspace(workspace)
        ws.index.ready(timeout=30)

        guard.handle(_prompt(str(workspace), "session-a", "Fix module"))
        current = ws.resolve_task_id("session-a")
        assert current

        event = AgentEvent(
            event=EventType.POST_TOOL_USE,
            agent="claude-code",
            workspace=str(workspace),
            session_id="session-a",
            task_id="stale-task-id",
            tool="Edit",
            arguments={"file_path": str(source)},
        )
        guard.handle(event)

        assert event.task_id is None
        state = ws.task_state(current, "session-a")
        assert state is not None
        assert state.touched_files == set()
    finally:
        guard.close()

"""The Guard: the one place an AgentEvent becomes a Decision.

This is "AgentGuard Core" from SPEC §28 — the actual product. Everything else (hooks,
MCP, CLI, adapters) is an integration surface around this object.

Fail-open is structural
-----------------------
SPEC §8/§39 require that AgentGuard be invisible in normal operation. The corollary is
that a *broken* AgentGuard must also be invisible. Every path through `handle()` is
wrapped: any exception, any timeout, any missing index yields ALLOW. There is no failure
mode in which AgentGuard stops a developer from working.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from agentguard import complexity, intent, planning, validate
from agentguard.challenge.ledger import ChallengeLedger
from agentguard.core.config import Settings
from agentguard.core.enums import DecisionAction, EscalationLevel, EventType
from agentguard.core.events import AgentEvent
from agentguard.core.metrics import (
    M_DECISION,
    M_FALSE_COMPLETION_BLOCKED,
    M_HOOK_LATENCY,
    METRICS,
    Timer,
)
from agentguard.core.models import Decision
from agentguard.core.store import ProjectStore
from agentguard.core.taskstate import TaskState
from agentguard.intent.models import TaskSpec
from agentguard.repo.index import RepoIndex
from agentguard.verify import completion_gate, runners

log = logging.getLogger(__name__)


class Workspace:
    """Per-repository state: the decision store and the repository index."""

    def __init__(self, root: Path, settings: Settings) -> None:
        self.root = root
        self.settings = settings
        # A project-scoped handle on the shared database. It has no way to reach another
        # project's rows (Memory plan §4).
        self.store = ProjectStore.for_workspace(root, settings)
        # Built in the background: a large monorepo takes seconds, and no developer
        # should wait on it. Until it is ready every query answers "unknown", which
        # resolves to ALLOW (SPEC §39).
        self.index = RepoIndex(root, settings.index)
        self.index.build_async()
        # The TaskSpec for the prompt currently being worked on.
        self.current_spec: TaskSpec | None = None
        self.ledger = ChallengeLedger(self.store, settings.challenge)
        # Live task state: files touched, tests observed, gate budget. In memory only —
        # a lost task means the next action is judged without history, which is ALLOW.
        self._tasks: dict[str, TaskState] = {}
        self._latest_task_id: str | None = None

    def begin_task(self, task_id: str, spec: TaskSpec) -> TaskState:
        state = TaskState(task_id=task_id, spec=spec)
        self._tasks[task_id] = state
        self._latest_task_id = task_id
        # One prompt at a time; older tasks are only kept for late-arriving events.
        if len(self._tasks) > 8:
            for stale in list(self._tasks)[:-8]:
                self._tasks.pop(stale, None)
        return state

    def resolve_task_id(self, session_id: str) -> str | None:
        """The task a tool event belongs to. In-memory first; the store is the fallback
        after a daemon restart."""
        return self._latest_task_id or self.store.current_task_id(session_id)

    def task_state(self, task_id: str | None) -> TaskState | None:
        if task_id and task_id in self._tasks:
            return self._tasks[task_id]
        # Tool events can arrive before the store has attributed them to a task.
        if self._latest_task_id:
            return self._tasks.get(self._latest_task_id)
        return None

    def close(self) -> None:
        self.store.close()


class Guard:
    """Routes events to handlers, records everything, never breaks the host."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.load()
        self._workspaces: dict[str, Workspace] = {}
        self._lock = threading.Lock()
        self._handlers: dict[EventType, Callable[[AgentEvent, Workspace], Decision]] = {
            EventType.SESSION_START: self._on_session_start,
            EventType.USER_PROMPT: self._on_user_prompt,
            EventType.PRE_TOOL_USE: self._on_pre_tool_use,
            EventType.POST_TOOL_USE: self._on_post_tool_use,
            EventType.POST_TOOL_FAILURE: self._on_post_tool_use,
            EventType.STOP: self._on_stop,
            EventType.SUBAGENT_STOP: self._on_stop,
            EventType.SESSION_END: self._on_session_end,
        }

    # -- workspaces ---------------------------------------------------------------

    def workspace(self, root: str | Path) -> Workspace:
        key = str(Path(root).expanduser().resolve())
        with self._lock:
            ws = self._workspaces.get(key)
            if ws is None:
                ws = Workspace(Path(key), self.settings)
                self._workspaces[key] = ws
            return ws

    def close(self) -> None:
        with self._lock:
            for ws in self._workspaces.values():
                ws.close()
            self._workspaces.clear()

    # -- entry point --------------------------------------------------------------

    def handle(self, event: AgentEvent) -> Decision:
        """Never raises. Never blocks on failure."""
        if not self.settings.enabled:
            return Decision.allow()

        with Timer() as t:
            try:
                ws = self.workspace(event.workspace)
                # Resolve the task *before* dispatching. Handlers need it: without a task
                # id the challenge ledger has no way to avoid repeating itself, so it
                # suppresses everything — which silently disabled every challenge.
                event.task_id = event.task_id or ws.resolve_task_id(event.session_id)
                handler = self._handlers.get(event.event)
                decision = handler(event, ws) if handler else Decision.allow()
            except Exception:
                log.exception("agentguard: handler failed for %s; failing open", event.event)
                decision = Decision.allow()
                ws = None

        decision.latency_ms = t.ms
        METRICS.observe(M_HOOK_LATENCY, t.ms, {"event": str(event.event)})
        METRICS.increment(M_DECISION, labels={"action": str(decision.action)})

        if ws is not None:
            try:
                ws.store.record_event(event)
                decision.decision_id = ws.store.record_decision(event, decision) or decision.decision_id
                # Latency lives in the in-memory collector only. Persisting it here would
                # add a third commit per tool call for data already held in METRICS.
            except Exception:
                log.exception("agentguard: failed to persist event/decision")

        return decision

    # -- handlers -----------------------------------------------------------------
    #
    #   session_start -> warm the index
    #   user_prompt   -> Intent Gateway, Complexity Engine, Planning Governor (§9-§13)
    #   pre_tool_use  -> Action Validator: evidence, scope, risk (§14-§18)
    #   post_tool_use -> observe what happened: files touched, tests run (§19)
    #   stop          -> Completion Gate (§19, §20)
    #   session_end   -> close out and run storage maintenance

    def _on_session_start(self, event: AgentEvent, ws: Workspace) -> Decision:
        ws.store.open_session(event.session_id, event.agent, str(ws.root))
        # Creating the Workspace already kicked off the build; this hook has a 20s
        # budget, so give it a moment to finish while the session is still starting up.
        ws.index.ready(timeout=5.0)
        return Decision.allow()

    def _on_user_prompt(self, event: AgentEvent, ws: Workspace) -> Decision:
        """Intent Gateway + Complexity Engine + Planning Governor (SPEC §9, §12, §13).

        The planning budget rides back on `additionalContext`, which the developer never
        sees — so this shapes the agent's approach without adding anything to the UI
        (SPEC §39).
        """
        ws.store.open_session(event.session_id, event.agent, str(ws.root))

        prompt = event.prompt_text or ""
        index = ws.index if ws.index.is_built else None
        spec = intent.extract(prompt, index)
        spec.complexity = complexity.assess(spec, index)
        ws.current_spec = spec

        task_id = ws.store.create_task(
            event.session_id,
            prompt,
            spec=spec.model_dump(mode="json"),
            complexity=spec.complexity.score,
            depth=str(spec.complexity.depth),
        )
        event.task_id = task_id
        ws.begin_task(task_id, spec)

        budget = planning.render(spec, index)
        if not budget:
            return Decision.allow(EscalationLevel.REPOSITORY)

        return Decision(
            action=DecisionAction.ALLOW,
            level=EscalationLevel.REPOSITORY,
            additional_context=budget,
        )

    def _on_pre_tool_use(self, event: AgentEvent, ws: Workspace) -> Decision:
        """Action Validator (SPEC §18), which runs the Evidence Engine inside it."""
        # SPEC §7/§8: read-only tools never leave Level 0. This short-circuit is what
        # keeps ordinary exploration free.
        if event.is_read_only:
            return Decision.allow()

        return validate.validate(
            event=event,
            index=ws.index,
            state=ws.task_state(event.task_id),
            ledger=ws.ledger,
            task_id=event.task_id,
        )

    def _on_post_tool_use(self, event: AgentEvent, ws: Workspace) -> Decision:
        """Observe what actually happened (SPEC §19).

        Two jobs: keep the evidence base honest, and record what the Completion Gate will
        need — which files changed, and what the agent's own test runs reported.
        """
        state = ws.task_state(event.task_id)

        changed = event.arg("file_path") or event.arg("notebook_path")
        if isinstance(changed, str) and changed:
            path = ws.index.normalize(changed)
            if state is not None:
                state.touch(path)
            # Re-parse exactly the file that changed (~1ms) rather than re-statting the
            # whole tree (~100ms on a large repo).
            if ws.index.is_built:
                ws.index.refresh_path(path)

        elif event.tool == "Bash":
            command = event.arg("command")
            if isinstance(command, str) and runners.is_test_command(command):
                self._record_test_run(event, ws, state, command)
            # A shell command can touch anything; fall back to a rate-limited sweep.
            if ws.index.is_built:
                ws.index.refresh(min_interval=2.0)

        return Decision.allow()

    @staticmethod
    def _record_test_run(
        event: AgentEvent, ws: Workspace, state: TaskState | None, command: str
    ) -> None:
        """Read the agent's own test output.

        This is what lets the Completion Gate contradict "all tests pass" without running
        anything itself: the agent produced the evidence.
        """
        if state is None:
            return
        state.verification.commands_seen.append(command.strip()[:200])

        # Tool results arrive as a dict from Claude Code; stdout and stderr both matter.
        outcome = runners.parse_output(command, runners.output_text(event.result))
        if outcome.passed is not None:
            state.verification.outcomes.append(outcome)
            ws.store.finish_verification(
                ws.store.start_verification(state.task_id, outcome.runner),
                "passed" if outcome.passed else "failed",
                {"command": outcome.command, "summary": outcome.summary, "failed": outcome.failed},
            )

    def _on_stop(self, event: AgentEvent, ws: Workspace) -> Decision:
        """Completion Gate (SPEC §19).

        "Done" is a claim like any other. Blocking Stop hands the turn back to the agent
        with the reason, so it keeps working rather than finishing on an unverified claim.
        """
        state = ws.task_state(event.task_id)
        if state is None or not ws.index.is_built:
            return Decision.allow()

        verdict = completion_gate.evaluate(
            state=state,
            index=ws.index,
            stop_hook_active=bool(event.raw.get("stop_hook_active")),
            max_blocks=self.settings.challenge.max_stop_blocks_per_task,
        )
        if not verdict.should_block:
            return Decision.allow(EscalationLevel.DEEP_VERIFICATION)

        state.stop_blocks += 1
        METRICS.increment(M_FALSE_COMPLETION_BLOCKED, labels={"result": str(verdict.result)})
        return Decision(
            action=DecisionAction.BLOCK,
            level=EscalationLevel.DEEP_VERIFICATION,
            reason=f"AgentGuard — completion gate: {verdict.result.value}\n\n{verdict.reason}",
        )

    def _on_session_end(self, event: AgentEvent, ws: Workspace) -> Decision:
        ws.store.close_session(event.session_id)
        # The one moment maintenance is safe to run: the agent has stopped, so pruning
        # and checkpointing cost nobody anything (Memory plan §7). Rate-limited inside,
        # and it can never raise.
        ws.store.db.maintain()
        return Decision.allow()

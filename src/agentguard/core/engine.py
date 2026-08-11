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

from agentguard.core.config import Settings
from agentguard.core.enums import EventType
from agentguard.core.events import AgentEvent
from agentguard.core.metrics import M_DECISION, M_HOOK_LATENCY, METRICS, Timer
from agentguard.core.models import Decision
from agentguard.core.store import Store
from agentguard.repo.index import RepoIndex

log = logging.getLogger(__name__)


class Workspace:
    """Per-repository state: the decision store and the repository index."""

    def __init__(self, root: Path, settings: Settings) -> None:
        self.root = root
        self.settings = settings
        self.store = Store.for_workspace(root)
        # Built in the background: a large monorepo takes seconds, and no developer
        # should wait on it. Until it is ready every query answers "unknown", which
        # resolves to ALLOW (SPEC §39).
        self.index = RepoIndex(root, settings.index)
        self.index.build_async()

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
                event.task_id = event.task_id or ws.store.current_task_id(event.session_id)
                ws.store.record_event(event)
                decision.decision_id = ws.store.record_decision(event, decision) or decision.decision_id
                # Latency lives in the in-memory collector only. Persisting it here would
                # add a third commit per tool call for data already held in METRICS.
            except Exception:
                log.exception("agentguard: failed to persist event/decision")

        return decision

    # -- handlers -----------------------------------------------------------------
    #
    # Phase 0 wires the skeleton; each handler is filled in by a later phase:
    #   user_prompt   -> Phase 2 (Intent Gateway, Complexity, Planning Governor)
    #   pre_tool_use  -> Phase 3 + 4 (Evidence Engine, Action Validator)
    #   post_tool_use -> Phase 4 (scope ledger, async verification)
    #   stop          -> Phase 4 (Completion Gate)

    def _on_session_start(self, event: AgentEvent, ws: Workspace) -> Decision:
        ws.store.open_session(event.session_id, event.agent, str(ws.root))
        # Creating the Workspace already kicked off the build; this hook has a 20s
        # budget, so give it a moment to finish while the session is still starting up.
        ws.index.ready(timeout=5.0)
        return Decision.allow()

    def _on_user_prompt(self, event: AgentEvent, ws: Workspace) -> Decision:
        ws.store.open_session(event.session_id, event.agent, str(ws.root))
        task_id = ws.store.create_task(event.session_id, event.prompt_text or "")
        event.task_id = task_id
        return Decision.allow()

    def _on_pre_tool_use(self, event: AgentEvent, ws: Workspace) -> Decision:
        # SPEC §7/§8: read-only tools never leave Level 0. This short-circuit is what
        # keeps ordinary exploration free.
        if event.is_read_only:
            return Decision.allow()
        return Decision.allow()

    def _on_post_tool_use(self, event: AgentEvent, ws: Workspace) -> Decision:
        # Keep the evidence base honest: re-parse exactly the file that just changed
        # (~1ms) rather than re-statting the whole tree (~100ms on a large repo).
        if ws.index.is_built:
            changed = event.arg("file_path") or event.arg("notebook_path")
            if isinstance(changed, str) and changed:
                ws.index.refresh_path(changed)
            elif event.tool == "Bash":
                # A shell command can touch anything; fall back to a rate-limited sweep.
                ws.index.refresh(min_interval=2.0)
        return Decision.allow()

    def _on_stop(self, event: AgentEvent, ws: Workspace) -> Decision:
        return Decision.allow()

    def _on_session_end(self, event: AgentEvent, ws: Workspace) -> Decision:
        ws.store.close_session(event.session_id)
        return Decision.allow()

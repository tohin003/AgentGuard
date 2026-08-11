"""Normalized, agent-agnostic event model (SPEC §23).

    "The core does not care which agent generated the event."

Adapters (Claude Code, Cursor, Codex, ...) translate their host's native payloads into
`AgentEvent`, and translate a `Decision` back into whatever their host understands.
Nothing below this line may branch on `event.agent`.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentguard.core.enums import EventType

# Tools that cannot change anything. Used to short-circuit the validation pipeline at
# Level 0 so ordinary exploration costs essentially nothing (SPEC §7, §8).
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {"Read", "Glob", "Grep", "WebFetch", "WebSearch", "NotebookRead", "TodoWrite", "ListMcpResources"}
)

# Tools that write to the workspace. These get the full pipeline.
MUTATING_TOOLS: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


class AgentEvent(BaseModel):
    """One thing an agent did or is about to do."""

    model_config = ConfigDict(extra="allow")

    event: EventType
    agent: str = "unknown"
    workspace: str = "."
    session_id: str = ""
    task_id: str | None = None

    # Tool events
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_use_id: str | None = None
    result: Any = None

    # Prompt / message events
    prompt_text: str | None = None
    last_assistant_message: str | None = None

    # Host-specific extras kept verbatim so adapters can round-trip without loss.
    raw: dict[str, Any] = Field(default_factory=dict)

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = Field(default_factory=time.time)

    @property
    def is_read_only(self) -> bool:
        return self.tool in READ_ONLY_TOOLS

    @property
    def is_mutating(self) -> bool:
        return self.tool in MUTATING_TOOLS

    def arg(self, name: str, default: Any = None) -> Any:
        return self.arguments.get(name, default)

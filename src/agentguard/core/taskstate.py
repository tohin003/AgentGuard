"""Per-task working state (SPEC §18, §19).

Scope checking and the Completion Gate both need the same thing: what has actually
happened during *this* task. Which files were touched, which test commands ran and what
they said, how many times the gate has already spoken.

Lives in memory, on the daemon, for the life of the task. Nothing here needs to survive a
restart — a lost task simply means the next action is evaluated without history, which
degrades to ALLOW.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from agentguard.intent.models import TaskSpec
from agentguard.verify.runners import VerificationState

# A task that has been running long enough to have touched this many files without any
# of them being in the expected scope is not the task that was asked for.
DEFAULT_UNRELATED_LIMIT = 5


@dataclass(slots=True)
class TaskState:
    task_id: str
    spec: TaskSpec
    started_at: float = field(default_factory=time.time)
    # The host session owns the task.  Keeping this alongside the in-memory state lets
    # the engine reject a stale or forged task id from another concurrent session.
    session_id: str = ""

    touched_files: set[str] = field(default_factory=set)
    deleted_files: set[str] = field(default_factory=set)
    verification: VerificationState = field(default_factory=VerificationState)

    # How many times the Completion Gate has held this task open. Capped, so a
    # disagreement between AgentGuard and the agent cannot become a loop (SPEC §19).
    stop_blocks: int = 0
    scope_challenged: bool = False

    def touch(self, path: str) -> None:
        self.touched_files.add(path)

    @property
    def changed_files(self) -> set[str]:
        return self.touched_files | self.deleted_files

    def unrelated_files(self, blast: set[str] | None = None) -> set[str]:
        """Touched files that the task does not plausibly reach.

        "Plausibly" is generous on purpose: the expected scope, anything importing or
        imported by it, and its tests all count as related. SPEC §18's example is an agent
        editing *17 unrelated files* while fixing login validation — the signal worth
        catching is wholesale drift, not an adjacent helper.
        """
        if not self.spec.expected_scope:
            return set()  # nothing was resolved, so nothing can be called unrelated
        related = set(self.spec.expected_scope) | (blast or set())
        return {path for path in self.touched_files if path not in related}

"""Completion Gate (SPEC §19, §20).

    The agent should not simply say: "Done."

`Stop` fires when the agent believes it has finished. This decides whether the evidence
agrees, and returns one of PASS / INCOMPLETE / VERIFICATION_FAILED /
HUMAN_REVIEW_REQUIRED.

The gate is built to be *quiet by default*, because a completion gate that fires on
ordinary turns is the most annoying thing this project could ship. It holds the turn open
only when there is positive evidence of a problem:

* code changed and the changed files no longer parse
* the agent ran tests, and the output says they failed
* code changed, the project has tests covering it, and nothing was run

Everything else passes silently — including every turn that changed no code, every project
without a test runner, and every case where the evidence is merely unclear. "Could not
tell" is never treated as failure.

Loop safety is structural. `stop_hook_active` tells us a Stop hook is already holding the
session open, and a per-task cap bounds how many times the gate may speak at all. A
disagreement between AgentGuard and the agent must end in the agent proceeding, not in a
loop (SPEC §39).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentguard.core.enums import GateResult
from agentguard.core.models import EvidenceRef
from agentguard.core.taskstate import TaskState
from agentguard.repo.index import RepoIndex
from agentguard.verify import runners, static


@dataclass(slots=True)
class GateVerdict:
    result: GateResult
    reason: str = ""
    evidence: list[EvidenceRef] = field(default_factory=list)

    @property
    def should_block(self) -> bool:
        return self.result is not GateResult.PASS


def evaluate(
    state: TaskState | None,
    index: RepoIndex,
    stop_hook_active: bool = False,
    max_blocks: int = 2,
) -> GateVerdict:
    """Decide whether this turn may end. Never raises."""
    if state is None:
        return GateVerdict(GateResult.PASS)

    # Loop safety comes first: whatever we might have said, we have said enough.
    if state.stop_blocks >= max_blocks:
        return GateVerdict(GateResult.PASS, "completion-gate budget spent")
    if stop_hook_active and state.stop_blocks >= 1:
        return GateVerdict(GateResult.PASS, "a stop hook is already holding this turn")

    changed = {path for path in state.changed_files if _is_source(path, index)}
    if not changed:
        return GateVerdict(GateResult.PASS, "no source changes to verify")

    # 1. Does it parse? The cheapest and least arguable failure there is.
    problems = static.check_files(index.root, state.touched_files)
    if problems:
        listing = "; ".join(f"{p.path}:{p.line} {p.message}" for p in problems[:3])
        return GateVerdict(
            GateResult.VERIFICATION_FAILED,
            reason=(
                f"The change leaves {len(problems)} file(s) unparsable: {listing}. "
                "Fix the syntax before finishing."
            ),
            evidence=[
                EvidenceRef(source="static", path=p.path, line=p.line, note=p.message)
                for p in problems[:5]
            ],
        )

    # 2. Did the tests the agent ran actually pass?
    verification = state.verification
    if verification.any_failed:
        failure = verification.latest_failure()
        return GateVerdict(
            GateResult.VERIFICATION_FAILED,
            reason=(
                f"The test run reported failures — `{failure.command}`"
                + (f": {failure.summary}" if failure.summary else "")
                + ". The task is not complete while its own tests are failing."
            ),
            evidence=[EvidenceRef(source="runtime", note=f"{failure.runner}: {failure.summary}")],
        )

    if verification.all_passed:
        return GateVerdict(GateResult.PASS, "tests ran and passed")

    # 3. Nothing was run. Only a problem if there was something to run.
    available = runners.detect_runners(index)
    if not available:
        return GateVerdict(GateResult.PASS, "no test runner in this project")

    covering = runners.affected_tests(index, changed)
    if not covering:
        # Changed code that nothing covers. Worth saying once, but it is a gap in the
        # project, not a failure by the agent — so it does not hold the turn open.
        return GateVerdict(GateResult.PASS, "no existing tests cover the changed files")

    if verification.commands_seen and not verification.outcomes:
        # A test command ran but its result was unreadable. Unclear is not failed.
        return GateVerdict(GateResult.PASS, "test output could not be interpreted")

    command = runners.suggested_command(available, covering)
    return GateVerdict(
        GateResult.INCOMPLETE,
        reason=(
            f"{len(changed)} file(s) changed and no tests were run, but "
            f"{len(covering)} test file(s) cover this code: {', '.join(covering[:4])}. "
            f"Run them before finishing — `{command}` — and report what they said."
        ),
        evidence=[EvidenceRef(source="tests", path=path) for path in covering[:5]],
    )


def _is_source(path: str, index: RepoIndex) -> bool:
    """Documentation and configuration changes do not need a test run."""
    record = index.files.get(path)
    if record is None:
        return Path(path).suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"}
    return record.parsable

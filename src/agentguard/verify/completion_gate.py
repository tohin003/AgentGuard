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

Seeing and speaking are separate (Phase 7)
------------------------------------------
Every path through this module now also produces `Finding`s tagged with the SPEC §3
failure mode they evidence, and those findings are produced **before** rationing is
applied. Loop safety, the per-task cap and observe-only mode all govern whether the gate
*speaks*; none of them should govern what the census *counts*. Observation is free.

One observation here never speaks at all: §3.15, "write insufficient tests". It is INFO
severity, so nothing surfaces it — it exists to be counted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentguard.core.enums import (
    ChallengeCategory,
    EscalationLevel,
    FailureMode,
    GateResult,
    Severity,
    Verdict,
)
from agentguard.core.models import EvidenceRef, Finding
from agentguard.core.taskstate import TaskState
from agentguard.repo.index import RepoIndex
from agentguard.verify import runners, static


@dataclass(slots=True)
class GateVerdict:
    result: GateResult
    reason: str = ""
    evidence: list[EvidenceRef] = field(default_factory=list)
    # What was observed, regardless of whether it will be said. Census input.
    findings: list[Finding] = field(default_factory=list)

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

    verdict = _assess(state, index)

    # Rationing is applied last, to the *speaking* only. Everything above has already
    # been observed and recorded.
    muted = _muted_reason(state, stop_hook_active, max_blocks)
    if verdict.should_block and muted:
        return GateVerdict(GateResult.PASS, reason=muted, findings=verdict.findings)
    return verdict


def _muted_reason(state: TaskState, stop_hook_active: bool, max_blocks: int) -> str:
    """Why the gate must stay quiet even though it has something to say."""
    if state.stop_blocks >= max_blocks:
        return "completion-gate budget spent"
    if stop_hook_active and state.stop_blocks >= 1:
        return "a stop hook is already holding this turn"
    return ""


def _assess(state: TaskState, index: RepoIndex) -> GateVerdict:
    """What the evidence says, before any question of whether to say it."""
    changed = {path for path in state.changed_files if _is_source(path, index)}
    if not changed:
        return GateVerdict(GateResult.PASS, "no source changes to verify")

    # Independent of the blocking assessment: an agent can run the suite, watch it pass,
    # and still have left new code untested (SPEC §3, "write insufficient tests").
    observations = _insufficient_tests(state, index, changed)

    # 1. Does it parse? The cheapest and least arguable failure there is.
    problems = static.check_files(index.root, state.touched_files)
    if problems:
        listing = "; ".join(f"{p.path}:{p.line} {p.message}" for p in problems[:3])
        evidence = [
            EvidenceRef(source="static", path=p.path, line=p.line, note=p.message)
            for p in problems[:5]
        ]
        reason = (
            f"The change leaves {len(problems)} file(s) unparsable: {listing}. "
            "Fix the syntax before finishing."
        )
        return GateVerdict(
            GateResult.VERIFICATION_FAILED,
            reason=reason,
            evidence=evidence,
            findings=[
                _finding(
                    FailureMode.FALSE_COMPLETION,
                    Severity.HIGH,
                    subject="unparsable-on-completion",
                    summary=f"finished with {len(problems)} file(s) that no longer parse",
                    detail=listing,
                    evidence=evidence,
                ),
                *observations,
            ],
        )

    # 2. Did the tests the agent ran actually pass?
    verification = state.verification
    if verification.any_failed:
        failure = verification.latest_failure()
        evidence = [EvidenceRef(source="runtime", note=f"{failure.runner}: {failure.summary}")]
        return GateVerdict(
            GateResult.VERIFICATION_FAILED,
            reason=(
                f"The test run reported failures — `{failure.command}`"
                + (f": {failure.summary}" if failure.summary else "")
                + ". The task is not complete while its own tests are failing."
            ),
            evidence=evidence,
            findings=[
                _finding(
                    FailureMode.FALSE_COMPLETION,
                    Severity.HIGH,
                    subject="failing-tests-on-completion",
                    summary=f"finished while `{failure.command}` was reporting failures",
                    detail=failure.summary,
                    evidence=evidence,
                ),
                *observations,
            ],
        )

    if verification.all_passed:
        return GateVerdict(GateResult.PASS, "tests ran and passed", findings=observations)

    # 3. Nothing was run. Only a problem if there was something to run *and* the
    #    developer did not say not to.
    if state.spec.tests_waived:
        return GateVerdict(
            GateResult.PASS, "the request explicitly waived running tests", findings=observations
        )

    available = runners.detect_runners(index)
    if not available:
        return GateVerdict(GateResult.PASS, "no test runner in this project", findings=observations)

    covering = runners.affected_tests(index, changed)
    if not covering:
        # Changed code that nothing covers. Worth saying once, but it is a gap in the
        # project, not a failure by the agent — so it does not hold the turn open. The
        # §3.15 observation above has already recorded it.
        return GateVerdict(
            GateResult.PASS, "no existing tests cover the changed files", findings=observations
        )

    if verification.commands_seen and not verification.outcomes:
        # A test command ran but its result was unreadable. Unclear is not failed.
        return GateVerdict(
            GateResult.PASS, "test output could not be interpreted", findings=observations
        )

    command = runners.suggested_command(available, covering)
    evidence = [EvidenceRef(source="tests", path=path) for path in covering[:5]]
    return GateVerdict(
        GateResult.INCOMPLETE,
        reason=(
            f"{len(changed)} file(s) changed and no tests were run, but "
            f"{len(covering)} test file(s) cover this code: {', '.join(covering[:4])}. "
            f"Run them before finishing — `{command}` — and report what they said."
        ),
        evidence=evidence,
        findings=[
            _finding(
                FailureMode.UNVERIFIED_CHANGE,
                Severity.MEDIUM,
                subject="no-test-run",
                summary=f"{len(changed)} file(s) changed and no tests were run",
                detail=f"{len(covering)} test file(s) cover this code: {', '.join(covering[:4])}",
                evidence=evidence,
            ),
            *observations,
        ],
    )


# -- §3.15 "write insufficient tests" ---------------------------------------------


def _insufficient_tests(state: TaskState, index: RepoIndex, changed: set[str]) -> list[Finding]:
    """Changed code that nothing tests, in a project that does test its code.

    Recorded, never raised — hence INFO. Three conditions, and all three are needed to
    keep this from becoming a complaint about the repository rather than an observation
    about the change:

    * **The project tests itself.** A runner *and* existing test files. In a repository
      with no tests, "no test was written" is the status quo, not a finding.
    * **The agent wrote no tests during this task.** If it touched a test file, it
      engaged with testing and this detector has nothing to say about whether it did so
      sufficiently — that judgement is not deterministic.
    * **Some changed file has no coverage at all**, directly or through a dependent.
      Editing well-covered code without adding a test is ordinary work.

    What this proves is narrow, and the census says so: untested code was changed, not
    that the tests written were inadequate.
    """
    if not any(record.is_test for record in index.files.values()):
        return []
    if not runners.detect_runners(index):
        return []
    if any(_looks_like_test(path, index) for path in state.touched_files):
        return []

    uncovered = sorted(path for path in changed if not _has_any_coverage(path, index))
    if not uncovered:
        return []

    return [
        _finding(
            FailureMode.INSUFFICIENT_TESTS,
            Severity.INFO,
            subject="untested-change",
            summary=f"{len(uncovered)} changed file(s) have no tests, and none were written",
            detail=", ".join(uncovered[:6]) + ("…" if len(uncovered) > 6 else ""),
            evidence=[EvidenceRef(source="tests", path=path) for path in uncovered[:5]],
        )
    ]


def _looks_like_test(path: str, index: RepoIndex) -> bool:
    record = index.files.get(index.normalize(path))
    if record is not None:
        return record.is_test
    # A test file created during this task and not yet indexed still counts as the agent
    # having written tests. Missing that would turn writing a test into a finding.
    from agentguard.repo import scanner

    return scanner.is_test_path(index.normalize(path))


def _has_any_coverage(path: str, index: RepoIndex) -> bool:
    if index.tests_for(path):
        return True
    return any(index.tests_for(dependent) for dependent in index.dependents_of(path))


def _finding(
    mode: FailureMode,
    severity: Severity,
    subject: str,
    summary: str,
    detail: str = "",
    evidence: list[EvidenceRef] | None = None,
) -> Finding:
    return Finding(
        category=ChallengeCategory.TESTABILITY,
        verdict=Verdict.INSUFFICIENT_EVIDENCE,
        failure_mode=mode,
        severity=severity,
        subject=subject,
        summary=summary,
        detail=detail,
        evidence=evidence or [],
        level=EscalationLevel.DEEP_VERIFICATION,
    )


def _is_source(path: str, index: RepoIndex) -> bool:
    """Documentation and configuration changes do not need a test run."""
    record = index.files.get(path)
    if record is None:
        return Path(path).suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"}
    return record.parsable

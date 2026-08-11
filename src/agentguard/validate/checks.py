"""The checks behind SPEC §18's validation pipeline.

    Agent proposal -> Evidence -> Scope -> Architecture -> Risk -> Complexity -> Decision

Evidence is Phase 3's engine; this module supplies the rest. Each check is a small
function returning findings, tagged with the escalation level it costs to run so the
validator can short-circuit (SPEC §7).

Every check here is written to under-report. Scope creep needs a *pattern* before it is
worth mentioning, and a dangerous command has to be unambiguously dangerous. The cost of
a missed finding is one imperfect edit; the cost of a false one is the developer turning
the whole layer off.
"""

from __future__ import annotations

import re

from agentguard.core.enums import (
    ChallengeCategory,
    EscalationLevel,
    PlanningDepth,
    Severity,
    Verdict,
)
from agentguard.core.events import AgentEvent
from agentguard.core.models import EvidenceRef, Finding
from agentguard.core.taskstate import DEFAULT_UNRELATED_LIMIT, TaskState
from agentguard.repo.index import RepoIndex

# A DIRECT-band task that has rewritten this much of the repository is not the task that
# was described, whatever the file paths say.
DIRECT_BAND_FILE_LIMIT = 8


# -- scope (SPEC §18) -------------------------------------------------------------


def scope_creep(state: TaskState, index: RepoIndex) -> list[Finding]:
    """SPEC §18's worked example.

        Task: Fix login validation.
        Agent attempts: Modify 17 unrelated files.
        Expected scope: authentication module -> SCOPE VIOLATION -> CHALLENGE

    Raised once per task. After the agent has heard it and chosen to continue, repeating
    it is nagging (SPEC §17).
    """
    if state.scope_challenged:
        return []

    spec = state.spec
    findings: list[Finding] = []

    # Everything reachable from the expected scope counts as related: its importers, its
    # imports, and its tests. Only genuine outliers are worth raising.
    related: set[str] = set(spec.expected_scope)
    for path in spec.expected_scope:
        related |= index.blast_radius(path, depth=2)
        related |= index.tests_for(path)
        related |= {record.resolved for record in index.imports_of(path) if record.resolved}

    unrelated = state.unrelated_files(related)
    if len(unrelated) >= DEFAULT_UNRELATED_LIMIT:
        findings.append(
            Finding(
                category=ChallengeCategory.SCOPE,
                verdict=Verdict.INSUFFICIENT_EVIDENCE,
                severity=Severity.HIGH,
                subject="scope",
                summary=(
                    f"{len(unrelated)} file(s) changed that the task does not reach"
                ),
                detail=(
                    f"The request was: \"{spec.goal or spec.prompt[:120]}\". Its evidence points at "
                    f"{', '.join(sorted(spec.expected_scope)[:3])}"
                    f"{'…' if len(spec.expected_scope) > 3 else ''}. "
                    f"These changes are outside that and outside anything connected to it: "
                    f"{', '.join(sorted(unrelated)[:6])}"
                    f"{'…' if len(unrelated) > 6 else ''}."
                ),
                evidence=[EvidenceRef(source="filesystem", path=p) for p in sorted(unrelated)[:6]],
                suggestion=(
                    "Either explain what connects these to the task, or split them into a "
                    "separate change."
                ),
                level=EscalationLevel.REPOSITORY,
            )
        )
        return findings

    # A proportionality check that does not depend on scope resolving at all.
    if (
        spec.complexity.depth is PlanningDepth.DIRECT
        and len(state.touched_files) >= DIRECT_BAND_FILE_LIMIT
    ):
        findings.append(
            Finding(
                category=ChallengeCategory.SCOPE,
                verdict=Verdict.OVERENGINEERED,
                severity=Severity.MEDIUM,
                subject="proportionality",
                summary=(
                    f"{len(state.touched_files)} files changed for a task assessed as "
                    f"direct implementation"
                ),
                detail=(
                    f"Complexity was {spec.complexity.score:.0f}/100. Either the task is larger "
                    "than it appeared — in which case say so — or this change has grown beyond it."
                ),
                evidence=[
                    EvidenceRef(source="filesystem", path=p)
                    for p in sorted(state.touched_files)[:6]
                ],
                level=EscalationLevel.DETERMINISTIC,
            )
        )

    return findings


# -- risky commands (SPEC §18 risk check) -----------------------------------------

# Only patterns that are dangerous regardless of context. `rm -rf ./build` is ordinary
# work; `rm -rf /` and `rm -rf $UNSET_VAR` are not.
_DANGEROUS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*[rR])\s+(/|~|\$HOME|/\*)(\s|$)"),
        "recursive delete of a root or home path",
        "critical",
    ),
    (
        re.compile(r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*f?\s+\"?\$\{?\w+\}?\"?(/|\s|$)"),
        "recursive delete of an unvalidated variable path — expands to `/` if it is unset",
        "high",
    ),
    (re.compile(r"\bgit\s+push\b[^\n]*\s(--force|-f)\b(?![-\w])"), "force push", "high"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "hard reset discards uncommitted work", "high"),
    (re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*f"), "git clean removes untracked files", "high"),
    (re.compile(r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE), "destructive SQL", "critical"),
    (re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE), "destructive SQL", "high"),
    (
        re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba)?sh\b"),
        "piping a download straight into a shell",
        "high",
    ),
    (re.compile(r"\bchmod\s+-R\s+777\b"), "world-writable permissions applied recursively", "high"),
    (re.compile(r"\b(mkfs|dd)\s+[^\n]*of=/dev/"), "writing directly to a block device", "critical"),
    (re.compile(r"\bgit\s+push\b[^\n]*\bmain\b[^\n]*\s(--force|-f)\b"), "force push to main", "critical"),
)


def risky_command(event: AgentEvent) -> list[Finding]:
    """Commands worth a human's attention before they run.

    These produce REQUEST_REVIEW rather than a block. AgentGuard is not the authority on
    whether a force push is appropriate — the developer is — so the right move is to
    surface it, not to overrule it.
    """
    if event.tool != "Bash":
        return []
    command = event.arg("command")
    if not isinstance(command, str) or not command.strip():
        return []

    findings: list[Finding] = []
    for pattern, description, severity in _DANGEROUS:
        match = pattern.search(command)
        if not match:
            continue
        findings.append(
            Finding(
                category=ChallengeCategory.RISK,
                verdict=Verdict.REQUIRES_HUMAN,
                severity=Severity(severity),
                subject=match.group(0)[:80],
                summary=f"Irreversible command: {description}",
                detail=f"`{command.strip()[:200]}`",
                evidence=[EvidenceRef(source="runtime", note=command.strip()[:200])],
                suggestion="Confirm this is intended, or narrow it to the specific target.",
                level=EscalationLevel.DETERMINISTIC,
            )
        )
        break  # one report per command is enough

    return findings


# -- repository consistency (SPEC §16 CONSISTENCY) --------------------------------


def pattern_consistency(event: AgentEvent, index: RepoIndex, path: str) -> list[Finding]:
    """Does a new file sit where its siblings sit?

    Weak evidence by nature, so this is INFO: recorded, never raised. It exists to be
    available to `agentguard log` and to the memory promotion in Phase 9, where a repeated
    pattern across sessions is worth more than any single instance.
    """
    if event.tool != "Write" or index.file_exists(path):
        return []

    directory = path.rsplit("/", 1)[0] if "/" in path else ""
    if not directory:
        return []

    siblings = [p for p in index.files if p.startswith(f"{directory}/") and p != path]
    if siblings:
        return []

    return [
        Finding(
            category=ChallengeCategory.CONSISTENCY,
            verdict=Verdict.SUPPORTED_WITH_RISK,
            severity=Severity.INFO,
            subject=path,
            summary=f"new file in a directory with no existing siblings: {directory}/",
            evidence=[EvidenceRef(source="filesystem", path=path)],
            level=EscalationLevel.DETERMINISTIC,
        )
    ]

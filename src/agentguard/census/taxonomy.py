"""SPEC §3's failure modes, and what AgentGuard can honestly say about each.

The census exists because the project guessed once and guessed wrong. The evidence engine
detects hallucinated references at 90% recall and 100% precision; the benchmark then found
that current models, unguarded, hallucinate approximately never
(`docs/BENCH-mutation.md`). Excellent engineering aimed at the wrong one of SPEC §3's
seventeen failures. So this time the target gets counted before anything is built on it.

Which makes one property of the report load-bearing:

    **A mode with no detector is reported as "not instrumented", never as zero.**

"Complete evidence or silence" is the rule the Evidence Engine follows about a claim; the
same rule has to apply to the census's own claims, or it repeats the original mistake in a
new form — a confident number that nothing supports. Six of the seventeen have no
deterministic detector, and the report says so in those words.

Each entry therefore carries `proves`: what the detector actually establishes, which is
usually narrower than the SPEC bullet it sits under. "Untested code was changed" is not
"the tests written were inadequate", and the report should not let a reader slide from one
to the other.

**Seventeen, not fourteen.** `IMPLEMENTATION_PLAN.md` and the Phase 7 brief both say
fourteen. SPEC §3 has seventeen bullets. Counted.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentguard.core.enums import FailureMode


@dataclass(frozen=True, slots=True)
class ModeSpec:
    """One SPEC §3 failure mode and its instrumentation status."""

    mode: FailureMode
    spec_text: str  # the SPEC §3 bullet, verbatim
    detector: str  # where the observation comes from, or "" when there is none
    proves: str  # what an observation actually establishes — often less than the bullet

    @property
    def instrumented(self) -> bool:
        return bool(self.detector)


# In SPEC §3's own order, so the table can be checked against the document line by line.
TAXONOMY: tuple[ModeSpec, ...] = (
    ModeSpec(
        FailureMode.HALLUCINATED_FILE,
        "hallucinate files",
        "evidence engine — an internal module import that resolves to no file",
        "a module under one of this repository's own top-level packages does not exist",
    ),
    ModeSpec(
        FailureMode.HALLUCINATED_API,
        "hallucinate APIs",
        "evidence engine — attribute on a fully-resolved type",
        "the receiver's type is known, its file parsed cleanly, every base resolved, and "
        "the member is absent",
    ),
    ModeSpec(
        FailureMode.INVENTED_FUNCTION,
        "invent functions",
        "evidence engine — `from <our module> import <name>`",
        "the named module neither defines nor re-exports that name",
    ),
    ModeSpec(
        FailureMode.INVENTED_LIBRARY,
        "invent libraries",
        "evidence engine — undeclared third-party import",
        "the package is not in the standard library, not in any manifest, and not "
        "imported elsewhere in the repository. It cannot separate an invented library "
        "from a real but undeclared one, which is why it never challenges",
    ),
    ModeSpec(
        FailureMode.UNSUPPORTED_ASSUMPTION,
        "make unsupported assumptions",
        "",
        "",
    ),
    ModeSpec(
        FailureMode.MISREAD_INTENT,
        "misunderstand developer intent",
        "",
        "",
    ),
    ModeSpec(
        FailureMode.OVERPLANNED_TRIVIAL,
        "over-plan trivial tasks",
        "action validator — a DIRECT-band task that changed 8+ files",
        "over-*execution*, which is the visible end of over-planning. Plan length and "
        "tool counts are measured directly in Phase 9",
    ),
    ModeSpec(
        FailureMode.UNDERPLANNED_COMPLEX,
        "under-plan complex tasks",
        "",
        "",
    ),
    ModeSpec(
        FailureMode.UNNECESSARY_ABSTRACTION,
        "introduce unnecessary abstractions",
        "",
        "",
    ),
    ModeSpec(
        FailureMode.UNRELATED_FILES_MODIFIED,
        "modify unrelated files",
        "action validator — scope creep against the task's resolved scope",
        "5+ files changed that are outside the task's targets and outside their import "
        "graph and tests",
    ),
    ModeSpec(
        FailureMode.UNNECESSARY_DEPENDENCY,
        "introduce unnecessary dependencies",
        "evidence engine (install commands) + action validator (manifest diff)",
        "a package became a dependency that was not one before. Only where an "
        "interchangeable package is already declared does the evidence reach "
        "*unnecessary*; otherwise it establishes *new*",
    ),
    ModeSpec(
        FailureMode.IGNORED_REPO_PATTERN,
        "ignore existing repository patterns",
        "action validator — naming and placement conventions on file creation",
        "5+ sibling files agree on a convention and the new file does not follow it",
    ),
    ModeSpec(
        FailureMode.OVERLOOKED_REGRESSION,
        "overlook regressions",
        "",
        "",
    ),
    ModeSpec(
        FailureMode.FALSE_COMPLETION,
        "incorrectly claim that a task is complete",
        "completion gate — finishing with failing tests or unparsable files",
        "the agent tried to end the turn while evidence it produced itself said the work "
        "was broken",
    ),
    ModeSpec(
        FailureMode.INSUFFICIENT_TESTS,
        "write insufficient tests",
        "completion gate — changed code with no coverage and no test written",
        "untested code was changed in a project that does test its code. It says nothing "
        "about the quality of tests that were written",
    ),
    ModeSpec(
        FailureMode.UNVERIFIED_CHANGE,
        "fail to verify their own changes",
        "completion gate — code changed, covering tests exist, none were run",
        "tests that cover the change existed and were not executed before the agent "
        "tried to finish",
    ),
    ModeSpec(
        FailureMode.CONTINUED_ON_BAD_ASSUMPTION,
        "confidently continue after making an incorrect assumption",
        "",
        "",
    ),
)

BY_MODE: dict[FailureMode, ModeSpec] = {entry.mode: entry for entry in TAXONOMY}

# Why the six uninstrumented modes have no detector. Printed with the census, because
# "no detector" invites the reader to assume an oversight, and in most of these cases it
# is a deliberate refusal to guess.
NOT_INSTRUMENTED_BECAUSE: dict[FailureMode, str] = {
    FailureMode.UNSUPPORTED_ASSUMPTION: (
        "every instance specific enough to detect is already counted under a narrower "
        "mode above — a separate detector would double-count the same observations"
    ),
    FailureMode.MISREAD_INTENT: (
        "requires knowing what the developer meant. That is a semantic judgement, and "
        "AgentGuard owns no LLM to make one (SPEC §6)"
    ),
    FailureMode.UNDERPLANNED_COMPLEX: (
        "'not enough planning' has no deterministic signature. Phase 9 measures planning "
        "depth against complexity directly"
    ),
    FailureMode.UNNECESSARY_ABSTRACTION: (
        "distinguishing a justified abstraction from an unnecessary one is exactly the "
        "judgement SPEC §17 says to hand back to the host, not to make here"
    ),
    FailureMode.OVERLOOKED_REGRESSION: (
        "candidate signal exists — a test suite that passed earlier in the task and "
        "fails later — but test output carries counts, not test names, so a differing "
        "subset is indistinguishable from a regression. Not built rather than built wrong"
    ),
    FailureMode.CONTINUED_ON_BAD_ASSUMPTION: (
        "requires the agent to have been challenged and to have proceeded anyway. "
        "Observe-only mode never challenges, so the census cannot observe this by "
        "construction"
    ),
}


def instrumented() -> tuple[ModeSpec, ...]:
    return tuple(entry for entry in TAXONOMY if entry.instrumented)


def uninstrumented() -> tuple[ModeSpec, ...]:
    return tuple(entry for entry in TAXONOMY if not entry.instrumented)

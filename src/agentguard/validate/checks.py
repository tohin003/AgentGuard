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
from pathlib import Path

from agentguard.core.enums import (
    ChallengeCategory,
    EscalationLevel,
    FailureMode,
    PlanningDepth,
    Severity,
    Verdict,
)
from agentguard.core.events import AgentEvent
from agentguard.core.models import EvidenceRef, Finding
from agentguard.core.taskstate import DEFAULT_UNRELATED_LIMIT, TaskState
from agentguard.repo import manifests, scanner
from agentguard.repo.index import RepoIndex

# A DIRECT-band task that has rewritten this much of the repository is not the task that
# was described, whatever the file paths say.
DIRECT_BAND_FILE_LIMIT = 8

# How much agreement it takes before a repository is deemed to *have* a convention.
# Below this, "the siblings do it differently" is a coincidence, not a pattern — and a
# census built on coincidences is worse than no census.
CONVENTION_MIN_SIBLINGS = 5


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
                failure_mode=FailureMode.UNRELATED_FILES_MODIFIED,  # SPEC §3
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
                # SPEC §3, "over-plan trivial tasks" — observed through what the agent
                # *did* rather than what it planned. A rename that rewrote nine files is
                # the visible end of over-planning, and it is the only end a
                # deterministic layer can see. §3's planning failure proper is measured
                # in Phase 9, against plan length and tool counts.
                failure_mode=FailureMode.OVERPLANNED_TRIVIAL,
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


def _is_guarded(command: str, match: re.Match[str]) -> bool:
    """Whether a variable-expansion delete has already been made safe.

    `[ -n "$OUT" ] && rm -rf "$OUT"` is the recommended form: it refuses to run rather
    than expanding to `/`. Flagging it would be a false positive on correct code, and
    would also stop MODIFY from ever being able to fix the unguarded version.
    """
    variables = re.findall(r"\$\{?(\w+)\}?", match.group(0))
    if not variables:
        return False
    return all(
        re.search(rf"\[\s*-[nz]\s+\"?\$\{{?{re.escape(name)}\}}?\"?\s*\]", command)
        for name in variables
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
        if "$" in match.group(0) and _is_guarded(command, match):
            continue
        findings.append(
            Finding(
                category=ChallengeCategory.RISK,
                verdict=Verdict.REQUIRES_HUMAN,
                # A force push may be exactly right. SPEC §3 does not list "ran a risky
                # command" as an agent failure, and stretching one to fit would put noise
                # in the census.
                failure_mode=FailureMode.NOT_A_FAILURE,
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


# -- repository consistency (SPEC §16 CONSISTENCY · SPEC §3 "ignore existing patterns") --
#
# Every finding below is INFO, which puts it under the challenge threshold: recorded in
# the decision log and counted by the census, never said to the agent. That is deliberate
# for Phase 7 and not merely caution. A convention detector that starts objecting during
# the census would change the behaviour the census exists to measure.
#
# The discipline that makes these worth counting at all: a convention has to be *proved*
# before it can be broken. Fewer than `CONVENTION_MIN_SIBLINGS` files agreeing is not a
# convention, and a file that carries no distinguishing marker cannot be said to violate
# one. Both rules cost recall and buy the only thing that matters here — a census nobody
# has to discount.


# Two questions about a filename, asked separately because a filename answers them
# separately. Lumping them into one four-way "snake / kebab / camel / pascal" verdict
# made `store.py` uncommitted — it is compatible with snake *and* kebab — and a directory
# of single-word lowercase modules is the commonest shape in Python. The detector was
# therefore silent on the clearest violation there is: `NewThing.py` among `store.py` and
# `engine.py`. Asked separately, every filename answers the casing question, and only
# multi-word ones need answer the separator question.


def _casing(stem: str) -> str:
    """`lower` or `mixed`. Every filename commits to this one."""
    return "lower" if stem == stem.lower() else "mixed"


def _separator(stem: str) -> str:
    """`_`, `-`, or "" when the name is a single word and so has no opinion.

    Dunder names are excluded by the caller: `__init__` is a language requirement, not a
    house style, and counting it as an underscore vote would invent a convention out of
    Python's own rules.
    """
    if "_" in stem:
        return "_"
    if "-" in stem:
        return "-"
    return ""


def _stem_and_suffix(path: str) -> tuple[str, str]:
    name = path.rsplit("/", 1)[-1]
    suffix = Path(name).suffix
    return name[: len(name) - len(suffix)] if suffix else name, suffix


def pattern_consistency(event: AgentEvent, index: RepoIndex, path: str) -> list[Finding]:
    """Does a new file follow the conventions its neighbours already keep?

    Runs on file *creation* only. That is where conventions get broken, it is rare enough
    to afford a scan of the file map, and it avoids re-judging files that already exist.
    """
    if event.tool != "Write" or index.file_exists(path):
        return []

    findings = _placement(index, path)
    findings.extend(_naming(index, path))
    return findings


def _placement(index: RepoIndex, path: str) -> list[Finding]:
    """A new file in a part of the tree its kind does not live in.

    Two cases, and only two, because both have unambiguous evidence: a test written
    outside wherever every other test lives, and a file created in a directory that does
    not exist yet.
    """
    directory = path.rsplit("/", 1)[0] if "/" in path else ""
    if not directory:
        return []

    if scanner.is_test_path(path):
        roots = {p.split("/")[0] for p, record in index.files.items() if record.is_test}
        count = sum(1 for record in index.files.values() if record.is_test)
        if count >= CONVENTION_MIN_SIBLINGS and len(roots) == 1:
            home = next(iter(roots))
            if path.split("/")[0] != home:
                return [
                    _consistency(
                        path,
                        f"new test outside `{home}/`, where all {count} existing tests live",
                        f"Every test file in this repository is under `{home}/`.",
                    )
                ]

    if not any(p.startswith(f"{directory}/") for p in index.files):
        return [
            _consistency(
                path,
                f"new file in a directory with no existing siblings: {directory}/",
                "",
            )
        ]
    return []


def _naming(index: RepoIndex, path: str) -> list[Finding]:
    """A new file named unlike every one of its same-extension siblings.

    Same extension only: `.tsx` components and `.css` files routinely follow different
    conventions in the same directory, and comparing across them invents violations.

    Unanimity is required, not a majority. One dissenting sibling means the repository
    tolerates both, and a house style that is merely popular is not one an agent can be
    said to have ignored.
    """
    stem, suffix = _stem_and_suffix(path)
    if not stem or not suffix:
        return []

    directory = path.rsplit("/", 1)[0] if "/" in path else ""
    prefix = f"{directory}/" if directory else ""

    casings: list[str] = []
    separators: list[str] = []
    for sibling in index.files:
        if sibling == path or not sibling.startswith(prefix):
            continue
        if "/" in sibling[len(prefix) :]:
            continue  # direct children only; a subtree is a different context
        sibling_stem, sibling_suffix = _stem_and_suffix(sibling)
        if sibling_suffix != suffix or not sibling_stem:
            continue
        casings.append(_casing(sibling_stem))
        if not sibling_stem.startswith("_"):
            separator = _separator(sibling_stem)
            if separator:
                separators.append(separator)

    verdict = _unanimous(casings, _casing(stem))
    if verdict:
        style = {"lower": "lowercase", "mixed": "mixed-case"}
        return [
            _consistency(
                path,
                f"`{stem}{suffix}` is {style[_casing(stem)]}; all {len(casings)} sibling "
                f"{suffix} files are {style[verdict]}",
                f"Existing convention in {prefix or './'}: {style[verdict]} filenames.",
            )
        ]

    separator = _separator(stem)
    verdict = _unanimous(separators, separator) if separator else ""
    if verdict:
        return [
            _consistency(
                path,
                f"`{stem}{suffix}` separates words with `{separator}`; all "
                f"{len(separators)} multi-word sibling {suffix} files use `{verdict}`",
                f"Existing convention in {prefix or './'}: `{verdict}` between words.",
            )
        ]
    return []


def _unanimous(votes: list[str], candidate: str) -> str:
    """The single value every sibling agreed on, when it differs from `candidate`.

    Empty when there are too few siblings, when they disagree, or when the new file
    already matches them — the three ways of having nothing to report.
    """
    if len(votes) < CONVENTION_MIN_SIBLINGS:
        return ""
    agreed = set(votes)
    if len(agreed) != 1:
        return ""
    only = agreed.pop()
    return "" if only == candidate else only


def _consistency(path: str, summary: str, detail: str) -> Finding:
    return Finding(
        category=ChallengeCategory.CONSISTENCY,
        verdict=Verdict.SUPPORTED_WITH_RISK,
        failure_mode=FailureMode.IGNORED_REPO_PATTERN,  # SPEC §3
        severity=Severity.INFO,
        subject=path,
        summary=summary,
        detail=detail,
        evidence=[EvidenceRef(source="filesystem", path=path)],
        level=EscalationLevel.DETERMINISTIC,
    )


# -- new dependencies (SPEC §16 DEPENDENCY · SPEC §3 "unnecessary dependencies") ---
#
# The Evidence Engine already catches `pip install X` (`evidence/resolvers._dependency`).
# It cannot see the other half of the same act: editing pyproject.toml or package.json,
# which is how a dependency actually becomes permanent. This closes that.
#
# Recorded, never raised, for a reason specific to this phase: the detector is new and
# unmeasured, and adding a fresh interruption during a census would contaminate the thing
# being counted. If the census says this fires often and accurately, Phase 8 can promote
# it.

# Packages that do the same job. Adding one when the repository already declares another
# is the strongest deterministic evidence available for the word "unnecessary" in SPEC
# §3 — everything else this detector sees is merely "new".
#
# A heuristic list, and short on purpose. Each row is a set of genuine substitutes, not
# merely a shared topic: `requests` and `httpx` really are alternatives, while `pydantic`
# and `sqlalchemy` both touch data and are not.
_INTERCHANGEABLE: tuple[frozenset[str], ...] = (
    frozenset({"requests", "httpx", "aiohttp", "urllib3", "treq", "niquests"}),
    frozenset({"orjson", "ujson", "simplejson", "python-rapidjson", "hyperjson"}),
    frozenset({"python-dateutil", "arrow", "pendulum", "delorean"}),
    frozenset({"python-dotenv", "environs", "dynaconf", "python-decouple"}),
    frozenset({"pydantic", "marshmallow", "cerberus", "voluptuous", "schematics"}),
    frozenset({"typer", "click", "fire", "docopt", "argh"}),
    frozenset({"pytest", "nose", "nose2", "unittest2"}),
    frozenset({"flask", "fastapi", "django", "bottle", "sanic", "starlette", "quart"}),
    frozenset({"sqlalchemy", "peewee", "tortoise-orm", "pony"}),
    frozenset({"loguru", "structlog", "logbook"}),
    frozenset({"axios", "node-fetch", "got", "superagent", "ky", "undici"}),
    frozenset({"jest", "vitest", "mocha", "jasmine", "ava", "tape"}),
    frozenset({"lodash", "underscore", "ramda"}),
    frozenset({"moment", "dayjs", "date-fns", "luxon"}),
    frozenset({"redux", "zustand", "jotai", "mobx", "recoil", "valtio"}),
    frozenset({"styled-components", "emotion", "@emotion/styled", "stitches"}),
)


def _substitute_for(package: str, declared: set[str]) -> str:
    """An already-declared package that does the same job, if there is one."""
    lowered = package.lower()
    for group in _INTERCHANGEABLE:
        if lowered not in group:
            continue
        for existing in sorted(declared):
            if existing.lower() != lowered and existing.lower() in group:
                return existing
    return ""


def dependency_added(event: AgentEvent, index: RepoIndex, path: str) -> list[Finding]:
    """Packages this edit would add to a manifest.

    Reads the manifest as it would be *after* the edit and diffs it against the current
    one, so what gets reported is the change rather than the file's whole contents.
    """
    if event.tool not in ("Write", "Edit", "MultiEdit"):
        return []
    filename = path.rsplit("/", 1)[-1]
    if not manifests.is_manifest(filename):
        return []

    from agentguard.evidence import extractors

    outcome = extractors.resolve_edit(event, index.root)
    if outcome is None or outcome.after is None:
        return []

    after = manifests.parse_text(filename, outcome.after)
    before = manifests.parse_text(filename, outcome.before or "")
    if after is None:
        return []  # the result does not parse as a manifest; say nothing about it
    if before is None and (outcome.before or "").strip():
        return []  # no clean baseline, so "added" cannot be distinguished from "existing"

    existing = set(before.all_names) if before else set()
    declared = existing | set(index.dependencies.all_names)
    added = sorted(set(after.all_names) - existing)
    if not added:
        return []

    findings: list[Finding] = []
    for package in added[:5]:
        substitute = _substitute_for(package, declared)
        findings.append(
            Finding(
                category=ChallengeCategory.DEPENDENCY,
                verdict=Verdict.INSUFFICIENT_EVIDENCE,
                failure_mode=FailureMode.UNNECESSARY_DEPENDENCY,  # SPEC §3
                # LOW when the repository already has something that does the job — that
                # is real evidence for "unnecessary". INFO otherwise, where all we can
                # honestly say is "new". Both are below the challenge threshold.
                severity=Severity.LOW if substitute else Severity.INFO,
                subject=package,
                summary=(
                    f"`{package}` added to {filename}, but `{substitute}` is already a "
                    f"dependency and does the same job"
                    if substitute
                    else f"`{package}` added to {filename} as a new dependency"
                ),
                detail=(
                    "New dependencies are permanent and are rarely removed. "
                    "If both are needed, the reason is worth stating."
                    if substitute
                    else "New dependencies are permanent; the reason should be explicit."
                ),
                evidence=[EvidenceRef(source="manifest", path=path, note=f"adds {package}")],
                level=EscalationLevel.DETERMINISTIC,
            )
        )
    return findings

"""The failure-mode census (Phase 7, SPEC §3 · §37).

Reads what AgentGuard has already recorded and ranks SPEC §3's seventeen failure modes by
how often they were actually observed. No new machinery: every finding needed for this has
been written to SQLite since Phase 0. What was missing was the taxonomy on each finding
and the discipline about what may be reported.

Three properties the renderer is responsible for, all of them about not overstating.

**Modes with no detector are a separate section, never a zero row.** Sorting seventeen
modes by count would put six uninstrumented ones at the bottom next to genuinely rare
ones, and the reader would have no way to tell "never happened" from "never looked".

**Rates carry their denominator.** "Observed in 9 of 41 tasks" survives being quoted;
"22%" does not.

**Guarded and unguarded observations are counted separately.** A session in which
AgentGuard was challenging and injecting planning budgets describes a steered agent. Its
failure rate is a different quantity from the unguarded baseline the census exists to
establish, and averaging the two produces a number that is neither.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agentguard.census.taxonomy import NOT_INSTRUMENTED_BECAUSE, TAXONOMY, ModeSpec
from agentguard.core.store import ProjectStore


@dataclass(slots=True)
class ModeObservation:
    spec: ModeSpec
    occurrences: int = 0
    tasks: int = 0
    distinct_subjects: int = 0
    last_seen: float = 0.0
    examples: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class Census:
    root: str
    days: int
    activity: dict[str, Any]
    observed: list[ModeObservation]  # instrumented and seen, most frequent first
    silent: list[ModeSpec]  # instrumented and never seen
    uninstrumented: list[ModeSpec]  # no detector — reported as such, never as zero
    unknown: list[str] = field(default_factory=list)  # recorded but not in the taxonomy

    @property
    def has_data(self) -> bool:
        return bool(self.activity.get("tasks") or self.activity.get("decisions"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "window_days": self.days,
            "generated_at": time.time(),
            "activity": self.activity,
            "observed": [
                {
                    "mode": str(o.spec.mode),
                    "spec_text": o.spec.spec_text,
                    "detector": o.spec.detector,
                    "proves": o.spec.proves,
                    "occurrences": o.occurrences,
                    "tasks": o.tasks,
                    "distinct_subjects": o.distinct_subjects,
                    "last_seen": o.last_seen,
                    "examples": o.examples,
                }
                for o in self.observed
            ],
            "instrumented_never_observed": [
                {"mode": str(s.mode), "spec_text": s.spec_text, "detector": s.detector}
                for s in self.silent
            ],
            "not_instrumented": [
                {
                    "mode": str(s.mode),
                    "spec_text": s.spec_text,
                    "because": NOT_INSTRUMENTED_BECAUSE.get(s.mode, ""),
                }
                for s in self.uninstrumented
            ],
            "unrecognised_modes": self.unknown,
        }


def collect(store: ProjectStore, days: int = 14, examples: int = 2) -> Census:
    """Build the census from one project's recorded findings."""
    since = time.time() - days * 86400
    counts = {row["failure_mode"]: row for row in store.failure_mode_counts(since)}

    observed: list[ModeObservation] = []
    silent: list[ModeSpec] = []
    uninstrumented: list[ModeSpec] = []

    for spec in TAXONOMY:
        if not spec.instrumented:
            uninstrumented.append(spec)
            continue
        row = counts.get(str(spec.mode))
        if row is None:
            silent.append(spec)
            continue
        observed.append(
            ModeObservation(
                spec=spec,
                occurrences=int(row["occurrences"]),
                tasks=int(row["tasks"]),
                distinct_subjects=int(row["distinct_subjects"]),
                last_seen=float(row["last_seen"] or 0.0),
                examples=(
                    store.failure_mode_examples(str(spec.mode), since, examples)
                    if examples
                    else []
                ),
            )
        )

    # Ranked by breadth first: a mode that touched nine separate tasks matters more than
    # one that fired thirty times inside a single stubborn afternoon.
    observed.sort(key=lambda o: (-o.tasks, -o.occurrences, str(o.spec.mode)))

    # Recorded modes with no taxonomy entry — a row written by a newer version, or a
    # detector added without one. Reported rather than dropped: quietly discarding data
    # you do not recognise is how a census starts lying.
    known = {str(spec.mode) for spec in TAXONOMY}
    unknown = sorted(name for name in counts if name not in known)

    return Census(
        root=store.identity.root,
        days=days,
        activity=store.activity(since),
        observed=observed,
        silent=silent,
        uninstrumented=uninstrumented,
        unknown=unknown,
    )


def render(census: Census) -> str:
    """The published table (Phase 7's exit criterion)."""
    activity = census.activity
    lines: list[str] = []
    add = lines.append

    add("AgentGuard — failure-mode census (SPEC §3)")
    add(census.root)
    add(f"last {census.days} days")
    add("")

    sessions = int(activity.get("sessions") or 0)
    observe_sessions = int(activity.get("observe_only_sessions") or 0)
    tasks = int(activity.get("tasks") or 0)
    add(
        f"  {sessions} session(s) · {tasks} task(s) · "
        f"{int(activity.get('decisions') or 0)} decision(s)"
    )
    add(f"  {observe_sessions} of {sessions} session(s) ran observe-only")
    if sessions and observe_sessions != sessions:
        add(
            "  ! mixed modes. Findings from guarded sessions describe an agent that was "
            "being\n    steered, which is a different quantity from the unguarded baseline."
        )
    if census.observed:
        add(
            f"  AgentGuard would have spoken {int(activity.get('would_have_spoken') or 0)} "
            "time(s) in observe-only sessions"
        )
    add("")

    if not census.has_data:
        add("  no recorded activity in this window — nothing to count")
        add("")

    add("observed")
    if census.observed:
        # Wide enough for the longest instrumented mode, so no row is ever truncated to
        # something that reads as a different failure than the one being reported.
        width = max(len(o.spec.spec_text) for o in census.observed) + 2
        add(f"  {'#':<3}{'§3 failure mode':<{width}}{'tasks':>7}{'occurrences':>13}{'  rate':>8}")
        for rank, o in enumerate(census.observed, start=1):
            rate = f"{o.tasks / tasks * 100:.1f}%" if tasks else "—"
            add(
                f"  {rank:<3}{o.spec.spec_text:<{width}}{o.tasks:>7}{o.occurrences:>13}{rate:>8}"
            )
        add("")
        for o in census.observed:
            for example in o.examples[:1]:
                add(f"  {o.spec.spec_text}: {str(example.get('summary'))[:88]}")
    else:
        add("  nothing — no instrumented failure mode was observed in this window")
    add("")

    # "Never observed" claims we looked. With nothing recorded we did not, and saying
    # otherwise is the same overstatement as printing a zero for an uninstrumented mode.
    add(
        "instrumented, never observed"
        if census.has_data
        else "instrumented — but there was nothing to observe"
    )
    if census.silent:
        for spec in census.silent:
            add(f"  · {spec.spec_text}")
    else:
        add("  —")
    add("")

    if census.unknown:
        add("recorded but not in the SPEC §3 taxonomy")
        for name in census.unknown:
            add(f"  ? {name}")
        add("")

    add("not instrumented — no detector, so no number")
    for spec in census.uninstrumented:
        add(f"  · {spec.spec_text}")
        because = NOT_INSTRUMENTED_BECAUSE.get(spec.mode, "")
        if because:
            add(f"      {because}")
    add("")
    add(
        "These modes are absent from the table above rather than reported as zero. A zero\n"
        "would say 'this does not happen'; the truth is 'nothing here looks for it'."
    )
    return "\n".join(lines)


def render_detectors() -> str:
    """What each detector actually proves — the `--verbose` half of the report.

    Separated because it is the part that stops a number being over-read. "insufficient
    tests: 40%" means something quite specific and considerably narrower than the SPEC
    bullet it is filed under.
    """
    lines = ["what each observation actually establishes", ""]
    for spec in TAXONOMY:
        if not spec.instrumented:
            continue
        lines.append(f"  {spec.spec_text}")
        lines.append(f"      detector: {spec.detector}")
        lines.append(f"      proves:   {spec.proves}")
        lines.append("")
    return "\n".join(lines)

# The failure-mode census — which of SPEC §3's failures actually happen

**Status: the instrument is built and verified. The count is not in yet, and this document
does not pretend otherwise.** Producing it needs a week of real work with observe-only
switched on. See [Running it](#running-it).

---

## Why this exists

The mutation benchmark (`BENCH-mutation.md`) measured the evidence engine at **90% recall
/ 100% precision** on hallucinated references. The control arm then measured how often an
*unguarded* agent hallucinates: across two repositories, two models and twenty sessions,
**zero times** — even when told outright that a method existed, on a monorepo too large to
read exhaustively.

An excellent detector for a problem current models no longer have. Not a failure of
engineering; a failure of *targeting*. SPEC §3 lists seventeen failure modes and the build
centred on the one that capability improvements have largely closed.

So the correction is not to guess a better target. It is to stop guessing.

> Run against real work, interfere with nothing, and count which of the seventeen actually
> occur.

Every piece of machinery this needs already existed — decisions and findings have been
written to SQLite since Phase 0. What was missing was three things: a mode that observes
without steering, a taxonomy on each finding so occurrences can be attributed, and a
discipline about what the resulting table is allowed to claim.

## Seventeen, not fourteen

`IMPLEMENTATION_PLAN.md` said fourteen. So did the Phase 7 brief. SPEC §3 lists
**seventeen** bullets.

A small thing, except that the entire deliverable is a count. A census built on a
miscounted taxonomy would have quietly dropped three failure modes and nothing would have
noticed. `tests/spec/test_s03_failure_census.py` now parses the bullets out of the SPEC
document itself and asserts the taxonomy matches them verbatim and in order, so the code
cannot drift from the document again.

## Method

**Observe-only mode.** `agentguard observe on` makes AgentGuard a sensor. Every engine
still runs and every finding is still recorded; nothing reaches the agent — no challenges,
no completion gate, and *no injected planning budget*.

That last one is not an oversight. Injected context steers the agent, and a census of a
steered agent measures the steering. The point is a baseline.

**The price, stated plainly: while observe-only is on, AgentGuard guards nothing.** That is
the cost of an uncontaminated count, and it is why this is a deliberate mode rather than a
default.

Silence is enforced at one place — `core/observe.py` — which rebuilds each decision as a
fresh ALLOW rather than blanking the fields it happens to know about. A new channel added
to `Decision` later is therefore silent until someone deliberately lets it through, which
is the safe direction for a mode whose whole promise is silence.

**Counting.** Every `Finding` now carries a `failure_mode` naming which SPEC §3 bullet it
evidences. It is a required field with no default, so a detector that cannot say what it
detects will not construct. The census is then a `GROUP BY` over recorded findings, ranked
by how many distinct *tasks* each mode touched — breadth before frequency, so one stubborn
afternoon of six retries does not outrank a problem that appeared in nine separate pieces
of work.

## The rule that matters most

> **A mode with no detector is reported as "not instrumented", never as zero.**

Six of the seventeen have no deterministic signal. Sorting all seventeen by count would
put those six at the bottom next to genuinely rare ones, and a reader would have no way to
tell "never happened" from "never looked".

This is the Evidence Engine's own rule — *complete evidence or silence* — applied to the
census's own claims. Reporting an unmeasured zero would repeat the original mistake in a
new form: a confident number with nothing behind it.

## What is instrumented, and what each detector actually proves

The `proves` column is narrower than the SPEC bullet above it, deliberately. "Untested
code was changed" is not "the tests written were inadequate", and a table should not let a
reader slide from one to the other. `agentguard census -v` prints this alongside the
numbers for the same reason.

| SPEC §3 failure mode | Detector | What an observation proves |
|---|---|---|
| hallucinate files | evidence engine — internal module import resolving to no file | a module under one of this repository's own top-level packages does not exist |
| hallucinate APIs | evidence engine — attribute on a fully-resolved type | the receiver's type is known, its file parsed cleanly, every base resolved, and the member is absent |
| invent functions | evidence engine — `from <our module> import <name>` | the named module neither defines nor re-exports that name |
| invent libraries | evidence engine — undeclared third-party import | not stdlib, not in any manifest, not imported elsewhere. **Cannot** separate an invented library from a real-but-undeclared one — hence LOW, never challenged |
| over-plan trivial tasks | action validator — DIRECT-band task that changed 8+ files | over-*execution*, the visible end of over-planning. Plan length and tool counts are measured directly in Phase 9 |
| modify unrelated files | action validator — scope creep | 5+ files changed outside the task's targets and outside their import graph and tests |
| introduce unnecessary dependencies | evidence engine (install commands) + action validator (manifest diff) | a package became a dependency that was not one. Only where an *interchangeable* package is already declared does this reach "unnecessary"; otherwise it establishes "new" |
| ignore existing repository patterns | action validator — naming and placement conventions on file creation | 5+ sibling files agree on a convention and the new file does not follow it |
| incorrectly claim that a task is complete | completion gate — finishing with failing tests or unparsable files | the agent tried to end the turn while evidence it produced itself said the work was broken |
| write insufficient tests | completion gate — changed code with no coverage and no test written | untested code was changed **in a project that does test its code**. Says nothing about the quality of tests that were written |
| fail to verify their own changes | completion gate — code changed, covering tests exist, none run | tests covering the change existed and were not executed before the agent tried to finish |

### Not instrumented, and why

| SPEC §3 failure mode | Why there is no detector |
|---|---|
| make unsupported assumptions | every instance specific enough to detect is already counted under a narrower mode above; a separate detector would double-count |
| misunderstand developer intent | requires knowing what the developer meant — a semantic judgement, and AgentGuard owns no LLM to make one (SPEC §6) |
| under-plan complex tasks | "not enough planning" has no deterministic signature. Phase 9 measures planning depth against complexity directly |
| introduce unnecessary abstractions | separating a justified abstraction from an unnecessary one is exactly the judgement SPEC §17 says to hand back to the host |
| overlook regressions | a candidate signal exists — a suite that passed earlier in the task and fails later — but test output carries counts, not test names, so a differing subset is indistinguishable from a regression. Not built rather than built wrong |
| confidently continue after making an incorrect assumption | requires the agent to have been challenged and proceeded anyway. Observe-only never challenges, so this is unobservable **by construction** |

That last row is worth sitting with: the mode is unobservable *precisely because* of the
choice that makes the rest of the census trustworthy. Measuring it needs a guarded arm,
which is a different experiment.

## The four new detectors

Three of SPEC §3's failures had no detector before this phase, and one had a mechanism but
no record.

**Unnecessary dependencies.** The Evidence Engine already caught `pip install X`. It could
not see the other half of the same act — editing `pyproject.toml` or `package.json` —
which is how a dependency actually becomes permanent. `manifests.parse_text()` now reads a
manifest's *content*, so the same parser answers both "what does this repo declare?" and
"what would it declare if this edit landed?", and the detector reports the diff. Where the
added package is interchangeable with one already declared (`flask` alongside `fastapi`),
the evidence reaches the word *unnecessary*; otherwise it only reaches *new*, and the
report says so.

**Ignored repository patterns.** Filename casing, word separators and test placement,
computed from the file map. A convention must be *proved* before it can be broken: five
agreeing siblings of the same extension, and **unanimity rather than a majority** — one
dissenter means the repository tolerates both, and a house style that is merely popular is
not one an agent can be said to have ignored.

The first attempt classified names four ways (snake / kebab / camel / pascal) and was
silent on the clearest violation there is: `NewThing.py` among `store.py` and `engine.py`.
`store` is compatible with snake *and* kebab, so it counted as uncommitted — and a
directory of single-word lowercase modules is the commonest shape in Python. Asking the two
questions separately (is it lowercase? which separator?) fixed it: every filename answers
the first, and only multi-word names need answer the second.

**Insufficient tests.** Changed code with no coverage, in a project that has tests, where
the agent wrote none. All three conditions are required — without the middle one the
detector reports on the repository rather than on the change.

**Unverified completion.** The Completion Gate already decided this; it produced a reason
string and nothing countable. It now emits findings, and it emits them **before** rationing
is applied. Loop safety and the per-task cap govern whether the gate *speaks*; they must
not govern what the census *sees*, or a long task would stop being observed part-way
through.

All four are INFO or LOW severity, which puts them under the challenge threshold:
**recorded, never raised**. That is not general caution — a detector that started objecting
mid-census would change the behaviour the census exists to count.

## Precision

The Phase 3 audit is the lesson this phase was built against: a hand-written
false-positive corpus passed 26/26 while real code was producing a 2.2% false-challenge
rate. A synthetic corpus only tests its author's imagination.

So the new convention detector is checked against real code — every file in this
repository, replayed as though the agent were creating it now. Each already follows
whatever conventions surround it by construction, so every finding would be a false
positive.

    116 files replayed as fresh creations -> 0 naming findings

## Running it

```bash
agentguard observe on          # sensor mode: records everything, says nothing
agentguard daemon stop && agentguard daemon start   # the daemon reads config at startup

# ... a week of ordinary work ...

agentguard census              # the ranked table
agentguard census -v           # plus what each detector actually proves
agentguard census --json       # machine-readable
agentguard observe off         # resume guarding
```

`AGENTGUARD_OBSERVE=1` does the same for a single session without touching config.
`agentguard doctor` reports observe-only prominently — a developer who has forgotten it is
on believes they are guarded and is not, which is the same hazard plan D9 exists to
prevent, arrived at from the other direction.

### It survives reboots

Nothing needs re-running across a week that includes shutdowns. The hooks live in
`~/.claude/settings.json`, the mode in `~/.agentguard/config.toml` and the data in
`~/.agentguard/agentguard.db` — all files. Only the daemon is a process, and it does not
survive a restart; the `SessionStart` hook is a `command` hook precisely so that the next
Claude Code session starts it again, reading the mode from config as it does.

That guarantee had a hole, found by exactly this question. Liveness was "does some process
hold this PID", `daemon.json` outlives a reboot, and operating systems recycle PIDs — so a
stale handshake naming a live unrelated process made the shim skip revival, and every hook
that session failed open in silence. It now checks that the endpoint answers `/health` and
reports the pid it should. `curl -s 127.0.0.1:8787/health` shows `"observing": true` when
the census is actually collecting.

## Results

**Not yet available, and deliberately not estimated.**

The instrument is verified end to end: 69 tests, including the real Guard, the real daemon
over the real installed `settings.json`, and a latency check (observe-only 0.44 ms p95
against guarding's 0.45 ms — the mode costs nothing).

What does not exist is data. AgentGuard's hooks are not currently installed on this
machine, and the Phase 6 and benchmark runs each used an isolated `AGENTGUARD_HOME` that
no longer survives. The recorded database holds 2 sessions and 3 decisions — nothing to
count.

Filling in a table from the benchmark corpus instead would be worse than leaving it empty:
those tasks were *written to bait specific failures*, so their frequencies measure the task
author, not the agent. That is the same error as reporting an uninstrumented mode as zero,
and the whole point of this phase is not to make it twice.

### Worked example — the instrument's output, not the census

Generated from a seeded three-task session, to show the shape of the report. **These
numbers are from a fixture and mean nothing about real agents.**

```
AgentGuard — failure-mode census (SPEC §3)
last 1 days

  1 session(s) · 3 task(s) · 13 decision(s)
  1 of 1 session(s) ran observe-only
  AgentGuard would have spoken 2 time(s) in observe-only sessions

observed
  #  §3 failure mode                       tasks  occurrences    rate
  1  write insufficient tests                  2            2   66.7%
  2  hallucinate APIs                          1            1   33.3%
  3  ignore existing repository patterns       1            1   33.3%
  4  introduce unnecessary dependencies        1            1   33.3%
  5  fail to verify their own changes          1            1   33.3%

instrumented, never observed
  · hallucinate files · invent functions · invent libraries
  · over-plan trivial tasks · modify unrelated files
  · incorrectly claim that a task is complete

not instrumented — no detector, so no number
  · make unsupported assumptions · misunderstand developer intent
  · under-plan complex tasks · introduce unnecessary abstractions
  · overlook regressions · confidently continue after an incorrect assumption
```

## Known limits

- **One project's census is not the population's.** Rates are per-repository and depend on
  its test culture, its conventions and its size. `write insufficient tests` will read very
  differently in a well-covered library than in a prototype.
- **Observe-only cannot see §3.17 at all**, by construction (above).
- **Two modes are proxies.** `over-plan trivial tasks` observes over-*execution*, and
  `invent libraries` cannot distinguish an invented package from a real undeclared one.
  Both are marked as such in `census -v`; neither should be quoted without its caveat.
- **The census counts what AgentGuard can see.** A failure with no deterministic signature
  is invisible to it, and its absence from the table is not evidence of its absence from
  the work. This document lists the six known blind spots; there may be others nobody has
  named.

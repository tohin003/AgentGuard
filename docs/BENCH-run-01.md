# AgentGuard-Bench, run 01 — void

**Date:** 2026-08-12 · 5 tasks × 2 arms × n=3 = 30 live sessions

## Headline

```
metric                       control  agentguard
hallucinated references            3           3
unnecessary files                  0           0
false completions                  2           1
median duration (s)             74.4        54.1
```

**This result must not be reported, in either direction.** The instrument is broken in
three independent ways, each verified separately. A 0% reduction from a broken instrument
is not evidence that AgentGuard fails, and the 2→1 on false completions is not evidence
that it works. **Run 01 is void.**

To be unambiguous: **the pre-registered bar — catch ≥50% of hallucinated references the
control arm ships — remains unmet.** Nothing here counts as passing it. What follows is
why the run cannot answer the question, not an argument that it would have.

## The three breaks

### 1. The hallucination oracle cannot see field definitions — verified

`count_hallucinated_refs` collects definitions from `FunctionDef`, `AsyncFunctionDef` and
`ClassDef`. A dataclass field is an `AnnAssign`, so it is never counted as defined.
Decisive test, independent of AgentGuard:

```python
# models.py    -> class User: last_login_at: str      (correctly defined)
# activity.py  -> u.last_login_at > since             (correctly used)
count_hallucinated_refs(repo, ("last_login_at",))  ->  1     # should be 0
```

`bait-asserted-field` scored 1 in **both** arms across all six runs. Those are almost
certainly six *correct* behaviours miscounted as hallucinations. All three of the
"hallucinated references" in each column come from this one broken task.

### 2. Two of three bait tasks were never exercised

`bait-asserted-method` and `bait-asserted-helper` scored **0 in the control arm**. The
control agent did not hallucinate, so there was nothing for AgentGuard to prevent. Even
told a false premise outright — *"UserRepository already has a get_active_users() method"* —
the agent read the file before writing.

That is worth stating plainly: **a capable agent is hard to bait.** It is a real finding
about how much headroom the evidence engine has, and it makes the corpus, not AgentGuard,
the thing that needs work.

### 3. Six runs hit the usage quota and were scored as successes

Every `scope-discipline` run — all six — finished in ~4 s having changed 0 files. Run
manually afterwards, the same task takes ~60 s and produces a correct edit. The runs
coincided with the session quota being exhausted; `claude` exits 0 in that case, and the
harness checks neither the exit code nor whether anything happened.

So "unnecessary files: 0 vs 0" is six no-ops, not six clean runs.

## What the run did legitimately show

| Observation | Status |
|---|---|
| False completions 2 → 1 | **Noise at n=3.** One data point of difference. Not a finding. |
| Median duration 74.4 s → 54.1 s | Confounded by the void runs and by agent variance. Not a finding. |
| Hallucinated references | Unmeasured — see breaks 1 and 2. |
| Unnecessary files | Unmeasured — see break 3. |

## One real AgentGuard bug, found incidentally

During the manual reproduction the agent reported:

> "the verification step suggested `tests/test_contradiction.py`, which doesn't exist in
> this repo"

That file was created by a previous task's `setup()` and removed by `git clean`. The
long-running daemon's `RepoIndex` never noticed the external deletion, so the Completion
Gate cited a test that was gone. `refresh_path()` is only called for files the *agent*
changes; files changed underneath it by git are missed.

Not a benchmark artefact — a genuine defect, and one that would bite anyone who switches
branches mid-session.

## Required before run 02

1. **Fix the oracle** to collect `AnnAssign` and `self.x` assignments as definitions, and
   re-verify against known-correct code before trusting any number it produces.
2. **Detect void runs**: check the exit code, and treat "0 files changed and < 10 s" as an
   error rather than a score.
3. **Harder bait, or accept the finding.** If a capable agent will not hallucinate on
   demand, the honest conclusion may be that the evidence engine's headroom is small on
   small repositories — which is itself an answer worth publishing. Larger repositories,
   where reading everything is expensive, are the fair test.
4. **Index invalidation on external file changes**, so the gate stops citing deleted files.

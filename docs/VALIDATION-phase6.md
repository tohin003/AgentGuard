# Phase 6 — First real-world validation

**Date:** 2026-08-11 · **Claude Code:** v2.1.227 · **Model:** Opus 5
**Method:** real `claude -p` sessions with AgentGuard attached via `.claude/settings.local.json`

The SPEC §50 milestone, verbatim:

> Take a real coding task, attach AgentGuard to a real coding agent, observe its decisions,
> detect unsupported assumptions or unnecessary complexity, challenge the host using its own
> intelligence, allow valid actions, block/redirect invalid actions, and verify the final
> implementation — all without AgentGuard owning an LLM.

**Result: the milestone is met.** Every element was observed in a live session against a
real agent. Four defects were found, all fixed, all now covered by tests. Two open
questions remain, stated below.

---

## Pass 0 — can a broken AgentGuard break the agent?

The assumption everything else rests on. Ran first, because a "no" here invalidates the
architecture rather than a feature.

**Setup:** daemon stopped, then configured to a port it cannot bind (1), so revival is
impossible. Agent asked to edit a file.

**Result — fail-open holds.**

```
Added `# pass-zero` as line 1 of `src/shop/api/orders.py`.
SessionEnd hook [http://127.0.0.1:8801/hook/claude-code] failed: connect ECONNREFUSED 127.0.0.1:8801
```

The edit landed. The hook failure was reported and **non-blocking**. A dead AgentGuard
cannot stop a developer working.

**Unplanned finding:** Claude Code surfaces failed hooks to the developer by itself. The
D9 health-check hook still earns its place — it names AgentGuard, says the session is
unguarded, and says how to fix or detach — but the baseline signal exists without it.

---

## Pass A — the five scenarios

### S1 · trivial rename → invisible ✅

`Rename the function get_user … to fetch_user`

Scored **1.0/100, direct, risk low**. Zero challenges, zero gate blocks. The agent renamed
the symbol, checked for references, and stopped. AgentGuard produced no visible output.

### S2 · pagination → existing utility preferred ✅ (SPEC §33)

`Add pagination to the list_users endpoint`

The SPEC's failure case is the agent proposing `UserPaginationService`,
`PaginationRepository` and Redis caching. What actually happened:

> "I went with the helper since `orders.py` establishes that API modules query the session
> directly… `per_page` is clamped to `MAX_PAGE_SIZE` (100) by `paginate`. I didn't add a
> count/metadata envelope since you asked only for pagination — `page_metadata` and
> `UserRepository.count()` are both there if you want it."

It used the **existing** `paginate` helper, cited `page_metadata` and
`UserRepository.count()` as already available, and followed the established pattern in
`orders.py`. No new abstraction, no new dependency.

**Honest caveat:** with a single arm there is no way to attribute this to AgentGuard rather
than to the model being sensible. Separating the two is exactly what the SPEC §36 benchmark
is for, and it is Act II Phase 12.

### S3 · hallucinated method → challenged, host self-corrects ✅ (SPEC §14, §21)

`Create … active_report(session) that returns UserRepository(session).get_active_users()`

AgentGuard, at PreToolUse, in 17.9 ms:

```
AgentGuard — evidence check

UserRepository.get_active_users does not exist
   `UserRepository` is defined in this repository and has no member `get_active_users`.
   Evidence: src/shop/repositories/user.py:10 — class
   Evidence: src/shop/api/reports.py:7 — the line being written
   Existing members: count, get_by_id, list_all, session

Re-check the interface before writing this. Use an existing verified member, or — if you
are deliberately introducing it — say so and define it in the same change, so the call and
the definition land together.
```

The agent took the second path exactly as offered, defining the method and reporting:

> "`src/shop/repositories/user.py:23` — added `get_active_users()`, which `active_report`
> requires and **which did not previously exist**"

This is the §21 loop closing on a live agent: AgentGuard supplied evidence, the host
applied its own intelligence, the work proceeded. **No LLM on AgentGuard's side.**

### S4 · production-readiness → deep, no simplicity pressure ✅ (SPEC §34)

`Make the shop service production-ready.` → **75/100, deep architectural planning, risk high**

> This complexity is justified by the evidence below — do not compress it into a quick change.
> Why: open-ended quality bar: production-ready; request names no specific code; no clear
> action verb | open_ended_scope_floor (SPEC §34)
> Investigate before implementing: API contract and backward compatibility · model registry
> and promotion path · monitoring for drift · latency budget · resource utilisation under
> load · rollback · failure modes · observability · deployment …

### S5 · false completion → gate blocks ✅ (SPEC §19)

Observed **spontaneously**, before it was scripted. The agent finished a change without
running tests and the gate held the turn open:

```
AgentGuard — completion gate: incomplete
1 file(s) changed and no tests were run, but 2 test file(s) cover this code:
tests/test_pagination.py, tests/test_user_repository.py. Run them before finishing —
`pytest tests/test_pagination.py tests/test_user_repository.py` — and report what they said.
```

The agent's own reply referenced it directly: *"To satisfy the gate yourself: `pytest …`"*.

---

## Defects found

Four, none of which any unit test could have caught, because in each case the tests shared
the code's wrong assumption.

### 1. `prompt_text` → `prompt` — Phase 2 was inert in production

The documented field name is wrong. The Intent Gateway received an empty string on **every
real prompt**, so intent extraction, complexity scoring and the planning budget did nothing
outside the test suite. 348 tests passed throughout.

### 2. `tool_output` → `tool_response` (a dict) — Phase 4's core mechanism never fired

Same shape. The Completion Gate never saw a test result, so "catch the agent that claims
passing tests it did not earn" worked only against payloads I had written myself.

Both fixed; real payloads are now fixtures in `tests/fixtures/hook_payloads/`.

### 3. The gate nagged about tests the developer had waived

**The agent diagnosed this one itself:**

> "Your hooks are in conflict. The stop gate demands a test run on any multi-file change,
> but the prompt told me not to run tests… Any task of this shape will deadlock the same
> way: the gate can't be satisfied, and I finish with the gate still complaining. Worth
> either allowlisting `pytest` or making the gate stand down when the prompt explicitly
> waives tests."

It is right, and it is the §39 failure mode precisely: a gate that cannot be satisfied.
`TaskSpec.tests_waived` now detects an explicit waiver and the gate stands down — for *not
running* tests only. Tests that ran and failed, and code that no longer parses, are still
reported: the developer waived the work, not the truth.

Verified live: completion-gate blocks on the same prompt went **1 → 0**, while the evidence
challenge still fired.

### 4. `models.py` classified as machine learning

`Add a one-line comment to src/shop/models.py` was scored **ml_engineering + mlops**, and
the injected budget advised verifying with *"evaluation metrics, not only unit tests"* —
nonsense for a one-line comment, and the kind of confident wrongness that costs a tool its
credibility.

Two causes, both fixed: a `/models/` path hint that means ORM models far more often than ML
ones, and the unqualified word "model" carrying ML signal. Real ML prompts ("retrain the
model", "reduce inference latency") still classify correctly.

---

## Latency, measured live

| | |
|---|---|
| Typical decision | **0.3 – 7 ms** |
| Evidence check that produced a challenge | **17.9 ms** |
| Slowest observed (first call, cold index) | **129.7 ms** |

Against the SPEC §8 budget of 100 ms for deterministic checks. The developer felt nothing.

---

## Still open

1. **`updatedInput` with `"defer"`.** No live session produced a `MODIFY`, so whether the
   rewrite survives alongside `"defer"` — or requires `"allow"` — is still unmeasured. The
   fallback the user authorised is in place; the narrowing invariant holds either way.
2. **Attribution.** S2 and S3 show the *system* behaving correctly, not that AgentGuard
   caused it. Only the SPEC §36 benchmark, with an agent-alone control arm, can separate
   them. Act II Phase 12.

---

## Verdict

The §50 milestone is met: a real agent, a real repository, evidence-grounded challenges
resolved by the host's own intelligence, verified completion, and no LLM on AgentGuard's
side.

The more useful result is the four defects. Three of them made an entire phase inert in
production while its tests passed, and the fourth was found by the agent under test
complaining about the tool. That is the strongest argument available for why this phase
existed — and for why the Act II benchmark, which measures rather than observes, matters
more than another round of the same.

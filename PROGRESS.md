# AgentGuard — Progress Tracker

Living status. Updated at the end of every phase.
Plan: `IMPLEMENTATION_PLAN.md` · Source of truth: `AgentGuard — Host-Powered AI Agent Reliability & Reasoning Layer.md`

**Current phase:** Phase 6 ✅ — §50 milestone met. **Awaiting your sign-off to start Act II.**
**Act I goal:** SPEC §50 milestone (Phase 6 real-world validation)
**Suite:** 372 tests passing · ruff clean

---

## Status board

| Phase | Name | Status | Exit criteria |
|---|---|---|---|
| 0 | Foundation + hook plumbing spike | ✅ **done** | all met — see below |
| 1 | Repository Intelligence | ✅ **done** | all met — see below |
| 2 | Intent Gateway + Complexity + Planning Governor | ✅ **done** | all met — see below |
| 3 | Evidence Engine + Contradiction Engine | ✅ **done** | all met — see below |
| 3.5 | Storage foundation & data lifecycle | ✅ **done** | all met — see below |
| 4 | Action Validator + Verification + Completion Gate | ✅ **done** | all met — see below |
| 5 | Full Claude Code adapter + install UX | ✅ **done** | all met — see below |
| 6 | 🔬 First real-world validation (§50 milestone) | ✅ **done** | met — `docs/VALIDATION-phase6.md` |
| 7 | Act II — performance & reliability hardening | ⬜ blocked on Phase 6 sign-off | — |
| 8 | Act II — agent interoperability (MCP, Cursor, Codex) | ⬜ blocked | — |
| 9 | Act II — persistent project memory foundation | ⬜ blocked | — |
| 10 | Act II — local semantic memory (sqlite-vec + FTS5) | ⬜ blocked | — |
| 11 | Act II — memory validation & intelligence | ⬜ blocked | — |
| 12 | Act II — AgentGuard-Bench + production release | ⬜ blocked | — |

Legend: ⬜ not started · 🟡 in progress · ✅ done · ⚠️ done with caveats · 🔴 blocked

---

## Phase 0 — exit criteria

| Criterion | Result |
|---|---|
| End-to-end hook round trip → daemon → decision → SQLite | ✅ verified through the **installed** settings.json, not hand-written payloads |
| Hot path < 100 ms p95 (SPEC §8) | ✅ **0.98 ms p95** kept-alive; **9.09 ms p95** with a fresh connection per call |
| Killing the daemon mid-session does not impede the agent | ✅ 12 distinct fail-open paths tested |
| No LLM anywhere in the core (SPEC §6) | ✅ manifest scan + import scan + live socket watch |

## Phase 1 — exit criteria

| Criterion | Result |
|---|---|
| Golden fixture repos assert correct symbol/import/test maps | ✅ 44 tests over a Python and a TypeScript fixture |
| Incremental update after edit / add / delete / rename | ✅ including symbol rename and dependency-graph rebuild |
| Index build time on a large repo | ✅ **5.6 s** for 1,887 source files (42k-file monorepo) — hence async build |
| Warm lookup < 5 ms | ✅ **1.6 µs** per call (~3,000× under) |
| Hot-path latency unchanged | ✅ 0.91 ms p95 end-to-end, same as Phase 0 |

Measured on a real 1,887-file monorepo: full build 5,647 ms · targeted `refresh_path`
**0.18 ms p50** · full `refresh` with no changes 104 ms.

## Phase 2 — exit criteria

| Criterion | Result |
|---|---|
| The SPEC's worked examples land in their stated bands | ✅ see anchor table below |
| Planning budget short for a rename, enumerative for deep work | ✅ 6 lines vs 18 |
| Calibration corpus (~40 prompts) | ✅ 57 conformance tests, incl. a 10-prompt anti-over-trigger corpus |
| No "simple" prompt escapes the DIRECT/LIGHT bands | ✅ enforced for every ordinary prompt |
| Latency inside budget | ✅ **1.47 ms p95** full prompt path (fixture); **3.79 ms p95** on the 1,887-file monorepo |

**SPEC anchors, measured:**

| Prompt | SPEC says | AgentGuard says |
|---|---|---|
| "Rename `get_user()` to `fetch_user()`" (§13) | 2/100, direct, 3-step plan | **1/100 DIRECT**, low risk, 3 steps |
| "Add pagination to /users" (§9) | Low complexity, **Medium** risk, backend | **10/100 DIRECT**, **medium** risk, backend — and it surfaces the existing `pagination.py` |
| "Change the prediction API" (§10) | ml_engineering + [backend, mlops], risk high, depth deep | **exactly that** |
| "Make auth horizontally scalable across services" (§2) | deep | **75/100 DEEP**, high risk, via `cross_boundary_floor` |
| "Introduce distributed session caching" (§13) | 80+; investigate consistency, caching, invalidation, concurrency, rollback | **DEEP**; all five topics present |
| "Make our inference service production-ready" (§34) | deep, and *not* "keep it simple" | **75/100 DEEP**, high risk, no simplicity pressure |

## Phase 3 — exit criteria

| Criterion | Result |
|---|---|
| Every seeded hallucination caught | ✅ method-on-type (3 forms), missing import symbol, undeclared internal module, new dependency |
| **Zero false challenges** on a legitimate-code corpus | ✅ 26 hand-written cases **and** 5,750 claims from three real repositories |
| Challenge rationing (§17, §39) | ✅ once per concern, hard per-task ceiling, severity floor |
| 100 ordinary operations produce nothing | ✅ enforced as a test |
| Latency inside budget | ✅ **0.06 ms p95** on the fixture, **4.2 ms p95** on a 70-file repo |

**The false-positive audit.** The hand-written corpus passed 26/26 while real code was
producing a **2.2–2.4% false-challenge rate** — a synthetic corpus only tests its author's
imagination. Replaying every Python file in three real repositories as "the agent is
creating this file fresh" exposed four distinct bugs; after fixing them the rate is **0 in
5,750 claims**. That replay is now a permanent test against AgentGuard's own source.

| Bug found by the audit | Why it mattered |
|---|---|
| `self.root = ...` in `__init__` was never indexed | every instance attribute (`index.root`, `store.db_path`) looked hallucinated |
| `list[Claim]` was unwrapped to `Claim` | every `claims.append(...)` became a confident claim about `Claim.append` |
| class-body `X = 1` was not a class attribute | every enum-style constant (`VerbClass.RENAME`) looked missing |
| undeclared third-party imports raised at MEDIUM | transitive deps and per-service manifests are indistinguishable from invented libraries — now LOW, logged not challenged |

## Phase 3.5 — exit criteria

Inserted after the **Memory & Database Management Plan** and **ACT II Local Semantic
Memory** plans. Only the parts that are cheap now and expensive to retrofit; the
intelligence built on them is Act II Phases 9–11.

| Criterion | Result |
|---|---|
| One database, projects → sessions (§1) | ✅ `~/.agentguard/agentguard.db`, replacing per-workspace files |
| Project isolation (§4) | ✅ **structural** — the engine holds a `ProjectStore` with no method that can express a cross-project query; 6 tests |
| Retention tiers, configurable (§5) | ✅ events 14 d · decisions 30 d · verifications 60 d · sessions 270 d · violations and memory kept |
| Bounded rows (§6) | ✅ a 500 KB payload stores as <5 KB, with its size still recorded |
| Maintenance off the hot path (§7) | ✅ runs on `SessionEnd`, rate-limited to 6 h, WAL checkpoint + incremental vacuum |
| Disk protection (§8) | ✅ healthy / low / critical; low prunes 4× harder, critical stops writing |
| **Storage is never a dependency** (§8 critical rule) | ✅ with the database closed mid-session the Guard still returns a decision |
| `memories` table with ACT II metadata | ✅ created, unwritten — so Phase 9 promotion needs no migration |

Identity follows the **git remote** when there is one, so a project that is moved or
re-cloned keeps its accumulated memory rather than starting over.

## Phase 4 — exit criteria

| Criterion | Result |
|---|---|
| A lying "all tests pass" turn is caught and blocked | ✅ driven through the real Guard, end to end |
| §18 scope violation challenged | ✅ 17 unrelated files caught; adjacent work not |
| Gate loop safety | ✅ per-task cap plus `stop_hook_active`; the gate yields rather than loops |
| The gate is quiet on ordinary turns | ✅ 6 silence cases: no changes, docs-only, no runner, uncovered code, unreadable output, no task |
| Risky commands surfaced, not overruled | ✅ 7 dangerous / 12 ordinary, incl. `--force` vs `--force-with-lease` |
| Latency unchanged | ✅ 0.98 ms p95 end-to-end |

**How the lie is caught.** AgentGuard does not run the tests. When the agent runs `pytest`
the output arrives in `PostToolUse`, and that output is ground truth. An agent that ran the
suite, saw two failures, and then says "all tests pass" has been contradicted by an
artefact it produced itself — no re-execution, no fixture conflicts, no latency.

**The bug this phase found.** `event.task_id` was resolved *after* the handler ran, so
every handler saw `None`. The challenge ledger treats a missing task as "no way to avoid
repeating myself" and suppresses everything — meaning **every challenge in the real
pipeline was being silently dropped**. Phase 3's tests called `evidence.check()` directly
and passed throughout. Only an end-to-end test through the Guard exposed it.

## Phase 5 — exit criteria

| Criterion | Result |
|---|---|
| All six hooks wired, installed safely | ✅ verified by installing into a real scratch project |
| Detach leaves your own hooks untouched | ✅ idempotent install, exact uninstall, `--dry-run` |
| Kill switch | ✅ `agentguard off` / `on`, plus `AGENTGUARD_DISABLE=1` per session |
| Transparency | ✅ `log`, `why`, `doctor`, `db stats \| projects \| maintain` |
| **MODIFY under a narrowing invariant** (plan D2) | ✅ rewrite-first, re-checked, always announced |
| **A dead AgentGuard says so** (plan D9) | ✅ revive → silent; unrecoverable → visible message, exit 1, blocks nothing |

**Two user decisions implemented.** `MODIFY` may now emit `"allow"`, which skips the
developer's permission prompt for that call — justified only because the rewrite is a
*narrowing*, so the code proves it: a rewrite may never extend reach, its output is
re-checked by the risk checks, and it always announces itself. Anything that cannot be made
safe falls through to `REQUEST_REVIEW` instead. And fail-open is no longer silent: a daemon
that cannot be revived is reported to the developer, who decides whether to continue.

**Two bugs found, one serious.**

| Bug | Consequence |
|---|---|
| `python -m agentguard.daemon run` defaulted to port **0** ("any free port") | The shim spawns it exactly that way. The daemon would bind a random port while the installed hook URL pointed at the configured one — **every hook failing, AgentGuard silently doing nothing in every real install.** Every test passed, because every test passed `--port` explicitly. |
| The handshake was published *before* binding | A daemon that could not bind still advertised itself, and briefly looked alive |

## Phase 6 — first real-world validation ✅

Full write-up: **`docs/VALIDATION-phase6.md`**. Real `claude -p` sessions, real repository.

| Scenario | Result |
|---|---|
| **Pass 0** — dead daemon | ✅ fail-open holds; hook failure reported, **non-blocking**; the edit landed |
| S1 trivial rename | ✅ 1.0/100 direct, completely invisible |
| S2 pagination (§33) | ✅ agent used the **existing** `paginate`, cited `page_metadata` / `count()`, no new abstraction |
| S3 hallucinated method (§14, §21) | ✅ challenged in 17.9 ms with file evidence; agent defined the method and said it "did not previously exist" |
| S4 production-ready (§34) | ✅ 75/100 DEEP, "do not compress it into a quick change" |
| S5 false completion (§19) | ✅ gate blocked — observed **spontaneously**, before it was scripted |

Live latency: typical decision **0.3–7 ms**, challenge **17.9 ms**, cold first call 129.7 ms.

**Four defects found, all fixed.** Three had made an entire phase inert in production while
its tests passed; the fourth was diagnosed by the agent under test.

| Defect | Consequence |
|---|---|
| `prompt_text` → **`prompt`** | Intent Gateway saw an empty string on every real prompt — Phase 2 inert |
| `tool_output` → **`tool_response`** (a dict) | Completion Gate never saw a test result — Phase 4's core mechanism never fired |
| Gate nagged about tests the developer waived | §39 failure mode; **the agent diagnosed it**: "the gate can't be satisfied" |
| `models.py` classified `ml_engineering` | Advised "evaluation metrics" for a one-line comment |

**Still open:** `updatedInput` with `"defer"` (no live MODIFY occurred), and attribution —
S2/S3 show the *system* working, not that AgentGuard caused it. Only the §36 benchmark with
a control arm can separate those. Act II Phase 12.

## Spec conformance suite

Acceptance tests derived from the SPEC's own worked examples. These are the real
definition of "it works".

| Test | SPEC § | Status |
|---|---|---|
| `test_s06_no_llm` (4 tests) | §6, §46.1 | ✅ |
| `test_s08_latency_budget` (6 tests) | §8 | ✅ |
| `test_s12_proportional_planning` (57 tests) | §2, §9, §10, §12, §13, §34 | ✅ |
| `test_s14_evidence` (57 tests) | §14, §15, §16, §17, §39 | ✅ |
| `test_s18_validation_and_completion` (68 tests) | §18, §19, §20 | ✅ |
| `test_s33_end_to_end` | §33 | ⬜ Phase 5 |

---

## What exists today

```
src/agentguard/
├── core/
│   ├── enums.py      SPEC vocabulary — every enum traceable to a section
│   ├── events.py     normalized agent-agnostic AgentEvent (§23)
│   ├── models.py     EvidenceRef, Finding, Decision (§14, §16, §18)
│   ├── config.py     settings, daemon bind, token, kill switch
│   ├── store.py      Database (shared, maintenance, disk) + ProjectStore (scoped)
│   │                 projects/sessions/tasks/events/decisions/findings/challenges/
│   │                 verifications/metrics/memories (§30 · Memory plan)
│   ├── metrics.py    latency percentiles (§8, §37)
│   └── engine.py     Guard — the one place an event becomes a Decision (§28)
├── adapters/claude_code/
│   ├── translate.py  Claude JSON ⇄ AgentEvent ⇄ hook output
│   ├── install.py    safe settings.json merge / uninstall
│   └── shim.py       stdlib-only fallback, --ensure-daemon, --health
├── repo/             ── the deterministic evidence base (§32) ──
│   ├── models.py     FileRecord, SymbolRecord, ImportRecord, GitState, DependencyInfo
│   ├── scanner.py    gitignore-aware discovery (git ls-files fast path + walk fallback)
│   ├── symbols_python.py  stdlib `ast` extraction (exact, no grammar drift)
│   ├── symbols_ts.py      tree-sitter for TS/JS/Go/Rust/Java/Ruby, optional
│   ├── manifests.py  pyproject / requirements / package.json / go.mod / Cargo.toml
│   ├── gitinfo.py    branch, dirty set, recent commits, per-file churn (TTL-cached)
│   └── index.py      RepoIndex — symbol map, import + reverse-import graph, test map
├── intent/           ── Intent Gateway (§9, §10) ──
│   ├── lexicon.py    verbs, domain keywords, risk vocabularies, term matching
│   ├── models.py     TaskSpec, Target, ComplexitySignal, ComplexityAssessment
│   └── extractor.py  prompt → grounded TaskSpec (targets resolved against the index)
├── complexity/       ── Complexity Engine (§12) ──
│   ├── signals.py    the 8 signals, each with its reason and evidence
│   ├── rules.py      override rules — the "decision system, not formula" part
│   └── engine.py     scoring, banding, and risk (assessed separately)
├── planning/
│   └── governor.py   the planning budget the host agent reads (§13, §20)
├── evidence/         ── Evidence Engine (§14, §15) ──
│   ├── models.py     Claim, Resolution
│   ├── pyanalysis.py Python claims + local type inference (the false-positive firewall)
│   ├── extractors.py tool args → post-edit content → *newly introduced* claims
│   ├── resolvers.py  claim → verdict, with the "complete evidence or silence" rule
│   └── engine.py     one call per proposed action
├── challenge/        ── Contradiction Engine (§16, §17) ──
│   ├── renderer.py   the challenge text the host reads
│   └── ledger.py     rationing — once per concern, hard ceiling per task
├── validate/         ── Action Validator (§18) ──
│   ├── checks.py     scope creep, proportionality, risky commands, consistency
│   ├── modify.py     narrowing rewrites, and the invariant that keeps them safe
│   └── validator.py  the cost-ordered pipeline and its decisions
├── verify/           ── Verification + Completion Gate (§19, §20) ──
│   ├── runners.py    runner detection, test-command recognition, output parsing
│   ├── static.py     does the changed code still parse?
│   └── completion_gate.py  PASS / INCOMPLETE / VERIFICATION_FAILED / HUMAN_REVIEW
├── daemon/app.py     FastAPI, 127.0.0.1, token auth, handshake file
└── cli/main.py       install · uninstall · doctor · log · why · index · find-symbol ·
                      explain · daemon
```

Every hook is now live. `user_prompt` runs Intent → Complexity → Planning and injects the
budget; `pre_tool_use` runs the full validation pipeline; `post_tool_use` observes what
happened (files touched, tests run) and keeps the index fresh; `stop` runs the Completion
Gate; `session_end` closes out and runs storage maintenance.

---

## Verified facts (confirmed, not assumed)

- **2026-08-11** — **Hook payloads captured from a live session** and saved as fixtures in
  `tests/fixtures/hook_payloads/`. Two documented field names are wrong in practice, and
  each silently disabled a whole phase:
  - the prompt arrives as **`prompt`**, not `prompt_text` → the Intent Gateway was scoring
    an empty string on every real prompt (Phase 2 inert in production).
  - tool results arrive as **`tool_response`**, a **dict** (`{stdout, stderr, …}`), not
    `tool_output` as a string → the Completion Gate never saw a test result (Phase 4's
    central mechanism never fired).
  Confirmed correct as documented: `cwd`, `session_id`, `tool_name`, `tool_input`,
  `tool_use_id`, `last_assistant_message`, `stop_hook_active`, `source`.
- **2026-08-11** — Claude Code hook contract as *documented* at `code.claude.com/docs/en/hooks`
  (superseded above where the two differ):
  - `PreToolUse` → `permissionDecision: allow|deny|ask|defer`, `permissionDecisionReason`,
    `additionalContext`, **`updatedInput`** (⇒ SPEC §18 `MODIFY` is implementable).
  - `PostToolUse` → `decision:"block"` + `reason`, `additionalContext`, `updatedToolOutput`.
  - `UserPromptSubmit` → `additionalContext` (⇒ Intent/Planning injection, §9/§13); 30s timeout.
  - `Stop` → `decision:"block"` + `reason`; `stop_hook_active` flag (⇒ Completion Gate §19 +
    loop-breaker).
  - `SessionStart` → `additionalContext`, `watchPaths`, `source: startup|resume|clear|compact|fork`.
  - Hook types include **`http`** (no per-call process spawn) and **`async: true`**.
- **2026-08-11** — **Transport measured, not assumed** (`docs/BENCH-latency.md`): `http` hook
  0.81 ms p50; command shim 70.5 ms p50 / 113.6 ms p95. The shim would consume the entire
  §8 budget before doing any work. Hence: `http` for the hot path, command hook only for
  `SessionStart --ensure-daemon`.
- **2026-08-11** — uvicorn 0.52 does **not** unwind the stack on SIGTERM, so a `finally`
  never runs. Fixed with an explicit handler setting `server.should_exit`. Stale handshake
  files are still treated as normal everywhere (SIGKILL exists).

- **2026-08-11** — **Index build measured on a real 42k-file monorepo**: 1,887 source files
  in 5.6 s. Too slow to sit in front of a developer, so the index builds in a background
  thread and every query answers "unknown" until it is ready. Targeted `refresh_path()`
  (0.18 ms) replaced full `refresh()` (104 ms) on the post-edit path.

## Design decisions worth remembering

- **Positive evidence is always safe; negative evidence requires a clean parse.** A file
  that failed to parse and a file that is genuinely empty both yield zero symbols, but only
  the second licenses concluding a symbol is *absent*. `ParsedFile.parsed` carries that
  distinction; conflating them would turn every syntax error into a false hallucination
  challenge. Found by a test during Phase 1, not in production.
- **`knows_type()` before challenging `X.y`.** The Evidence Engine may only object to a
  missing method when it actually knows the type. Otherwise it is reasoning from ignorance.
- **Word-start matching, never substring.** `"all" in "horizontally"` invented breadth
  language in prompts that had none; `"make a" in "make authentication"` classified a
  scaling task as an *add*. Single words anchor at a word start and may run on (so `auth`
  still matches `authentication`); phrases must also close on a boundary.
- **Complexity and risk are separate questions.** SPEC §9's pagination example is
  explicitly low-complexity *and* medium-risk. Collapsing them would lose the case where
  an easy change touches a contract other people depend on.
- **Unknown scores zero.** With no index, blast radius is 0, not a guess. Scoring unknowns
  high would make AgentGuard cautious about everything it does not understand — exactly
  the over-planning SPEC §2 forbids.
- **A synthetic corpus only tests its author's imagination.** The hand-written
  false-positive corpus passed 26/26 while real code showed 2.2% false challenges. Replaying
  real repositories through the engine is now a permanent test.
- **Complete evidence or silence.** A claim is only ever CONTRADICTED when every link holds:
  the receiver's type is known, its file parsed cleanly, and every base class resolved. A
  class inheriting from `pydantic.BaseModel` has methods AgentGuard cannot see, so it says
  nothing about them.
- **Only *newly introduced* claims are checked.** Pre-existing problems in a file are never
  attributed to the edit that touched something else.
- **Let the agent under test tell you what is wrong with the tool.** The most useful defect
  of Phase 6 was diagnosed by the agent complaining, in its own words, that the gate could
  not be satisfied.
- **Documentation is a hypothesis; captured payloads are evidence.** Both field-name bugs
  were invisible to 348 passing tests, because every test fed the adapter payloads written
  from the same docs the adapter was written from. Tests now run against real captured
  payloads.
- **A safety layer must not fail silently.** A developer who believes they are guarded and
  is not is worse off than one who knows. Fail-open stays; being quiet about it does not.
- **Defaults that only tests override are untested defaults.** The daemon's port default
  was wrong in a way that would have disabled AgentGuard in every real install, and every
  test passed because every test supplied the value explicitly.
- **Read the agent's test output rather than running tests.** The output is ground truth
  and arrives free in `PostToolUse`. Running a second suite concurrently invites conflicts
  over ports, fixtures and temp files, and is slow on a path meant to be invisible.
- **"Could not tell" is never "failed".** Unrecognised test output yields no opinion.
  Accusing an agent of breaking tests on the strength of an unparsed string is worse than
  silence.
- **Unit tests that bypass the wiring can pass while the product does nothing.** The
  task-id bug proved it. Every phase now needs at least one test through the real Guard.
- **Project isolation is a type, not a convention.** With one shared database, the engine
  holds only a project-scoped handle; a cross-project query is unrepresentable rather than
  merely discouraged.
- **Persistence is never a dependency.** Evidence checks, challenges and verification do
  not need to write anything in order to work, so a closed database or a full disk stops
  logging and nothing else.
- **Round-robin across domains when listing what to investigate.** Taking domain concerns
  in order let backend and ML consume every slot and silently drop MLOps — the single-lens
  blindness SPEC §10 exists to prevent.

- **AgentGuard never returns `permissionDecision: "allow"`.** "allow" bypasses the user's own
  permission rules; a guard that auto-approved would make the system less safe. It can only
  stay silent, object (`deny` + reason), or escalate (`ask`). Enforced by a test that walks
  every decision action.
- **Fail-open is structural, not aspirational.** `Guard.handle()` catches everything; the
  daemon returns `200 {}` on auth failure, bad JSON, unknown adapter and internal crash; the
  shim exits 0 silently on every error path.
- **Fixed daemon port (8787)** because the hook URL is baked into settings.json at install
  time and cannot be rewritten per daemon restart. `doctor` detects a port conflict.

---

## Open questions for the user

| # | Question | Needed by |
|---|---|---|
| 1 | Which repository should Phase 6 validation run against? A throwaway scratch repo is safest; a real project is more convincing. | Phase 6 |
| 2 | Is "production ready + deployed" satisfied by a local-first installable release (PyPI + docs), or do you also want a hosted/team component? | Phase 11 |

## Known issues / deferred

- **88 ms latency outlier** seen once in 220 iterations (likely a SQLite WAL auto-checkpoint;
  two commits per tool call). Inside budget at p95/p99, so recorded rather than fixed. If the
  tail grows as engines land, the fix is a batched write queue — Phase 10.
- **`MODIFY` uses `permissionDecision: "defer"` + `updatedInput`.** The docs' own example
  pairs `updatedInput` with `"allow"`, which we refuse to emit. `defer` should preserve the
  user's permission flow while still rewriting arguments — **to be confirmed empirically in
  Phase 5/6** against a live session.
- **HTTP hook failure semantics** (connection refused → non-blocking error) are inferred from
  the documented `command` hook exit-code table; confirm empirically in Phase 6.

---

## Log

### 2026-08-11
- Read and analysed the SPEC in full.
- Verified the Claude Code hook contract from official docs (see Verified facts).
- Wrote `IMPLEMENTATION_PLAN.md`: 13 phases in two acts, Phase 6 = the §50 checkpoint.
- **Phase 0 complete.** 84 tests, ruff clean. Foundation, normalized event model, SQLite
  store, metrics, Guard orchestrator, FastAPI daemon, Claude Code translation + installer +
  shim, CLI. Transport benchmarked and chosen on evidence (`docs/BENCH-latency.md`).
- **Phase 1 complete.** 128 tests, ruff clean. Repository intelligence: scanner, Python
  (`ast`) and tree-sitter symbol extraction, import + reverse-import graph, dependency
  manifests, test map, git state. Fixture repos modelled on the SPEC's own §14/§33
  examples so later phases test against the same ground truth. Index build made async
  after measuring 5.6 s on a real monorepo. Hot-path latency unchanged.
- **Phase 2 complete.** 186 tests, ruff clean. Intent Gateway (grounded target
  resolution, domain classification, constraints, ambiguity), Complexity Engine (8
  signals + 6 override rules), Planning Governor. All six SPEC anchors hit, in both
  directions — the rename stays a 3-step rename, and production-readiness is allowed to
  be deep. Two matching bugs found and fixed: substring term matching, and first-come
  ordering dropping whole domains from the investigation list.
- **Phase 3 complete.** 244 tests, ruff clean. Evidence Engine (claim extraction with local
  type inference, resolution against the index, seven verdicts) and Contradiction Engine
  (challenge rendering in the §14 format, ledger rationing per §17/§39). A false-positive
  audit against three real repositories found four bugs the synthetic corpus could not; the
  rate went from 2.2% to 0 in 5,750 claims, and the audit is now a permanent test.
- **Phase 3.5 complete.** 260 tests, ruff clean. Adopted the Memory & Database Management
  Plan and restructured Act II per the Local Semantic Memory plan. Single project-scoped
  database, retention tiers, bounded rows, background maintenance, disk-space degradation,
  and the `memories` table that Phase 9 will promote into. `agentguard db stats |
  projects | maintain` make the lifecycle observable.
- **Phase 4 complete.** 328 tests, ruff clean. Action Validator (scope creep,
  proportionality, risky commands), Verification (runner detection, test-output parsing,
  static syntax checks) and the Completion Gate. A turn that claims passing tests it did
  not earn is now blocked with the agent's own output as evidence. Found and fixed a bug
  that had been suppressing every challenge in the real pipeline.
- **Phase 5 complete.** 348 tests, ruff clean. Full adapter, install/uninstall UX, kill
  switch, and the two decisions taken with the user: MODIFY under an enforced narrowing
  invariant, and visible failure when the daemon cannot be revived. Found and fixed a
  port-default bug that would have silently disabled AgentGuard in every real install.
- **Phase 6 complete — the §50 milestone is met.** 372 tests, ruff clean. Real agent, real
  repository, evidence-grounded challenges resolved by the host's own intelligence, verified
  completion, no LLM on AgentGuard's side. Four defects found and fixed; see
  `docs/VALIDATION-phase6.md` including what is still unproven.

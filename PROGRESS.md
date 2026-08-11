# AgentGuard — Progress Tracker

Living status. Updated at the end of every phase.
Plan: `IMPLEMENTATION_PLAN.md` · Source of truth: `AgentGuard — Host-Powered AI Agent Reliability & Reasoning Layer.md`

**Current phase:** Phase 2 — Intent Gateway + Complexity + Planning Governor (next)
**Act I goal:** SPEC §50 milestone (Phase 6 real-world validation)
**Suite:** 128 tests passing · ruff clean

---

## Status board

| Phase | Name | Status | Exit criteria |
|---|---|---|---|
| 0 | Foundation + hook plumbing spike | ✅ **done** | all met — see below |
| 1 | Repository Intelligence | ✅ **done** | all met — see below |
| 2 | Intent Gateway + Complexity + Planning Governor | ⬜ next | — |
| 3 | Evidence Engine + Contradiction Engine | ⬜ not started | — |
| 4 | Action Validator + Verification + Completion Gate | ⬜ not started | — |
| 5 | Full Claude Code adapter + install UX | ⬜ not started | — |
| 6 | 🔬 First real-world validation (§50 milestone) | ⬜ not started | — |
| 7–12 | Act II — production | ⬜ blocked on Phase 6 sign-off | — |

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

## Spec conformance suite

Acceptance tests derived from the SPEC's own worked examples. These are the real
definition of "it works".

| Test | SPEC § | Status |
|---|---|---|
| `test_s06_no_llm` (4 tests) | §6, §46.1 | ✅ |
| `test_s08_latency_budget` (5 tests) | §8 | ✅ |
| `test_s02_rename_stays_simple` | §2, §13 | ⬜ Phase 2 |
| `test_s02_scaling_goes_deep` | §2, §13 | ⬜ Phase 2 |
| `test_s09_pagination_intent` | §9 | ⬜ Phase 2 |
| `test_s10_domain_multi` | §10 | ⬜ Phase 2 |
| `test_s34_inference_deep` | §34 | ⬜ Phase 2 |
| `test_s14_hallucinated_method` | §14 | ⬜ Phase 3 |
| `test_s14_no_false_positive` | §14 | ⬜ Phase 3 |
| `test_s17_justified_complexity` | §17 | ⬜ Phase 3 |
| `test_s39_stays_silent` | §39 | ⬜ Phase 3 |
| `test_s18_scope_violation` | §18 | ⬜ Phase 4 |
| `test_s19_false_completion` | §19 | ⬜ Phase 4 |
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
│   ├── store.py      SQLite: sessions/tasks/events/decisions/findings/
│   │                 challenges/verifications/metrics (§30)
│   ├── metrics.py    latency percentiles (§8, §37)
│   └── engine.py     Guard — the one place an event becomes a Decision (§28)
├── adapters/claude_code/
│   ├── translate.py  Claude JSON ⇄ AgentEvent ⇄ hook output
│   ├── install.py    safe settings.json merge / uninstall
│   └── shim.py       stdlib-only fallback + --ensure-daemon
├── repo/             ── the deterministic evidence base (§32) ──
│   ├── models.py     FileRecord, SymbolRecord, ImportRecord, GitState, DependencyInfo
│   ├── scanner.py    gitignore-aware discovery (git ls-files fast path + walk fallback)
│   ├── symbols_python.py  stdlib `ast` extraction (exact, no grammar drift)
│   ├── symbols_ts.py      tree-sitter for TS/JS/Go/Rust/Java/Ruby, optional
│   ├── manifests.py  pyproject / requirements / package.json / go.mod / Cargo.toml
│   ├── gitinfo.py    branch, dirty set, recent commits, per-file churn (TTL-cached)
│   └── index.py      RepoIndex — symbol map, import + reverse-import graph, test map
├── daemon/app.py     FastAPI, 127.0.0.1, token auth, handshake file
└── cli/main.py       install · uninstall · doctor · log · why · index · find-symbol ·
                      explain · daemon
```

Handlers for user_prompt / pre_tool_use / stop are wired but currently return ALLOW —
Phases 2–4 fill them in. post_tool_use already keeps the index fresh.

---

## Verified facts (confirmed, not assumed)

- **2026-08-11** — Claude Code hook contract verified against `code.claude.com/docs/en/hooks`:
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

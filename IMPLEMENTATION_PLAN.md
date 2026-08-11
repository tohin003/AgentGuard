# AgentGuard — Implementation Plan

**Source of truth:** `AgentGuard — Host-Powered AI Agent Reliability & Reasoning Layer.md`
(referred to below as **SPEC**; `§N` = section N of that file).

Every phase below lists the SPEC sections it implements and the tests that prove it.
No phase is "done" until its exit criteria pass *and* its spec-conformance tests pass.

---

## 0. What we are actually building (restated, to check my understanding)

AgentGuard is **not** an agent, not an LLM wrapper, not an MCP server. It is a
**deterministic control plane** that sits beside an existing coding agent and makes that
agent's mistakes hard to execute.

The five load-bearing ideas, in the order they matter:

1. **Bring Your Own Brain** (§5, §6, §22, §46.1–2). AgentGuard owns *zero* LLM calls.
   When a semantic judgement is needed, it does not think — it **hands the problem back to
   the host agent** as a challenge, and the host's own intelligence resolves it. Every
   internal check must therefore be filesystem/AST/git/test-derived, or it must be a
   question posed to the host.

2. **Evidence before action** (§14, §15, §46.3). The agent says
   `UserRepository.get_active_users()`; AgentGuard asks the repository whether that exists.
   If it does not, the claim is `INSUFFICIENT_EVIDENCE` and the host gets challenged.
   This is the primary hallucination-reduction mechanism.

3. **Proportional planning, not minimal planning** (§2, §12, §13, §17, §46.6). This is the
   idea most systems get wrong. AgentGuard is *not* an anti-complexity system; it is an
   **evidence-based proportionality system**. "Rename getUser()" must stay a 3-step task
   (≈2/100). "Introduce distributed session caching" must be allowed to become a deep
   architectural investigation (80+/100). A build that only ever says "keep it simple" is a
   **failed** build.

4. **Silence is the default** (§7, §8, §39). Level 0 deterministic checks on the hot path,
   sub-100ms, ALLOW without a word. Escalate to Level 1 (repo lookup) → Level 2 (host
   challenge) → Level 3 (deep/async verification) only on real signal. "ABS for AI agents":
   invisible until it isn't.

5. **Verified completion** (§19, §20). "Done" is a claim like any other and must be backed
   by executed tests, static analysis, diff analysis and requirement coverage.

### Non-goals, enforced as design constraints

| Must never happen | Enforcement |
|---|---|
| AgentGuard makes its own LLM call | No provider SDK in `agentguard-core` deps; import-guard test in CI |
| AgentGuard blocks the agent when it is broken/slow | Hard fail-open on every hook path + timeout budget test |
| AgentGuard nags (§17, §39) | Challenge ledger: one challenge per claim per task, hard caps, then defer |
| AgentGuard forces simplicity (§17, §34) | `UNDERENGINEERED` verdict + justified-complexity ALLOW path, both spec-tested |
| Latency the developer can feel (§8) | p50/p95 budgets asserted in the test suite, not aspirational |

---

## 1. Architecture decisions (made, with reasons)

### D1 — Transport: HTTP hook to a local daemon, with a command-shim fallback

Claude Code hooks support `{"type": "http", "url": "..."}`. Claude Code performs the HTTP
request itself, so on the hot path there is **no Python process spawn at all** — the cost is
a localhost round trip (~1–3ms) plus our compute. A `command` hook, by contrast, pays
30–80ms of interpreter startup *per tool call* before doing any work, which eats most of the
§8 budget.

- **Primary:** long-lived local daemon (FastAPI/uvicorn on 127.0.0.1, ephemeral port,
  token-authenticated) holding the warm `RepoIndex` in memory. §30.
- **Fallback:** a stdlib-only `agentguard-hook` shim (command hook) that forwards stdin JSON
  over a Unix domain socket, for environments where HTTP hooks are unavailable.
- **Both fail open.** Daemon down, port moved, timeout exceeded, exception raised → the
  hook returns "no decision" and the agent proceeds. Verified by a test that kills the
  daemon mid-run.

Phase 0 measures both and records real numbers before anything is built on top.

### D2 — `MODIFY` is real

`PreToolUse` accepts `updatedInput`, which rewrites tool arguments before execution. §18
lists `MODIFY` as a decision and this makes it implementable rather than aspirational (e.g.
narrowing a `Bash` command, stripping an unrelated file from a multi-file edit). Used
sparingly — silently changing what the agent asked for is a trust hazard, so `MODIFY` is
reserved for provably-safe narrowing and always reports itself via `additionalContext`.

### D3 — Hook → SPEC component mapping

| Hook event | AgentGuard component | SPEC |
|---|---|---|
| `SessionStart` | Warm index, open session, inject repo profile | §30, §32 |
| `UserPromptSubmit` | **Intent Gateway** + **Complexity Engine** + **Planning Governor** → `additionalContext` | §9, §12, §13 |
| `PreToolUse` | **Evidence Engine** + **Action Validator** → allow/deny/ask/updatedInput | §14, §16, §18 |
| `PostToolUse` | Scope ledger, diff tracking, async **Verification** trigger | §18, §19 |
| `Stop` | **Completion Gate** → `decision:"block"` when unverified | §19, §20 |
| `SessionEnd` | Flush metrics, close session | §37 |

`stop_hook_active` (present when a Stop hook is already holding the session open) is the
loop-breaker for the Completion Gate.

### D4 — Runtime and dependency posture

- Python **3.12** (broadest wheel coverage for tree-sitter; 3.14 is on this machine but not
  all native wheels exist yet). Managed by `uv`.
- Core deps: `pydantic`, `typer`, `fastapi`, `uvicorn`, `httpx`, `pyyaml`. Optional:
  `tree-sitter` + `tree-sitter-language-pack` (JS/TS/Go symbols), degrading to regex when
  absent. Python symbols use stdlib `ast` — no dependency, no grammar loading cost.
- Git via subprocess `git` (faster and fewer failure modes than GitPython for the read-only
  queries we need). `ripgrep` used when present, stdlib walk otherwise.
- **No vector DB** (§31, explicit).
- Storage: SQLite (WAL) at `<workspace>/.agentguard/agentguard.db`; global config at
  `~/.agentguard/config.toml`. §30.

### D5 — Package layout

```
agentguard/
├── core/        events, models, config, store, metrics, escalation
├── repo/        scanner, symbols, imports, manifests, tests_map, gitinfo, index
├── intent/      extractor, taskspec
├── complexity/  engine, signals, rules
├── evidence/    engine, claims, extractors, resolvers
├── policy/      loader, packs/{software,ai,devops,security}/*.yaml
├── validate/    validator, checks/*
├── challenge/   ledger, renderer
├── verify/      runners, static, diff, coverage, completion_gate
├── daemon/      app, routes, lifecycle
├── adapters/    claude_code/, cursor/, codex/
├── mcp/         server
├── cli/         main
└── bench/       harness, tasks/
```

### D6 — Spec Conformance Suite (how "validating against the SPEC" is made concrete)

`tests/spec/` contains one test module per SPEC behaviour, each named for its section and
docstring-quoting the SPEC text it enforces. These are the acceptance tests for the whole
project. Initial set, all derived from worked examples the SPEC itself gives:

| Test | SPEC | Asserts |
|---|---|---|
| `test_s02_rename_stays_simple` | §2, §13 | "Rename getUser→fetchUser" ⇒ complexity ≤ 20, depth `DIRECT`, ≤ 4 plan steps |
| `test_s02_scaling_goes_deep` | §2, §13 | "Make auth horizontally scalable" ⇒ ≥ 71, depth `DEEP` |
| `test_s09_pagination_intent` | §9 | "/users pagination" ⇒ domain backend, complexity low, risk medium, unnecessary-actions list includes caching/new service |
| `test_s10_domain_multi` | §10 | "Change the prediction API" ⇒ primary `ml_engineering`, secondary ⊇ {backend, mlops} |
| `test_s14_hallucinated_method` | §14 | `UserRepository.get_active_users()` absent ⇒ `INSUFFICIENT_EVIDENCE` + challenge naming the file as evidence |
| `test_s14_no_false_positive` | §14 | Symbol *defined in the same edit* ⇒ `SUPPORTED`, no challenge |
| `test_s17_justified_complexity` | §17 | Abstraction with 3 real consumers in repo ⇒ ALLOW, not challenge |
| `test_s18_scope_violation` | §18 | "Fix login validation" + 17 unrelated files ⇒ `CHALLENGE`, scope violation |
| `test_s19_false_completion` | §19 | Stop with untested changes ⇒ gate `INCOMPLETE`, Stop blocked |
| `test_s33_end_to_end` | §33 | Full pagination scenario: over-engineered proposal → challenge → revised proposal → allow → verify → PASS |
| `test_s34_inference_deep` | §34 | "Make inference service production-ready" ⇒ DEEP, no "keep it simple", multi-domain |
| `test_s39_stays_silent` | §39 | 100 ordinary read/edit ops ⇒ 0 challenges, 0 blocks |
| `test_s08_latency_budget` | §8 | L0 p95 < 100ms, L1 p95 < 500ms, measured |
| `test_s06_no_llm` | §6, §46.1 | No LLM SDK importable/imported anywhere in core |

---

## 2. Phases

**Act I (Phases 0–6) proves the thesis.** It ends at the SPEC §50 milestone, which is the
checkpoint you asked for: attach to a real agent on a real task and watch it work.
**Act II (Phases 7–12) is only started after you confirm Act I actually behaves as intended.**

---

### Phase 0 — Foundation + hook plumbing spike  ⟵ retires the two biggest risks first
**Goal:** prove the transport works and is fast *before* building intelligence on top of it.

**Deliverables**
- `uv` project, Python 3.12, package skeleton (D5), ruff + pytest + coverage config.
- `core/events.py` — normalized `AgentEvent` model (§23), adapter-agnostic.
- `core/store.py` — SQLite schema + migrations: sessions, tasks, events, decisions,
  claims, evidence, findings, challenges, verifications, metrics (§30).
- `core/config.py`, `core/metrics.py` (latency histograms per level, §37).
- `daemon/` — FastAPI app, `/health`, `/hook` endpoint, token auth, PID/port file at
  `~/.agentguard/daemon.json`, graceful start/stop.
- `adapters/claude_code/` — event translation both directions (Claude JSON ⇄ `AgentEvent`
  ⇄ Claude hook output), currently returning a trivial ALLOW.
- `agentguard-hook` stdlib-only command shim (fallback path).
- `cli/` — `agentguard daemon start|stop|status`, `agentguard install claude`,
  `agentguard doctor`.
- **Latency bench** comparing http-hook vs command-shim vs cold-start, recorded in
  `docs/BENCH-latency.md`.

**Tests** — daemon lifecycle; hook round trip for all 6 events; **fail-open under daemon
kill / timeout / malformed payload / oversized payload**; `test_s06_no_llm`; latency probe.

**Exit criteria**
- End-to-end: Claude Code hook fires → daemon → decision → agent proceeds, with an entry in
  SQLite.
- Measured hot-path overhead **< 100ms p95** (§8), number recorded.
- Killing the daemon mid-session does not impede the agent in any way.

---

### Phase 1 — Repository Intelligence (§32)
**Goal:** the deterministic evidence base everything else queries.

**Deliverables**
- `scanner` (gitignore-aware, `git ls-files` fast path), `symbols` (stdlib `ast` for Python;
  tree-sitter for JS/TS/Go with regex degradation), `imports` + **reverse-import graph**
  (this is blast radius), `manifests` (pyproject/requirements/package.json/go.mod/Cargo.toml
  → declared deps), `tests_map` (test files → covered modules, via imports + naming),
  `gitinfo` (branch, dirty set, recent commits, per-file churn), `configs`.
- `RepoIndex` facade: warm in-memory, SQLite-persisted, **mtime-incremental** refresh, plus
  `watchPaths` wiring from `SessionStart`.
- `cli`: `agentguard index`, `agentguard find-symbol`, `agentguard explain <file>`.

**Tests** — golden fixture repos (`tests/fixtures/pyrepo`, `jsrepo`, `mixedrepo`) with
asserted symbol/import/test maps; incremental-update correctness after edit/add/delete/rename;
index build time on a large repo; warm lookup latency < 5ms.

**Exit criteria** — on this machine's real repos: index builds in seconds, symbol lookup is
sub-5ms warm, reverse-import blast radius is correct on the fixtures.

---

### Phase 2 — Intent Gateway + Complexity Engine + Planning Governor (§9–§13)
**Goal:** turn a prompt into a repo-grounded `TaskSpec`, and set planning depth proportional
to reality.

**Deliverables**
- `intent/extractor.py` — deterministic: verb class, target extraction (identifiers, paths,
  endpoints, quoted strings), **target resolution against `RepoIndex`** (this is what makes
  it grounded rather than keyword-matching), constraints, acceptance criteria, ambiguity
  markers, expected & forbidden scope (§9's "Unnecessary actions" list).
- `complexity/engine.py` — 8 signals per §12 (scope, dependency count, architectural impact,
  data risk, security risk, uncertainty, blast radius, reversibility), each emitting a score
  *with its evidence*, plus **override rules** so it is a decision system, not a formula
  (§12: "simple-looking task that crosses service boundaries ⇒ increase depth").
- Bands → `DIRECT / LIGHT / STRUCTURED / DEEP` (§12 0–20/21–40/41–70/71–100).
- `Planning Governor` → planning budget rendered as `additionalContext` on
  `UserPromptSubmit`: what to investigate, what *not* to (§13).
- Domain classification (§10) incl. multi-domain `primary`/`secondary`.

**Tests** — `test_s02_*`, `test_s09_pagination_intent`, `test_s10_domain_multi`,
`test_s13_*`, `test_s34_inference_deep`; a calibration table of ~40 prompts with expected
bands; **anti-regression: no prompt in the "simple" set may score above 20.**

**Exit criteria** — the SPEC's own four worked examples land in their stated bands, and the
injected planning budget for a rename is visibly ~3 lines while the one for distributed
caching enumerates the §13 list.

---

### Phase 3 — Evidence Engine + Contradiction Engine (§14–§17)
**Goal:** catch unsupported claims; challenge the host in its own language; **do not cry wolf**.

**Deliverables**
- `evidence/claims.py` — claim taxonomy: `symbol_exists`, `attribute_on_type`, `file_exists`,
  `module_importable`, `dependency_declared`, `endpoint_exists`, `config_key_exists`,
  `test_exists`.
- `evidence/extractors.py` — pull claims from `Write.content`, `Edit.new_string`
  **applied to the file to get true post-edit content**, `Bash.command`, and assistant text.
- `evidence/resolvers.py` — resolve against repo symbols ∪ **symbols defined in this very
  edit** ∪ locally bound names ∪ stdlib ∪ declared deps ∪ installed packages.
  *This union is the false-positive firewall and is the single most important correctness
  requirement in the phase* — flagging a symbol the agent is currently defining would make
  AgentGuard unusable.
- Verdicts per §16: `SUPPORTED`, `SUPPORTED_WITH_RISK`, `INSUFFICIENT_EVIDENCE`,
  `CONTRADICTED`, `OVERENGINEERED`, `UNDERENGINEERED`, `REQUIRES_HUMAN`.
- `challenge/renderer.py` — challenge text in the SPEC §14 format: claim, evidence, file
  reference, and an instruction to re-evaluate *or justify*.
- `challenge/ledger.py` — fingerprinted one-challenge-per-claim-per-task, escalation caps,
  and the §17 **justified-complexity acceptance path**.

**Tests** — `test_s14_hallucinated_method`, `test_s14_no_false_positive` (a family of ~20
"legitimately new code" cases), `test_s17_justified_complexity`, ledger dedupe/cap tests,
`test_s39_stays_silent`.

**Exit criteria** — measured on fixtures: **zero false challenges** across the legitimate-code
corpus, and every seeded hallucination caught.

---

### Phase 4 — Action Validator + Verification + Completion Gate (§18–§20)
**Goal:** decide, verify, and refuse to rubber-stamp "Done".

**Deliverables**
- `validate/validator.py` — the §18 pipeline (evidence → scope → architecture → risk →
  complexity) with **per-check level tags** so L0 short-circuits for read-only tools, and
  decisions `ALLOW / BLOCK / CHALLENGE / MODIFY / REQUEST_REVIEW`.
- Checks: scope creep (§18's 17-file example), new-dependency justification, repo-pattern
  consistency, destructive/risky command detection, over/under-engineering signals.
- `verify/` — test-runner detection (pytest/jest/vitest/go/cargo/npm scripts), **affected-test
  selection** from the import graph, async execution via `async: true` hooks, static analysis
  (parse/compile changed files; ruff/eslint when present), git-diff analysis, requirement
  coverage against `TaskSpec` acceptance criteria.
- `verify/completion_gate.py` → `PASS / INCOMPLETE / VERIFICATION_FAILED /
  HUMAN_REVIEW_REQUIRED`, wired to `Stop` with `stop_hook_active` loop-breaking.
- Domain-specific test expectations (§20) sourced from policy packs.

**Tests** — `test_s18_scope_violation`, `test_s19_false_completion`, gate-loop-safety
(never blocks Stop more than the cap), async verification does not stall the turn.

**Exit criteria** — a deliberately-lying "I've completed it, all tests pass" turn is caught
and blocked with an accurate reason.

---

### Phase 5 — Full Claude Code adapter + install UX
**Goal:** one command to attach, one command to detach, nothing else to learn (§8 "install
once → forget it exists").

**Deliverables**
- All six hooks wired to the real engines; matcher config that only intercepts what matters.
- `agentguard install claude [--global|--project]` writing `settings.json` hooks safely
  (merge, never clobber; `--dry-run`; `agentguard uninstall`).
- Session/task lifecycle: prompt → task, tool calls → task, Stop → gate.
- Transparency surface: `agentguard log`, `agentguard why <decision-id>`,
  `agentguard status` (§37 metrics).
- Kill switch: `AGENTGUARD_DISABLE=1` and `agentguard off`.

**Tests** — settings merge/idempotency/uninstall-restores-exactly; full simulated session
replay through the real adapter; `test_s33_end_to_end`.

**Exit criteria** — install on a scratch repo, run a real Claude Code session, and the agent
behaves normally with AgentGuard invisible.

---

### Phase 6 — 🔬 FIRST REAL-WORLD VALIDATION  ⟵ **your checkpoint**
**Goal:** the SPEC §50 milestone, verbatim:

> Take a real coding task, attach AgentGuard to a real coding agent, observe its decisions,
> detect unsupported assumptions or unnecessary complexity, challenge the host using its own
> intelligence, allow valid actions, block/redirect invalid actions, and verify the final
> implementation — all without AgentGuard owning an LLM.

**Method** — a scratch repository (I will propose candidates when we get here) and a scripted
set of real tasks run through a live Claude Code session with AgentGuard attached:
1. **Trivial task** (rename) → expect: invisible, no challenge, tiny planning budget.
2. **Grounded task** (§33 pagination) → expect: over-engineering challenged, revised plan
   allowed, completion verified.
3. **Hallucination-bait task** (asks for a method that does not exist) → expect: challenge
   with file evidence, host self-corrects.
4. **Complex task** (§34 production-readiness) → expect: DEEP depth, *no* simplicity pressure.
5. **False-completion bait** → expect: Stop blocked with an accurate reason.

**Evidence captured** — full decision log, latency distribution, challenge/allow counts,
before/after transcripts, and an honest write-up in `docs/VALIDATION-phase6.md` including
**anything that did not work**.

**Exit criteria** — you review the report and confirm the idea behaves as intended. If it
does not, we iterate here rather than proceeding.

---

### Act II — production (starts only after your Phase 6 sign-off)

| Phase | Content | SPEC |
|---|---|---|
| **7** | Engineering Policy Packs (full YAML set), MCP server (`inspect_repository`, `find_symbol`, `find_evidence`, `check_complexity`, `validate_action`, `get_domain_policy`, `verify_requirement`, `run_verification`), decision logs, aggressive caching, perf instrumentation | §11, §25, §41 |
| **8** | **AgentGuard-Bench** + metrics: agent-alone vs agent+AgentGuard across backend/frontend/db/devops/ml/llm/cv/security/debugging/refactoring | §36, §37, §42 |
| **9** | Cursor adapter, Codex adapter | §24 |
| **10** | Hardening: fuzzing hook inputs, concurrency, huge-repo scale, monorepo, error budgets, security review of the daemon | §30, §39 |
| **11** | Packaging & distribution: PyPI, versioned releases, `pipx`/`uv tool install`, docs site, quickstart, telemetry opt-in | — |
| **12** | Optional per §43–§44: Copilot/OpenHands/ACP adapters, IDE dashboard, browser extension experiments | §43, §44 |

Note on "deployment": AgentGuard is a **local-first developer tool** (§30 — offline,
no network calls). "Production ready + deployed" therefore means Phase 11 (installable
release + docs), not a cloud service. If you want a hosted component (team dashboards,
shared benchmarks), say so and I will add it as a separate phase.

---

## 3. Working agreement

- One phase at a time. Each ends with tests green and `PROGRESS.md` updated.
- Every phase's spec-conformance tests stay green forever (they are the regression net).
- If the SPEC and reality conflict, I stop and tell you rather than silently reinterpreting.
- Honest reporting: failures reported as failures, with output.

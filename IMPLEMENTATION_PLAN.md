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

### D2 — `MODIFY` is real, and may only ever narrow  *(decided with the user, 2026-08-11)*

`PreToolUse` accepts `updatedInput`, which rewrites tool arguments before execution. §18
lists `MODIFY` as a decision and this makes it implementable rather than aspirational.

The open question was whether emitting `updatedInput` requires `permissionDecision:
"allow"`, which bypasses the developer's own permission rules. **The user's decision:
allow it.** Their reasoning: the rewrite is a *narrowing* of something the host LLM already
proposed and would have run in auto mode, and genuinely risky actions are handled by a
different decision (`REQUEST_REVIEW` → `"ask"`) rather than by rewriting.

That reasoning holds *because the rewrite is a narrowing*, so the implementation makes
that a property the code enforces rather than an assumption:

1. **A rewrite may only reduce reach, never extend it.** Narrowing a path, dropping a
   flag, removing a file from a multi-file edit. Anything that could touch more than the
   original is not a `MODIFY`.
2. **The rewritten arguments are re-checked.** If the rewrite itself trips a risk check,
   the decision escalates to `REQUEST_REVIEW` instead. A bug in the rewriting logic must
   not be able to launder a dangerous command through a bypassed prompt.
3. **It always announces itself** via `additionalContext`. Silently changing what the
   agent asked for is a trust hazard even when the change is an improvement.
4. **`"defer"` is tried first.** If Claude Code honours `updatedInput` alongside `"defer"`,
   we get the rewrite *and* the developer's permission flow, which is strictly better.
   Phase 6 settles it; `"allow"` is the fallback the user has authorised.

Risk accepted knowingly: for the specific call being rewritten, the developer's permission
prompt is skipped. Invariants 1–3 bound what can be done with that.

### D9 — A dead AgentGuard tells you it is dead  *(decided with the user, 2026-08-11)*

Fail-open was previously *silent*: daemon dies, hooks fail, the agent carries on, nobody
knows. The user identified the flaw — **silent failure of a safety layer is worse than
visible failure, because you believe you are protected when you are not.**

Required behaviour: detect, re-verify, tell the developer, and let them choose whether to
continue unguarded.

Implementation (Phase 5):

* `UserPromptSubmit` gains a second, `command`-type hook that health-checks the daemon and
  attempts **one revival**. Once per prompt, not per tool call, so the ~60 ms is invisible.
* Revived or healthy → exit 0, silent. Nothing changes.
* Unrecoverable → write to stderr and exit non-zero-non-2. Claude Code surfaces the first
  stderr line in the transcript as a hook error, and treats it as **non-blocking** — so the
  developer sees it and the work proceeds. That is exactly "notify, do not block, user
  decides".
* Announced **once per session**, not per prompt. A warning repeated every turn is the
  nagging SPEC §39 forbids, even when the warning is true.
* The message has to be actionable: how to restart, and how to detach.

Fail-open is unchanged as a mechanism. What changes is that it is no longer quiet about it.

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

### D7 — Storage: one database, scoped by project

Per the **Memory & Database Management Plan**. Supersedes the original per-workspace
database.

```
~/.agentguard/
├── agentguard.db        one database, all projects
├── config.toml
├── token · daemon.json
└── projects/<id>/       per-project caches
```

Every row carries a `project_id`, and the engine only ever holds a **project-scoped
handle** that cannot express a cross-project query. The memory plan's §4 — *"AgentGuard
must never accidentally use Project A's architectural knowledge while working on Project
B"* — is therefore a structural property, not a convention, and is tested as one.

Three consequences that are reliability requirements rather than memory features, so they
belong in Act I:

* **Bounded rows.** "Never store huge objects unnecessarily" (§6): store task id,
  decision, evidence *references*, violation type, short summary. Source stays on disk.
* **Retention + maintenance** (§5, §7): configurable ages, pruning and checkpointing run
  in the background and never while the agent is executing.
* **Storage must never be a dependency.** The plan's §8 critical rule — *"If SQLite fails
  or disk space becomes unavailable, AgentGuard's core reliability functionality must
  continue working"* — is the same fail-open guarantee as D1, extended to storage.

### D8 — Memory is an escalation capability, not a layer in the hot path

Per the **ACT II Local Semantic Memory** plan. Retrieval happens for complex or ambiguous
tasks, never on every tool call — the same escalation ladder as SPEC §7.

The load-bearing idea, and the reason this strengthens rather than dilutes the thesis:

> **Vector memory suggests relevant knowledge; the current repository decides whether that
> knowledge is still true.**

Three sources of truth, with distinct jobs: **current code** answers "what is true now",
**memory** answers "what did we learn before", **host LLM** answers "what should we do".
Memory never gets the last word — a remembered fact that the repository now contradicts is
stale, and stale memory is worse than none.

**Embeddings and the no-LLM principle.** An embedding model is not a reasoning model, so a
pluggable embedding provider does not violate SPEC §6. The boundary that *does* need
holding: `agentguard` core must never gain an embedding dependency. Providers arrive in
Phase 10 as an optional extra (`agentguard[embeddings]`), import-guarded, absent by
default. `test_s06_no_llm` is extended then to assert the *core* dependency set stays
clean rather than relaxed.

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

### Phase 3.5 — Storage foundation & data lifecycle  ⟵ inserted after the memory plans
**Goal:** put the storage architecture in place *before* Phase 4 starts producing the
high-volume data it governs. Only the parts that are cheap now and expensive to retrofit;
the intelligence built on top of them is Act II (Phases 9–11).

**Deliverables**
- Single database at `~/.agentguard/agentguard.db`, replacing per-workspace files, with a
  `projects` table and a stable project identity (git remote when available, canonical
  path otherwise).
- `ProjectStore`: a project-scoped handle that **cannot** express a cross-project query.
- `memories` table, with the ACT II plan's full metadata (`memory_type`, `source_files`,
  `confidence`, `validation_status`, `last_verified`). Written to from Phase 9 — created
  now so Phase 4's violations and verification results are recorded in a shape that can be
  promoted later without a migration.
- Retention configuration (memory plan §5) and a pruning/maintenance pass: expiry,
  `wal_checkpoint`, incremental vacuum. Runs on `SessionEnd` and rate-limited, never
  during agent execution (§7).
- Bounded rows (§6): argument blobs summarised rather than stored whole.
- Disk-space monitoring with healthy / low / critical degradation (§8).

**Tests** — project isolation (A cannot read B, by construction); retention prunes exactly
what it should and nothing long-lived; **a read-only, full, or deleted database does not
impede a single agent action**; maintenance never runs on the hot path; row size bounded.

**Exit criteria** — the §8 critical rule holds under test: with the database made
unwritable mid-session, every hook still returns a decision and the agent proceeds.

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

**Method** — real `claude -p` sessions with AgentGuard installed, in two passes.

**Pass 0 — the assumption that has to hold first.** Install the hooks, kill the daemon
mid-session, ask the agent to edit a file. If a failed `http` hook is *not* a non-blocking
error, AgentGuard crashing breaks the developer's agent, and the architecture has to change
before anything else is worth measuring. Run before the scenarios, not after.

**Pass A — scripted scenarios** on a purpose-built scratch repository, seeded so each
scenario has exactly the conditions it needs (a pagination utility that already exists, a
class *without* the method the agent will reach for, a runnable test suite):
1. **Trivial task** (rename) → expect: invisible, no challenge, tiny planning budget.
2. **Grounded task** (§33 pagination) → expect: over-engineering challenged, revised plan
   allowed, completion verified.
3. **Hallucination-bait task** (asks for a method that does not exist) → expect: challenge
   with file evidence, host self-corrects.
4. **Complex task** (§34 production-readiness) → expect: DEEP depth, *no* simplicity pressure.
5. **False-completion bait** → expect: Stop blocked with an accurate reason.

**Pass B — realism check** on a *copy* of a large private monorepo (cloned to a temp
directory; nothing of the developer's is at risk), with ordinary prompts rather than
scripted ones. Pass A cannot
surprise me — it is seeded to conditions I chose. Phase 3 already demonstrated the danger
there: a hand-written corpus passed 26/26 while real code showed a 2.2% false-positive
rate. Pass B is where "is it actually invisible during normal work" gets answered.

**Evidence captured** — full decision log, latency distribution, challenge/allow counts,
before/after transcripts, and an honest write-up in `docs/VALIDATION-phase6.md` including
**anything that did not work**.

**Exit criteria** — you review the report and confirm the idea behaves as intended. If it
does not, we iterate here rather than proceeding.

---

### Act II — production hardening + persistent intelligence
*(starts only after your Phase 6 sign-off; structure follows the ACT II memory plan)*

> **Reordered 2026-08-11 at the user's request.** The benchmark was Phase 12, behind
> hardening, adapters and the entire memory system. It is now **Phase 7 — next** — because
> the question it answers ("is this useful, or over-engineering?") should be settled before
> more is built on top, not after. Every later phase then gets a before/after number
> instead of an opinion.

### Phase 7 — AgentGuard-Bench  ⟵ **NEXT**  (SPEC §36, §37)

**The benchmark has to be able to say the project is worthless.** A design that cannot
produce that result is not a measurement, it is a demonstration — and I have already built
the demonstration in Phase 6. So the rules are set before any number is generated:

**Pre-registered, before running anything**
- Success criteria written down first. If AgentGuard does not beat the control arm on
  hallucinated references *and* false completions, the honest conclusion is that the
  evidence engine and the completion gate are not earning their cost.
- A **stated kill condition**: if the control arm performs as well within noise on the
  metrics AgentGuard exists to move, that goes in the README, and the phases built on the
  assumption get reconsidered — including reverting them.

**Method**
- Two arms, identical in every respect except `AGENTGUARD_DISABLE`: same prompts, same
  model, same repository state (`git reset --hard` between runs), same permission mode.
- **n ≥ 3 runs per task per arm.** Agents are stochastic; a single run of each proves
  nothing, and reporting one would be dishonest.
- Tasks seeded across the SPEC §36 domains, each with a **deterministic** oracle: does the
  named symbol exist, did unrelated files change, does the suite pass, did the agent claim
  completion it had not earned. **Scored by a script, never by reading transcripts** — I
  built the thing being measured and cannot be the judge of it.

**Metrics** (SPEC §37) — hallucinated references · unsupported assumptions · files modified
· unnecessary files modified · false completion rate · requirement coverage · tool calls ·
tokens · wall-clock latency overhead.

**Known weaknesses, stated up front rather than discovered later**
- Small n gives wide confidence intervals. The report will show per-run results, not just
  means, so the spread is visible.
- I designed the tasks, and could bias them toward what AgentGuard happens to catch. Half
  the corpus is therefore drawn from failures observed in real sessions rather than
  invented, and the task list ships in the repo for anyone to disagree with.
- Two arms cannot separate "AgentGuard helped" from "the model had a good day". Only n and
  variance reporting mitigate that; nothing eliminates it.

**Cost:** roughly `tasks × 2 arms × 3 runs` live agent sessions. A 12-task corpus is ~72
sessions of real usage. That is the price of an actual answer.

**Exit criteria** — a table of measured numbers with variance, an honest verdict, and the
README's claims rewritten to match. If the verdict is "no measurable benefit", that is the
result and it gets published.

| Phase | Content | Source |
|---|---|---|
| **8 — Performance & reliability hardening** | Engineering Policy Packs (full YAML set), aggressive caching, decision logs, perf instrumentation, hook-input fuzzing, concurrency, huge-repo/monorepo scale, security review of the daemon | SPEC §11, §30, §39, §41 |
| **9 — Agent interoperability** | MCP server (`inspect_repository`, `find_symbol`, `find_evidence`, `check_complexity`, `validate_action`, `get_domain_policy`, `verify_requirement`, `run_verification`), Cursor adapter, Codex adapter | SPEC §24, §25 |
| **10 — Persistent project memory foundation** | Memory promotion (session → validated knowledge, *not* every response), the seven high-value memory types, retention tiers, project isolation at the memory layer, archival export | Memory plan §3–§5, §9 · ACT II "memory lifecycle" |
| **11 — Local semantic memory** | `EmbeddingProvider` abstraction (optional extra, never a core dependency), sqlite-vec, FTS5, hybrid retrieval, non-LLM reranking (similarity + keyword + project + freshness + confidence + source relevance) | ACT II "hybrid search", "reranking" |
| **12 — Memory validation & intelligence** | Staleness detection, evidence re-verification, memory confidence, contradiction detection, context injection — plus **incremental revalidation**: file changes invalidate only the memories that cite them, in background | ACT II "memory confidence", "updating should be incremental" |
| **13 — Production release** | Re-run Phase 7's benchmark with the memory arm added (agent · +AgentGuard · +memory), extended with retrieval precision and stale-memory rate; then PyPI, versioned release, `uv tool install`, docs site | SPEC §36, §37 · ACT II "research opportunity" |

Deferred to a Phase 14 if wanted: Copilot / OpenHands / ACP adapters, IDE dashboard,
browser-extension experiments (SPEC §43–§44).

**Explicitly not adopted** (ACT II plan, "What I would NOT do"): Pinecone, Weaviate,
Milvus, a Qdrant server, or Postgres+pgvector. Each adds a network dependency, a service
to deploy and a failure mode, in exchange for capability sqlite-vec already provides
locally.

**The Phase 12 question is the interesting one.** Benchmarking all three arms answers
*"does persistent semantic memory actually improve coding-agent reliability?"* — a real
result either way, and a far better claim than "AgentGuard uses a vector database". It is
also the evaluation SPEC §31 demands before embeddings are allowed to stay: if memory does
not measurably help, Phases 10–11 get reverted rather than shipped.

Note on "deployment": AgentGuard is a **local-first developer tool** (§30 — offline, no
network calls). "Production ready + deployed" therefore means Phase 12's installable
release + docs, not a cloud service. If you want a hosted component (team dashboards,
shared benchmarks), say so and I will add it as a separate phase.

---

## 3. Working agreement

- One phase at a time. Each ends with tests green and `PROGRESS.md` updated.
- Every phase's spec-conformance tests stay green forever (they are the regression net).
- If the SPEC and reality conflict, I stop and tell you rather than silently reinterpreting.
- Honest reporting: failures reported as failures, with output.

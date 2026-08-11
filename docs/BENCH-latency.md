# Latency bench — transport choice

**Date:** 2026-08-11 · **Phase:** 0 · **Machine:** macOS (Darwin 25.3.0), x86_64, Python 3.12.13
**Reproduce:** `uv run pytest tests/spec/test_s08_latency_budget.py -s`

SPEC §8 sets the constraint this bench exists to answer:

> Deterministic check: < 100 ms target
> The user should not feel that AgentGuard is slowing down the coding agent.

## Result

| Path | p50 | p95 | p99 | max | verdict |
|---|---|---|---|---|---|
| L0 read-only, in-process | 0.17 ms | 1.17 ms | 2.91 ms | 88.6 ms | ✅ |
| L0 mutating, in-process | 0.27 ms | 2.10 ms | 4.33 ms | 7.3 ms | ✅ |
| L1 user prompt, in-process | 0.20 ms | 0.26 ms | 0.33 ms | 4.1 ms | ✅ |
| **End-to-end over HTTP hook** (kept-alive connection) | **0.81 ms** | **0.98 ms** | **1.40 ms** | 6.4 ms | ✅ **~100× under budget** |
| **End-to-end through the installed config** (fresh TCP connection per call) | **5.73 ms** | **9.09 ms** | — | — | ✅ ~11× under budget |
| Command shim (fallback) | 70.5 ms | 113.6 ms | 135.6 ms | 141.1 ms | ❌ over budget before doing any work |

The two HTTP rows bracket the real cost. The first reuses one connection; the second opens
a fresh TCP connection for every call, which is the pessimistic assumption about how Claude
Code drives `http` hooks. Even pessimistically the hot path is ~9 ms at p95, leaving ~90 ms
of headroom for the engines built in Phases 1–4.

## What this decides

**`http` hooks are the right transport, and the margin is not close.**

A `command` hook spawns a Python interpreter for every single tool call. At **p50 70 ms /
p95 114 ms** that consumes the entire §8 budget *before AgentGuard has looked at anything*
— there would be no room left for evidence lookups, and every check added in Phases 1–4
would push it further over.

The `http` hook path costs **0.81 ms p50**, because Claude Code makes the request itself
and the daemon is already warm. That leaves ~99 ms of headroom for the actual intelligence.

This is why the installed configuration is split:

* `SessionStart` → **command** hook (`--ensure-daemon`). Once per session, so ~70 ms is
  invisible, and it guarantees the daemon is warm before the hot path starts.
* everything else → **http** hook straight to the daemon.

The shim remains as a fallback for environments without `http` hook support, with its cost
understood and documented rather than discovered later.

## Known issue: the 88 ms outlier

The read-only run shows p95 = 1.17 ms but max = 88.6 ms — a single stall across 220
iterations. The likely cause is a SQLite WAL auto-checkpoint: each event currently commits
twice (event row, decision row), so a few hundred tool calls accumulate enough WAL pages to
trigger one.

It is inside budget at p95 and p99, so it is recorded rather than fixed here — fixing it
now would be exactly the kind of unnecessary complexity this project exists to prevent. One
redundant third commit (a duplicate latency metric already held in memory) was removed. If
the tail grows as real checks land, the fix is a batched write queue, deferred to Phase 10.

## Re-run this when

* any phase adds work to `Guard.handle()`
* the repository index starts being consulted on the hot path (Phase 1)
* before the Phase 6 validation, on the real repository being used

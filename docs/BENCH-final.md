# Final Reliability Snapshot

Measured on 2026-08-22 with Python 3.12, the local `.venv`, and the repository's official
benchmark tests. These numbers describe the current implementation, not a guarantee across
all machines.

## Latency

| Path | p50 | p95 | p99 | Result |
|---|---:|---:|---:|---|
| Read-only Guard event, in process | 0.20 ms | 0.26 ms | 0.50 ms | 385x under the 100 ms target |
| Mutating Guard event, in process | 0.59 ms | 0.67 ms | 0.81 ms | 149x under target |
| Prompt path over a real index | 2.98 ms | 3.07 ms | 3.15 ms | 163x under the 500 ms repository target |
| Indexed evidence check | 0.35 ms | 0.37 ms | 0.39 ms | 270x under the 100 ms target |
| Installed HTTP hook, end to end | 1.44 ms | 1.60 ms | 1.87 ms | 62x under target |
| Command shim fallback | 62.07 ms | 65.84 ms | 66.43 ms | within target, but much slower |

Command shim startup is why the normal installation uses HTTP hooks for per-tool events and
uses the shim only for the once-per-session daemon health/startup hooks.

Reproduce:

```bash
AGENTGUARD_HOME=/tmp/agentguard-bench .venv/bin/pytest -q tests/spec/test_s08_latency_budget.py -s
```

## Evidence Detection

The mutation benchmark seeded 400 references to symbols that do not exist in this repository.
AgentGuard flagged 392 of them, for **98.0% recall**, and produced **0 false positives** on
the unmodified files, for **100% precision**.

Reproduce:

```bash
AGENTGUARD_HOME=/tmp/agentguard-bench .venv/bin/python -m agentguard.cli.main bench \
  --repo . --settings .claude/settings.json --task bait-asserted-method -n 1
```

For the direct detector benchmark used for this snapshot:

```bash
AGENTGUARD_HOME=/tmp/agentguard-bench PYTHONPATH=src \
  .venv/bin/python - <<'PY'
from agentguard.bench.mutation import run, render
from pathlib import Path

print(render(run(Path.cwd(), limit=400)))
PY
```

## What This Proves

AgentGuard is fast enough for a hot hook path, and its repository-grounded evidence detector
is effective on the class of hallucinations it is designed to inspect without crying wolf on
clean code.

It does **not** prove that every real coding-agent session improves by the same percentage.
That requires paired live-agent control and guarded runs with the same model, prompt, and
starting repository. The shipped `agentguard bench` harness exists for that measurement.

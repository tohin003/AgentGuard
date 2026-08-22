# AgentGuard

**A reliability and reasoning control layer for AI coding agents.**

AgentGuard attaches to an existing coding agent — Claude Code today, Cursor and Codex next —
and makes that agent's mistakes hard to execute. It does not replace the agent, and it does
not contain an LLM of its own.

> The host provides intelligence. AgentGuard provides discipline.

## What it does

| | |
|---|---|
| **Grounds claims in evidence** | The agent says it will call `UserRepository.get_active_users()`. AgentGuard asks the repository whether that exists. If it doesn't, the agent is challenged with the file as evidence. |
| **Sizes planning to the task** | "Rename `getUser`" stays three steps. "Introduce distributed session caching" is *allowed* to become a deep architectural investigation. AgentGuard is a proportionality system, not an anti-complexity system. |
| **Watches scope** | "Fix login validation" that starts editing 17 unrelated files gets challenged. |
| **Refuses to rubber-stamp "Done"** | Completion is a claim like any other, and has to be backed by tests that actually ran. |
| **Stays quiet** | Deterministic checks on the hot path, sub-100ms, no output. Like ABS: invisible until it isn't. |

## What it is not

It has no LLM. When a judgement needs real reasoning, AgentGuard doesn't think — it hands
the problem back to the host agent as a challenge, and the host's own intelligence resolves
it. That is the entire architecture: **bring your own brain**.

It also never grants permissions. It can stay silent, object, or escalate to you — but it
will not approve something on your behalf.

## Status

The Claude Code integration is usable today, with deterministic evidence checks, proportional
planning, scope validation, verified completion, local telemetry, and hardened fail-open daemon
lifecycle. Current measured performance and detector coverage are in
[`docs/BENCH-final.md`](docs/BENCH-final.md). See [`PROGRESS.md`](PROGRESS.md) for phase detail.

## Install (development)

### Requirements

- Python 3.12 or newer
- Git (used for repository evidence when the project is a Git checkout)
- Claude Code with hooks enabled
- `uv` is recommended for installation, but the package can also be installed with any
  Python packaging workflow that supports editable installs

Put `agentguard` on your PATH. `--editable` means it tracks this source tree, so changes
take effect without reinstalling:

```bash
uv tool install --editable /path/to/AgentGuard
```

Then, **from the repository you want guarded**:

```bash
cd /path/to/your-project
agentguard install claude --project   # -> ./.claude/settings.local.json (gitignored)
agentguard daemon start               # optional: SessionStart does this too
agentguard doctor                     # confirm everything is wired
```

`--project` attaches AgentGuard to that repository only. Use `--global` instead to attach
to every project, which writes to `~/.claude/settings.json`.

Running it as `uv run agentguard …` only works from inside this repository, because that
form uses the project's own virtualenv.

### First Use

1. Install the hooks from the repository you want to protect.
2. Run `agentguard doctor` and fix any reported configuration or port conflict.
3. Start Claude Code in that repository. `SessionStart` starts the local daemon when needed;
   `agentguard daemon start` is useful when you want to start it before opening Claude.
4. Work normally. AgentGuard is silent for ordinary safe events and returns a challenge,
   review request, narrowed input, or completion-gate hold only when its deterministic checks
   find something worth surfacing.

For a global installation, replace `--project` with `--global`. Project installation writes
`.claude/settings.local.json`; global installation writes `~/.claude/settings.json`. The
installer is idempotent and removes only hooks marked as AgentGuard-owned.

AgentGuard's daemon is deliberately bound to IPv4 loopback (`127.0.0.1`) and authenticates
each hook with a local bearer token. Its database, token, handshake, lifecycle markers and
logs are kept under `AGENTGUARD_HOME` (default `~/.agentguard`) with owner-only permissions.
The daemon does not accept remote connections. If the config file is malformed or contains
an unsafe value, hooks use safe defaults and `agentguard doctor` reports the configuration
problem so it can be corrected rather than silently running with an unexpected setup.

### Turning it off

```bash
agentguard off                  # stays installed, decides nothing
agentguard on
AGENTGUARD_DISABLE=1 claude     # off for one session
agentguard uninstall claude --project   # remove the hooks entirely
```

`uninstall` removes only what AgentGuard added; any hooks you wrote yourself are left
alone.

`off` and `on` change the persisted setting and take effect after the daemon is restarted.
For a one-session switch, use `AGENTGUARD_DISABLE=1 claude`. To remove the integration
entirely, run `agentguard uninstall claude --project` (or `--global`).

### Daily Commands

```bash
agentguard doctor                 # installation, daemon, storage, and port checks
agentguard daemon status          # whether the authenticated daemon is answering
agentguard daemon start           # start it in the background
agentguard daemon stop            # stop it safely
agentguard log -n 20              # recent decisions and latency
agentguard why <decision-id>      # findings and evidence behind one decision
agentguard report -d 7            # project observations over the last week
agentguard db stats               # database size, retention, and disk state
```

Repository inspection is available without starting Claude:

```bash
agentguard index                  # build and summarize the repository index
agentguard find-symbol UserRepository
agentguard explain src/app.py
```

`agentguard doctor` is the first diagnostic to run. It checks Python/Git, safe daemon
configuration, private storage, hook installation, and whether the loopback port is occupied
by another process. The daemon listens only on `127.0.0.1` and authenticates hook requests
with a local bearer token.

### Seeing what it did

```bash
agentguard log            # recent decisions
agentguard why <id>       # the evidence behind one decision
agentguard db stats       # what is stored, and how much room is left
```

### Observe-only, and the failure-mode census

AgentGuard can run as a pure sensor: every check still runs and every finding is still
recorded, but nothing reaches the agent — no challenges, no completion gate, not even the
injected planning budget.

```bash
agentguard observe on     # record everything, say nothing
# ... a week of ordinary work ...
agentguard census         # which of SPEC §3's 17 failure modes actually occurred
agentguard observe off
```

This exists because the benchmark ([`docs/BENCH-mutation.md`](docs/BENCH-mutation.md))
found that the failure AgentGuard was best at detecting — hallucinated references — is one
current models essentially no longer commit. Rather than guess again at which failure to
target, the census counts. **While observe-only is on, AgentGuard guards nothing**; that is
the price of measuring an unguarded agent. Method and caveats:
[`docs/CENSUS.md`](docs/CENSUS.md).

### Configuration And Data

AgentGuard stores its shared SQLite database, token, handshake, logs, and lifecycle files
under `AGENTGUARD_HOME` (default `~/.agentguard`). Files are created with owner-only
permissions. Set `AGENTGUARD_HOME` to an isolated directory for tests or a separate local
profile. `AGENTGUARD_DISABLE=1` disables all decisions for one process; `AGENTGUARD_OBSERVE=1`
runs the engines but suppresses everything sent back to the agent.

The daemon defaults to `127.0.0.1:8787`, which is embedded in the installed hook URL. If that
port is occupied, set a different loopback port in `~/.agentguard/config.toml` and reinstall
the hooks so the URL and daemon agree:

```toml
[daemon]
host = "127.0.0.1"
port = 8790
```

Do not bind the daemon to a network interface. AgentGuard is designed as a local control
layer, not a remotely exposed service.

### What It Can And Cannot Prove

AgentGuard can ground repository claims, challenge risky or out-of-scope actions, and stop a
false completion claim when the available evidence is insufficient. It does not contain an
LLM and does not replace the host agent's reasoning. Unknown or unsupported evidence is
treated conservatively as unknown, not as proof of failure.

The current final measurements are in [`docs/BENCH-final.md`](docs/BENCH-final.md): the
installed HTTP hook measured 1.60 ms p95, and the repository mutation benchmark reached
98.0% recall with 100% precision on 400 seeded nonexistent references. These are detector and
latency measurements, not a causal guarantee for every real agent session. A paired live
control/guarded run is required to measure outcome improvement.

### Development

From this repository:

```bash
uv sync
.venv/bin/pytest -q
.venv/bin/ruff check src tests
.venv/bin/python -m compileall -q src tests
```

The project intentionally has no LLM/provider SDK dependency. Claude Code is the current
adapter; Cursor and Codex adapters are planned separately.

## License

Apache-2.0

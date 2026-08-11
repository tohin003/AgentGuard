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

Early development. See [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for the phased
build and [`PROGRESS.md`](PROGRESS.md) for what actually works today.

## Install (development)

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

### Turning it off

```bash
agentguard off                  # stays installed, decides nothing
agentguard on
AGENTGUARD_DISABLE=1 claude     # off for one session
agentguard uninstall claude --project   # remove the hooks entirely
```

`uninstall` removes only what AgentGuard added; any hooks you wrote yourself are left
alone.

### Seeing what it did

```bash
agentguard log            # recent decisions
agentguard why <id>       # the evidence behind one decision
agentguard db stats       # what is stored, and how much room is left
```

## License

Apache-2.0

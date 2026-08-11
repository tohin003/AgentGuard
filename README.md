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

```bash
uv sync --all-extras
uv run agentguard install claude    # writes hooks into ~/.claude/settings.json
uv run agentguard daemon start
uv run agentguard doctor
```

Detach at any time with `agentguard uninstall claude`, or disable without uninstalling by
setting `AGENTGUARD_DISABLE=1`.

## License

Apache-2.0

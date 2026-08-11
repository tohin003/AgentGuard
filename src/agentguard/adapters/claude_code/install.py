"""Install/uninstall AgentGuard's hooks in Claude Code settings (SPEC §26).

Transport split, and why
------------------------
* ``SessionStart`` is a **command** hook running the shim with ``--ensure-daemon``. It
  fires once per session, so ~50ms of process startup is invisible, and it guarantees the
  daemon is warm before any hot-path hook fires.
* Everything else is an **http** hook straight to the daemon. Claude Code performs the
  request itself, so per-tool-call cost is a localhost round trip instead of a Python
  interpreter start — the difference between meeting and missing SPEC §8.

``PreToolUse``/``PostToolUse`` are matched to mutating tools only. Read/Grep/Glob would
short-circuit to ALLOW anyway, so intercepting them would be pure latency for no signal.

Idempotent: installing twice leaves one copy; uninstalling removes exactly what was added
and nothing a human put there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agentguard.core.config import Settings, get_or_create_token

MARKER = "agentguard"

# Tools that can change the workspace. SPEC §18 only cares about these.
MUTATING_MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash"

# Hook timeouts (seconds). Deliberately tight: a wedged daemon must never hold the
# developer hostage, and the fail-open path is cheap.
TIMEOUTS = {
    "SessionStart": 20,
    "UserPromptSubmit": 10,
    "PreToolUse": 5,
    "PostToolUse": 5,
    "Stop": 10,
    "SessionEnd": 3,
}


def global_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def project_settings_path(workspace: Path | None = None) -> Path:
    # settings.local.json is gitignored by Claude Code convention, which matters here
    # because the config carries a local auth token.
    return (workspace or Path.cwd()) / ".claude" / "settings.local.json"


def _http_hook(url: str, token: str, timeout: int) -> dict[str, Any]:
    return {
        "type": "http",
        "url": url,
        "headers": {
            "Authorization": f"Bearer {token}",
            # Doubles as the ownership marker for _is_ours() and as a label for any
            # human who opens settings.json and wonders what this entry is.
            "X-AgentGuard": "1",
        },
        "timeout": timeout,
    }


def build_hook_config(settings: Settings | None = None, python_exe: str | None = None) -> dict[str, Any]:
    settings = settings or Settings.load()
    token = get_or_create_token()
    url = f"{settings.daemon.base_url}/hook/claude-code"
    py = python_exe or sys.executable

    return {
        "SessionStart": [
            {
                "matcher": "startup|resume|clear",
                "hooks": [
                    {
                        "type": "command",
                        "command": py,
                        "args": ["-m", "agentguard.adapters.claude_code.shim", "--ensure-daemon"],
                        "timeout": TIMEOUTS["SessionStart"],
                        "statusMessage": "AgentGuard: warming repository index",
                    }
                ],
            }
        ],
        "UserPromptSubmit": [
            {"hooks": [_http_hook(url, token, TIMEOUTS["UserPromptSubmit"])]}
        ],
        "PreToolUse": [
            {"matcher": MUTATING_MATCHER, "hooks": [_http_hook(url, token, TIMEOUTS["PreToolUse"])]}
        ],
        "PostToolUse": [
            {"matcher": MUTATING_MATCHER, "hooks": [_http_hook(url, token, TIMEOUTS["PostToolUse"])]}
        ],
        "Stop": [{"hooks": [_http_hook(url, token, TIMEOUTS["Stop"])]}],
        "SessionEnd": [{"hooks": [_http_hook(url, token, TIMEOUTS["SessionEnd"])]}],
    }


def _is_ours(group: dict[str, Any]) -> bool:
    """Identify AgentGuard's own entries so uninstall never touches a human's hooks."""
    return any(MARKER in json.dumps(hook).lower() for hook in group.get("hooks", []))


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"{path} is not valid JSON; refusing to overwrite it ({exc})") from exc
    return data if isinstance(data, dict) else {}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".agentguard-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def merge_hooks(existing: dict[str, Any], new_hooks: dict[str, Any]) -> dict[str, Any]:
    """Add our groups, replacing any previous AgentGuard groups, preserving all others."""
    out = dict(existing)
    hooks: dict[str, Any] = dict(out.get("hooks") or {})

    for event, groups in new_hooks.items():
        current = [g for g in (hooks.get(event) or []) if not _is_ours(g)]
        hooks[event] = current + list(groups)

    out["hooks"] = hooks
    return out


def strip_hooks(existing: dict[str, Any]) -> dict[str, Any]:
    out = dict(existing)
    hooks: dict[str, Any] = dict(out.get("hooks") or {})
    for event in list(hooks):
        remaining = [g for g in (hooks.get(event) or []) if not _is_ours(g)]
        if remaining:
            hooks[event] = remaining
        else:
            hooks.pop(event)
    if hooks:
        out["hooks"] = hooks
    else:
        out.pop("hooks", None)
    return out


def install(
    path: Path, settings: Settings | None = None, dry_run: bool = False, python_exe: str | None = None
) -> tuple[dict[str, Any], bool]:
    """Returns (resulting settings, changed)."""
    existing = _load(path)
    merged = merge_hooks(existing, build_hook_config(settings, python_exe))
    changed = merged != existing
    if changed and not dry_run:
        _save(path, merged)
    return merged, changed


def uninstall(path: Path, dry_run: bool = False) -> tuple[dict[str, Any], bool]:
    existing = _load(path)
    stripped = strip_hooks(existing)
    changed = stripped != existing
    if changed and not dry_run:
        _save(path, stripped)
    return stripped, changed


def is_installed(path: Path) -> bool:
    try:
        hooks = (_load(path).get("hooks") or {}).values()
    except RuntimeError:
        return False
    return any(_is_ours(group) for groups in hooks for group in groups)

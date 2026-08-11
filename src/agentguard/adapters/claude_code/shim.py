"""Command-hook shim: stdin JSON -> daemon -> stdout JSON.

Two jobs:

1. **Fallback transport.** Where `http` hooks are unavailable, this runs as a `command`
   hook and forwards to the daemon. It pays Python interpreter startup (~30-80ms), which
   is why it is the fallback and not the default.
2. **`--ensure-daemon`.** Installed on `SessionStart` only — once per session, where a
   process spawn is invisible — so the daemon is guaranteed warm before the hot-path
   `http` hooks start firing.

Deliberately stdlib-only, with no import of `agentguard.core` or `agentguard.daemon`
(those pull in pydantic/fastapi and would triple startup time). The handshake-reading
code below is duplicated from `daemon/app.py` for exactly that reason.

Every failure path prints nothing and exits 0 — "no decision" — so a broken AgentGuard is
indistinguishable from an absent one (SPEC §39).
"""

from __future__ import annotations

import json
import os
import sys

DEFAULT_TIMEOUT_S = 2.0
DAEMON_START_TIMEOUT_S = 8.0


def _start_timeout() -> float:
    """How long to wait for a revived daemon.

    Configurable because the right answer differs by machine: a cold start on a slow
    filesystem takes longer than the default, and a developer who would rather fail fast
    than wait can say so.
    """
    raw = os.environ.get("AGENTGUARD_START_TIMEOUT", "")
    try:
        value = float(raw)
    except ValueError:
        return DAEMON_START_TIMEOUT_S
    return value if value > 0 else DAEMON_START_TIMEOUT_S


def _home() -> str:
    return os.path.expanduser(os.environ.get("AGENTGUARD_HOME") or "~/.agentguard")


def _handshake() -> dict | None:
    path = os.path.join(_home(), "daemon.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _alive(hs: dict | None) -> bool:
    if not hs:
        return False
    pid = hs.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _post(hs: dict, payload: dict, timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    import http.client

    body = json.dumps(payload).encode("utf-8")
    conn = http.client.HTTPConnection(hs.get("host", "127.0.0.1"), int(hs["port"]), timeout=timeout)
    try:
        conn.request(
            "POST",
            "/hook/claude-code",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {hs.get('token', '')}",
            },
        )
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status != 200 or not raw:
            return {}
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    finally:
        conn.close()


def ensure_daemon(timeout: float | None = None) -> dict | None:
    """Start the daemon if it is not already running. Returns the handshake, or None."""
    import time

    timeout = _start_timeout() if timeout is None else timeout

    hs = _handshake()
    if _alive(hs):
        return hs

    import subprocess

    log_dir = _home()
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_file = open(os.path.join(log_dir, "daemon.log"), "ab")  # noqa: SIM115
    except OSError:
        log_file = subprocess.DEVNULL

    try:
        subprocess.Popen(
            [sys.executable, "-m", "agentguard.daemon", "run"],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # survives the hook process exiting
        )
    except OSError:
        return None

    deadline = time.time() + timeout
    while time.time() < deadline:
        hs = _handshake()
        if _alive(hs):
            return hs
        time.sleep(0.05)
    return None


HEALTH_NOTICE = (
    "AgentGuard is not running — this session is UNGUARDED. "
    "Restart it with `agentguard daemon start`, or detach with "
    "`agentguard uninstall claude`. Your work continues either way."
)


def _notice_marker(session_id: str) -> str:
    safe = "".join(c for c in (session_id or "unknown") if c.isalnum() or c in "-_")[:64]
    return os.path.join(_home(), f"notified-{safe}")


def _already_notified(session_id: str) -> bool:
    """Warn once per session.

    A true warning repeated on every prompt is still nagging, and nagging is what gets
    AgentGuard uninstalled (SPEC §39).
    """
    marker = _notice_marker(session_id)
    if os.path.exists(marker):
        return True
    try:
        os.makedirs(_home(), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass
    return False


def health_check(payload: dict) -> int:
    """Detect, re-verify, and tell the developer if AgentGuard is down.

    Silent fail-open is worse than visible fail-open: a developer who believes they are
    guarded, and is not, is in a worse position than one who knows. So this attempts one
    revival and, if that fails, writes to stderr and exits non-zero-non-2 — which Claude
    Code surfaces in the transcript as a hook error and treats as **non-blocking**.

    The developer sees it. The work continues. Whether to carry on unguarded is their call.
    """
    if _alive(ensure_daemon()):
        return 0

    if _already_notified(payload.get("session_id", "")):
        return 0

    sys.stderr.write(HEALTH_NOTICE + "\n")
    return 1  # non-zero, non-2: reported to the developer, blocks nothing


def main() -> int:
    args = set(sys.argv[1:])
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        return 0

    if not isinstance(payload, dict):
        return 0

    if os.environ.get("AGENTGUARD_DISABLE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return 0  # deliberately off: say nothing

    if "--health" in args:
        return health_check(payload)

    hs = ensure_daemon() if "--ensure-daemon" in args else _handshake()
    if not _alive(hs):
        return 0  # fail open

    try:
        out = _post(hs, payload)
    except Exception:  # noqa: BLE001 - fail open: any transport failure means "no decision"
        return 0

    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

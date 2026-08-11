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


def ensure_daemon(timeout: float = DAEMON_START_TIMEOUT_S) -> dict | None:
    """Start the daemon if it is not already running. Returns the handshake, or None."""
    import time

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
        return 0

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

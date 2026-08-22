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

Transport failures return no decision and exit 0. The one intentional exception is an
unrecoverable SessionStart health check, which emits a once-per-session warning so the
developer knows the session is running unguarded.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import math
import os
import sys

DEFAULT_TIMEOUT_S = 2.0
DAEMON_START_TIMEOUT_S = 8.0
# A localhost round trip is sub-millisecond; this only has to bound the pathological case.
HEALTH_TIMEOUT_S = 1.0
MAX_STDIN_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 64_000


def _endpoint(hs: dict) -> tuple[str, int] | None:
    host = hs.get("host", "127.0.0.1")
    if isinstance(host, str) and host.strip().lower() == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(str(host).strip())
        port = int(hs["port"])
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(address, ipaddress.IPv4Address) or not address.is_loopback:
        return None
    if not 0 < port <= 65535:
        return None
    return str(address), port


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
    return min(value, 60.0) if math.isfinite(value) and value > 0 else DAEMON_START_TIMEOUT_S


def _home() -> str:
    return os.path.expanduser(os.environ.get("AGENTGUARD_HOME") or "~/.agentguard")


def _handshake() -> dict | None:
    path = os.path.join(_home(), "daemon.json")
    try:
        if os.path.islink(path):
            return None
        with open(path, encoding="utf-8") as fh:
            raw = fh.read(MAX_RESPONSE_BYTES)
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _alive(hs: dict | None) -> bool:
    """Whether the daemon named in this handshake is actually answering.

    A process-existence check alone is not enough, and the gap it leaves is the reboot
    case. `daemon.json` survives a restart while the process does not, and operating
    systems recycle PIDs — so a stale handshake can name a live, entirely unrelated
    process. `os.kill(pid, 0)` then succeeds, the shim concludes the daemon is up, skips
    reviving it, and every hook for that whole session fails open against a port nothing
    is listening on. Silently: fail-open is working exactly as designed, and the developer
    has no way to tell an unguarded session from a guarded one.

    That is the invisible non-guarding plan D9 exists to prevent, reached through the back
    door — so liveness means *reachable*, not merely *some process holds that number*.
    The PID check stays as a free pre-filter for the common case where the daemon is
    simply gone.
    """
    if not hs:
        return False
    pid = hs.get("pid")
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, OverflowError, ValueError):
        return False
    return _answers_health(hs, pid)


def _answers_health(hs: dict, pid: int) -> bool:
    """Ask the endpoint who it is.

    Matching the reported pid against the handshake's does double duty: it proves the
    daemon is listening, and it proves the thing on that port is *our* daemon rather than
    whatever else may have claimed a fixed port (8787) since.
    """
    import http.client

    endpoint = _endpoint(hs)
    if endpoint is None:
        return False
    try:
        conn = http.client.HTTPConnection(*endpoint, timeout=HEALTH_TIMEOUT_S)
    except (TypeError, ValueError):
        return False
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        if response.status != 200:
            return False
        body = json.loads(response.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace"))
        return isinstance(body, dict) and body.get("pid") == pid
    except Exception:  # noqa: BLE001 - unreachable, wrong service, garbage body: all "no"
        return False
    finally:
        import contextlib

        with contextlib.suppress(OSError):
            conn.close()


def _post(hs: dict, payload: dict, timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    import http.client

    body = json.dumps(payload).encode("utf-8")
    endpoint = _endpoint(hs)
    if endpoint is None:
        return {}
    conn = http.client.HTTPConnection(*endpoint, timeout=timeout)
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
        raw = resp.read(MAX_RESPONSE_BYTES)
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
    # SessionStart can run concurrently for multiple host sessions. Claiming a small
    # inter-process startup lock makes the check/start/wait sequence one operation: the
    # second hook waits for the first daemon to publish its handshake, then reuses it
    # instead of launching a competing process that could clobber credentials.
    try:
        from agentguard.daemon.lifecycle import interprocess_lock, open_private_append, startup_lock_path

        startup_guard = interprocess_lock(startup_lock_path(_home()), timeout=timeout)
    except Exception:  # noqa: BLE001 - shim must remain dependency/failure tolerant
        startup_guard = None

    if startup_guard is None:
        return None

    with startup_guard as locked:
        if not locked:
            hs = _handshake()
            return hs if _alive(hs) else None

        # Another process may have completed startup while we were acquiring the lock.
        hs = _handshake()
        if _alive(hs):
            return hs

        log_dir = _home()
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_file = open_private_append(os.path.join(log_dir, "daemon.log"))
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
        finally:
            if log_file is not subprocess.DEVNULL:
                with contextlib.suppress(OSError):
                    log_file.close()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
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
        from agentguard.daemon.lifecycle import create_private_marker

        return not create_private_marker(marker, str(os.getpid()))
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
        raw = sys.stdin.read(MAX_STDIN_BYTES + 1)
        if len(raw.encode("utf-8", "surrogatepass")) > MAX_STDIN_BYTES:
            return 0
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

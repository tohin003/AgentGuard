"""Local AgentGuard daemon (SPEC §30).

    Developer -> Coding Agent -> Local AgentGuard daemon -> Filesystem / Git / AST / tests

Why a daemon at all: the repository index, symbol maps and caches are expensive to build
and cheap to keep. A per-tool-call subprocess would rebuild or reload them constantly and
blow the SPEC §8 latency budget. The daemon holds them warm.

Why HTTP: Claude Code supports `{"type": "http"}` hooks, which it calls itself. That
removes Python interpreter startup (30-80ms) from every single tool call. Binding is
127.0.0.1-only and every request carries a bearer token from a 0600 handshake file.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import os
import secrets
import signal
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from agentguard.adapters.claude_code import translate as claude
from agentguard.core.config import (
    DaemonSettings,
    Settings,
    agentguard_home,
    daemon_handshake_path,
    ensure_private_dir,
    get_or_create_token,
    write_private_text,
)
from agentguard.core.engine import Guard
from agentguard.core.metrics import METRICS
from agentguard.daemon.lifecycle import handshake_lock_path, interprocess_lock

log = logging.getLogger(__name__)

ADAPTERS = {"claude-code": claude}
MAX_HOOK_BODY_BYTES = 1_000_000
MAX_HEALTH_BODY_BYTES = 64_000


def _safe_endpoint(host: Any, port: Any) -> tuple[str, int] | None:
    """Accept only the daemon's loopback IPv4 endpoint from a handshake file."""
    if not isinstance(host, str) or host.strip().lower() == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(str(host).strip())
        port_number = int(port)
    except (ValueError, TypeError):
        return None
    if not isinstance(address, ipaddress.IPv4Address) or not address.is_loopback:
        return None
    if not 0 < port_number <= 65535:
        return None
    return str(address), port_number


def write_handshake(host: str, port: int, token: str) -> Path:
    """Publish how to reach the daemon. 0600: the token is a local credential."""
    ensure_private_dir()
    path = daemon_handshake_path()
    payload = {
        "host": host,
        "port": port,
        "token": token,
        "pid": os.getpid(),
        "started_at": time.time(),
        "version": 1,
    }
    # Serialize publication with shutdown cleanup. Without this, an older daemon can
    # finish shutting down after a replacement has published its handshake and unlink
    # the replacement's credentials.
    with interprocess_lock(handshake_lock_path(agentguard_home()), timeout=2.0) as locked:
        if not locked:
            raise RuntimeError("could not claim the daemon handshake lock")
        write_private_text(path, json.dumps(payload, indent=2))
    return path


def read_handshake() -> dict[str, Any] | None:
    try:
        # Validate the full home path before reading credentials.  A symlinked parent
        # can otherwise redirect a seemingly safe daemon.json to another directory.
        ensure_private_dir()
    except (OSError, RuntimeError):
        return None
    path = daemon_handshake_path()
    if path.is_symlink():
        return None
    if not path.exists():
        return None
    try:
        if path.stat().st_size > MAX_HEALTH_BODY_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def clear_handshake(expected_pid: int | None = None, expected_token: str | None = None) -> None:
    """Remove the handshake, optionally only when it still belongs to this daemon.

    Shutdown is racy by nature: a supervisor may start a replacement before the old
    process has finished unwinding.  Matching both PID and token prevents the old
    process from deleting the replacement's handshake.  The lock closes the remaining
    read/replace/unlink window with :func:`write_handshake`.
    """
    path = daemon_handshake_path()
    with interprocess_lock(handshake_lock_path(agentguard_home()), timeout=2.0) as locked:
        if not locked:
            return
        if expected_pid is not None or expected_token is not None:
            current = read_handshake()
            if not current:
                return
            if expected_pid is not None and current.get("pid") != expected_pid:
                return
            if expected_token is not None and current.get("token") != expected_token:
                return
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def daemon_is_alive() -> bool:
    """Whether the daemon in the handshake is actually answering.

    Not a PID check. `daemon.json` outlives a reboot while the process does not, and
    operating systems recycle PIDs, so a stale handshake can name a live but unrelated
    process. Two things then go wrong, and the second is the serious one:

    * `agentguard daemon start` reports "already running" and starts nothing, leaving
      every hook to fail open in silence;
    * `agentguard daemon stop` sends **SIGTERM to that unrelated process**.

    So liveness is defined as "the endpoint answers `/health` and reports the pid we
    expect" — which also rules out something else having claimed the fixed port.
    """
    hs = read_handshake()
    if not hs:
        return False
    pid = hs.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)  # free pre-filter for the common case: the process is simply gone
    except (OSError, ProcessLookupError):
        return False
    return _health_reports(hs, pid)


def _health_reports(hs: dict[str, Any], pid: int) -> bool:
    """`GET /health` on the handshake's endpoint, via stdlib.

    `httpx` is a development dependency, not a runtime one, and this runs inside the CLI.
    """
    import http.client

    endpoint = _safe_endpoint(hs.get("host", "127.0.0.1"), hs.get("port"))
    if endpoint is None:
        return False
    try:
        conn = http.client.HTTPConnection(*endpoint, timeout=1.0)
    except (TypeError, ValueError):
        return False
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        if response.status != 200:
            return False
        body = json.loads(response.read(MAX_HEALTH_BODY_BYTES).decode("utf-8", "replace"))
        return isinstance(body, dict) and body.get("pid") == pid
    except Exception:  # noqa: BLE001 - unreachable or not ours: either way, not alive
        return False
    finally:
        with contextlib.suppress(OSError):
            conn.close()


def create_app(token: str, settings: Settings | None = None, guard: Guard | None = None) -> FastAPI:
    app = FastAPI(title="AgentGuard", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.token = token
    app.state.guard = guard or Guard(settings)
    app.state.started_at = time.time()

    def _check_auth(authorization: str | None) -> None:
        expected = f"Bearer {app.state.token}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "pid": os.getpid(),
            "uptime_s": round(time.time() - app.state.started_at, 3),
            "enabled": app.state.guard.settings.enabled,
            # The daemon reads config once, at startup. A developer who changed the mode
            # and forgot to restart has no other way to find out which one is live — and
            # the two differ in whether anything is being guarded at all.
            "observing": app.state.guard.settings.observe_only,
        }

    @app.get("/metrics")
    async def metrics(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _check_auth(authorization)
        return METRICS.snapshot()

    @app.post("/hook/{adapter_name}")
    async def hook(
        adapter_name: str, request: Request, authorization: str | None = Header(default=None)
    ) -> JSONResponse:
        """The hot path.

        Returns ``{}`` — "no opinion" — on every failure. A guard layer that 500s must
        still not impede the agent (SPEC §39).
        """
        try:
            _check_auth(authorization)
        except HTTPException:
            # Even an auth failure fails open: log it, decide nothing.
            log.warning("agentguard: rejected unauthenticated hook request")
            return JSONResponse({}, status_code=200)

        adapter = ADAPTERS.get(adapter_name)
        if adapter is None:
            return JSONResponse({}, status_code=200)

        try:
            content_length = request.headers.get("content-length")
            if content_length is not None and int(content_length) > MAX_HOOK_BODY_BYTES:
                return JSONResponse({}, status_code=200)
        except (TypeError, ValueError):
            return JSONResponse({}, status_code=200)

        try:
            chunks: list[bytes] = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_HOOK_BODY_BYTES:
                    return JSONResponse({}, status_code=200)
                chunks.append(chunk)
            payload = json.loads(b"".join(chunks))
        except Exception:  # noqa: BLE001 - fail open: unreadable body means "no decision"
            return JSONResponse({}, status_code=200)

        if not isinstance(payload, dict):
            return JSONResponse({}, status_code=200)

        try:
            event = adapter.to_event(payload)
            if event is None:
                return JSONResponse({}, status_code=200)
            # Guard.handle performs synchronous indexing, AST and SQLite work. Running it
            # directly in this async route would stall health checks and every other hook
            # whenever one repository refresh is slow. The Guard serializes state per
            # workspace, so moving it to a worker preserves ordering without blocking the
            # event loop.
            decision = await run_in_threadpool(app.state.guard.handle, event)
            out = adapter.from_decision(event, decision, payload.get("hook_event_name", ""))
            return JSONResponse(out, status_code=200)
        except Exception:
            log.exception("agentguard: hook handling failed; returning no-decision")
            return JSONResponse({}, status_code=200)

    @app.post("/shutdown")
    async def shutdown(authorization: str | None = Header(default=None)) -> dict[str, str]:
        _check_auth(authorization)
        os.kill(os.getpid(), signal.SIGTERM)
        return {"status": "stopping"}

    return app


def run(host: str | None = None, port: int | None = None, log_level: str = "warning") -> None:
    """Run the daemon in the foreground.

    Defaults come from settings (fixed port, so the installed hook URL stays valid).
    Pass ``port=0`` to grab an ephemeral port — used by tests running daemons in parallel.
    """
    import socket

    import uvicorn

    settings = Settings.load()
    host = host if host is not None else settings.daemon.host
    port = port if port is not None else settings.daemon.port
    try:
        # Port 0 is useful only for an explicitly foregrounded test process. It must not
        # be accepted from persisted Settings, because an installed hook cannot point at
        # an ephemeral port that changes on every restart.
        ephemeral = port == 0
        endpoint = DaemonSettings(host=host, port=1 if ephemeral else port)
    except Exception as exc:  # invalid config must fail before binding
        sys.stderr.write(f"agentguard: invalid daemon endpoint ({exc})\n")
        raise SystemExit(2) from exc
    host = endpoint.host
    if not ephemeral:
        port = endpoint.port
    token = get_or_create_token()

    # Bind exactly once and retain this socket until uvicorn owns it. A preliminary
    # check followed by close/bind has a check-then-use race: another process can claim
    # the port, and port=0 can be replaced by a different ephemeral port.
    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_socket.bind((host, port))
    except OSError as exc:
        with contextlib.suppress(OSError):
            listen_socket.close()
        sys.stderr.write(
            f"agentguard: cannot bind {host}:{port} ({exc}). "
            "Change [daemon].port in config.toml and re-run `agentguard install claude`.\n"
        )
        raise SystemExit(1) from exc

    port = int(listen_socket.getsockname()[1])
    app = create_app(token, settings)
    write_handshake(host, port, token)

    ensure_private_dir()
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level, access_log=False)
    server = uvicorn.Server(config)

    # uvicorn 0.52 does not unwind the stack on SIGTERM, so a `finally` alone never
    # runs and the handshake file is left behind. Asking the server to exit instead
    # gives a real graceful shutdown. (A stale handshake is still handled correctly
    # everywhere — see `daemon_is_alive()` — because SIGKILL and crashes exist.)
    def _graceful(signum: int, frame: object) -> None:
        server.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        # ValueError/OSError here means we are not in the main thread, in which case
        # uvicorn's own handling applies.
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, _graceful)

    try:
        # Keep the socket that was checked above open through uvicorn startup.  A
        # check-then-close-then-bind sequence leaves a small but real window in which
        # another process can claim the fixed port (or an ephemeral port can change).
        # Passing the already-bound socket makes the reservation atomic.
        server.run(sockets=[listen_socket])
    finally:
        with contextlib.suppress(OSError):
            listen_socket.close()
        clear_handshake(expected_pid=os.getpid(), expected_token=token)

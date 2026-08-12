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

from agentguard.adapters.claude_code import translate as claude
from agentguard.core.config import (
    Settings,
    agentguard_home,
    daemon_handshake_path,
    get_or_create_token,
)
from agentguard.core.engine import Guard
from agentguard.core.metrics import METRICS

log = logging.getLogger(__name__)

ADAPTERS = {"claude-code": claude}


def write_handshake(host: str, port: int, token: str) -> Path:
    """Publish how to reach the daemon. 0600: the token is a local credential."""
    path = daemon_handshake_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": host,
        "port": port,
        "token": token,
        "pid": os.getpid(),
        "started_at": time.time(),
        "version": 1,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def read_handshake() -> dict[str, Any] | None:
    path = daemon_handshake_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def clear_handshake() -> None:
    with contextlib.suppress(OSError):
        daemon_handshake_path().unlink(missing_ok=True)


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

    try:
        conn = http.client.HTTPConnection(
            str(hs.get("host", "127.0.0.1")), int(hs["port"]), timeout=1.0
        )
    except (KeyError, TypeError, ValueError):
        return False
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        if response.status != 200:
            return False
        body = json.loads(response.read().decode("utf-8", "replace"))
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
            payload = await request.json()
        except Exception:  # noqa: BLE001 - fail open: unreadable body means "no decision"
            return JSONResponse({}, status_code=200)

        if not isinstance(payload, dict):
            return JSONResponse({}, status_code=200)

        try:
            event = adapter.to_event(payload)
            if event is None:
                return JSONResponse({}, status_code=200)
            decision = app.state.guard.handle(event)
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
    token = get_or_create_token()

    if port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            port = s.getsockname()[1]
    else:
        # Confirm the port is actually available *before* publishing the handshake.
        # Publishing first meant a daemon that could not bind still advertised itself,
        # and for the moment before it died, clients saw a live pid and a port that
        # refused connections — a failure that looked like success.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, port))
        except OSError as exc:
            sys.stderr.write(
                f"agentguard: cannot bind {host}:{port} ({exc}). "
                "Change [daemon].port in config.toml and re-run `agentguard install claude`.\n"
            )
            raise SystemExit(1) from exc

    app = create_app(token, settings)
    write_handshake(host, port, token)

    agentguard_home().mkdir(parents=True, exist_ok=True)
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
        server.run()
    finally:
        clear_handshake()

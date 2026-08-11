"""Configuration and filesystem layout.

Global config:    ~/.agentguard/config.toml
Daemon handshake: ~/.agentguard/daemon.json      (host, port, token, pid)
Per-workspace:    <workspace>/.agentguard/agentguard.db   (SPEC §30)
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

ENV_DISABLE = "AGENTGUARD_DISABLE"
ENV_HOME = "AGENTGUARD_HOME"


def agentguard_home() -> Path:
    return Path(os.environ.get(ENV_HOME, Path.home() / ".agentguard")).expanduser()


def daemon_handshake_path() -> Path:
    return agentguard_home() / "daemon.json"


def workspace_state_dir(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / ".agentguard"


def is_globally_disabled() -> bool:
    """Kill switch. When set, every hook path returns 'no decision' immediately."""
    return os.environ.get(ENV_DISABLE, "").strip().lower() in {"1", "true", "yes", "on"}


def token_path() -> Path:
    return agentguard_home() / "token"


def get_or_create_token() -> str:
    """A stable local credential for the daemon.

    The hook URL and its ``Authorization`` header are written into settings.json once,
    but the daemon restarts often — so the token must survive restarts rather than being
    regenerated per process. 0600, local-only, never leaves the machine.
    """
    import secrets

    path = token_path()
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(token, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return token


class DaemonSettings(BaseModel):
    """Bind address for the local daemon.

    The port is *fixed* rather than ephemeral because Claude Code's `http` hook URL is
    baked into settings.json at install time and cannot be rewritten on every daemon
    restart. `agentguard doctor` reports a conflict if something else owns the port;
    change it here and re-run `agentguard install claude`.
    """

    host: str = "127.0.0.1"
    port: int = 8787

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class LatencyBudget(BaseModel):
    """SPEC §8 targets, expressed as enforceable numbers rather than aspirations."""

    deterministic_ms: float = 100.0
    repository_ms: float = 500.0
    # Hard ceiling for a hook round trip. Past this the client fails open so the
    # developer never waits on AgentGuard.
    hook_timeout_ms: float = 2000.0


class IndexSettings(BaseModel):
    max_file_bytes: int = 1_500_000
    max_files: int = 200_000
    exclude_dirs: list[str] = Field(
        default_factory=lambda: [
            ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
            "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
            "dist", "build", "target", ".next", ".nuxt", "out", "coverage",
            ".terraform", ".gradle", "vendor", ".idea", ".vscode", ".agentguard",
            ".tox", "site-packages", ".eggs", "htmlcov",
        ]
    )


class ChallengeSettings(BaseModel):
    """Anti-nag governance (SPEC §17, §39).

    AgentGuard must not become "AI that constantly interrupts AI", so challenges are
    rationed: each distinct concern is raised at most once, and there is a hard ceiling
    per task after which AgentGuard defers to the host and the human.
    """

    max_per_task: int = 6
    max_per_fingerprint: int = 1
    max_stop_blocks_per_task: int = 2
    # Below this severity nothing is ever surfaced to the agent.
    min_severity: str = "medium"


class Settings(BaseModel):
    enabled: bool = True
    log_level: str = "INFO"
    daemon: DaemonSettings = Field(default_factory=DaemonSettings)
    latency: LatencyBudget = Field(default_factory=LatencyBudget)
    index: IndexSettings = Field(default_factory=IndexSettings)
    challenge: ChallengeSettings = Field(default_factory=ChallengeSettings)

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or (agentguard_home() / "config.toml")
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (tomllib.TOMLDecodeError, OSError):
                # A broken config must never stop the agent from working.
                data = {}
        settings = cls.model_validate(data)
        if is_globally_disabled():
            settings.enabled = False
        return settings

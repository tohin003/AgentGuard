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
ENV_OBSERVE = "AGENTGUARD_OBSERVE"


def agentguard_home() -> Path:
    return Path(os.environ.get(ENV_HOME, Path.home() / ".agentguard")).expanduser()


def daemon_handshake_path() -> Path:
    return agentguard_home() / "daemon.json"


def database_path() -> Path:
    """One database for every project (Memory plan §1).

    Replaces the earlier per-workspace file. A single database means one retention pass,
    one maintenance schedule and one place for cross-session project memory, at the cost
    of requiring project scoping on every query — which `ProjectStore` makes structural.
    """
    return agentguard_home() / "agentguard.db"


def project_cache_dir(project_id: str) -> Path:
    return agentguard_home() / "projects" / project_id


def workspace_state_dir(workspace: str | Path) -> Path:
    """Deprecated: pre-Phase-3.5 per-workspace location, kept for cleanup only."""
    return Path(workspace).expanduser().resolve() / ".agentguard"


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def is_globally_disabled() -> bool:
    """Kill switch. When set, every hook path returns 'no decision' immediately."""
    return _flag(ENV_DISABLE)


def is_observe_only() -> bool:
    """Census mode. Everything is computed and recorded; nothing is ever said."""
    return _flag(ENV_OBSERVE)


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


class RetentionSettings(BaseModel):
    """How long each kind of data lives (Memory plan §5).

    Configuration values rather than hard-coded rules, as the plan requires. `0` means
    "keep indefinitely" — used for the two things that are worth keeping: validated
    project memory, and violations worth learning from.
    """

    raw_events_days: int = 14  # highest volume, lowest long-term value
    decisions_days: int = 30
    verifications_days: int = 60
    session_summaries_days: int = 270
    violations_days: int = 0  # long-term
    memories_days: int = 0  # long-term
    metrics_days: int = 90

    # Maintenance is deliberately infrequent and never runs while the agent is working.
    maintenance_interval_hours: float = 6.0
    vacuum_threshold_mb: float = 256.0


class DiskSettings(BaseModel):
    """Disk-space protection (Memory plan §8).

    The critical rule: "If SQLite fails or disk space becomes unavailable, AgentGuard's
    core reliability functionality must continue working." Below `critical_free_mb` the
    store stops writing entirely — evidence checks, challenges and verification carry on,
    because none of them depend on being able to persist a log line.
    """

    low_free_mb: float = 1024.0  # prune aggressively below this
    critical_free_mb: float = 256.0  # stop writing below this
    max_database_mb: float = 512.0


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
    # Phase 7's census switch. Every engine still runs and every finding is still
    # recorded; the agent hears none of it (see `core/observe.py`). Off by default —
    # while it is on, AgentGuard provides no protection at all.
    observe_only: bool = False
    log_level: str = "INFO"
    daemon: DaemonSettings = Field(default_factory=DaemonSettings)
    latency: LatencyBudget = Field(default_factory=LatencyBudget)
    index: IndexSettings = Field(default_factory=IndexSettings)
    challenge: ChallengeSettings = Field(default_factory=ChallengeSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    disk: DiskSettings = Field(default_factory=DiskSettings)

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
        if is_observe_only():
            settings.observe_only = True
        return settings

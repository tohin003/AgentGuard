"""Configuration and filesystem layout.

Global config:    ~/.agentguard/config.toml
Daemon handshake: ~/.agentguard/daemon.json      (host, port, token, pid)
Shared database:  ~/.agentguard/agentguard.db
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import secrets
import stat
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, ValidationError, field_validator

ENV_DISABLE = "AGENTGUARD_DISABLE"
ENV_HOME = "AGENTGUARD_HOME"
ENV_OBSERVE = "AGENTGUARD_OBSERVE"
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
_TRUSTED_SYSTEM_ALIASES = {
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}


def _trusted_system_alias(path: Path, mode: int) -> bool:
    """Allow macOS's root-owned /tmp and /var aliases, but no user symlink."""
    target = _TRUSTED_SYSTEM_ALIASES.get(path)
    if target is None:
        return False
    try:
        info = os.lstat(path)
        return info.st_uid == 0 and mode & 0o022 == 0 and path.resolve(strict=False) == target
    except OSError:
        return False


def _assert_no_symlink_components(path: Path) -> None:
    """Reject a path that traverses any existing symlink component.

    Checking only the leaf directory is insufficient when a symlinked parent already
    contains the requested child.  These paths hold credentials and lifecycle state, so
    fail closed before creating or reading anything below them.
    """
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = candidate
    while True:
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeError(f"could not inspect private path component: {current}") from exc
        else:
            if stat.S_ISLNK(mode) and not _trusted_system_alias(current, mode):
                raise RuntimeError(f"refusing to use symlinked private path component: {current}")
        parent = current.parent
        if parent == current:
            return
        current = parent


def agentguard_home() -> Path:
    return Path(os.environ.get(ENV_HOME, Path.home() / ".agentguard")).expanduser()


def ensure_private_dir(path: Path | None = None) -> Path:
    """Create a directory used for AgentGuard credentials with owner-only access."""
    directory = Path(path or agentguard_home()).expanduser()
    _assert_no_symlink_components(directory)
    # Check every existing component we are about to traverse.  Checking only the leaf
    # still permits ``AGENTGUARD_HOME=/tmp/user-link/agentguard`` to redirect credentials
    # through a symlinked parent.
    current = directory
    missing: list[Path] = []
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    if current.is_symlink() and not _trusted_system_alias(current, current.lstat().st_mode):
        raise RuntimeError(f"refusing to use symlinked private path component: {current}")
    directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    _assert_no_symlink_components(directory)
    for component in reversed(missing):
        if component.is_symlink() or not component.is_dir():
            raise RuntimeError(f"private path component is not a directory: {component}")
    # mkdir's mode is filtered by umask and is a no-op for an existing directory.
    # Re-apply it so an old/shared installation cannot leave credentials world-readable.
    os.chmod(directory, PRIVATE_DIR_MODE)
    return directory


def write_private_text(path: Path, content: str) -> None:
    """Atomically write owner-readable text (used for local credentials/settings)."""
    path = Path(path).expanduser()
    _assert_no_symlink_components(path.parent)
    if path.is_symlink():
        raise RuntimeError(f"refusing to overwrite symlink: {path}")
    if path.parent.is_symlink():
        raise RuntimeError(f"refusing to write through symlinked directory: {path.parent}")
    if path.exists() and not path.is_file():
        raise RuntimeError(f"refusing to overwrite non-regular file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_components(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, PRIVATE_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            # Set the mode through the already-open descriptor.  A pathname chmod
            # after close would introduce a symlink-swap window between creation and
            # replacement.
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), PRIVATE_FILE_MODE)
            else:  # pragma: no cover - legacy platforms without fchmod.
                os.chmod(tmp, PRIVATE_FILE_MODE)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


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
    path = token_path()
    ensure_private_dir()
    # Installers and daemon processes can ask for the token at the same time. The
    # lifecycle lock makes the read/repair/write sequence atomic, so no process can keep
    # using a token that a concurrent creator immediately replaced.
    from agentguard.daemon.lifecycle import interprocess_lock

    with interprocess_lock(path.with_name("token.lock"), timeout=2.0) as locked:
        if not locked:
            raise RuntimeError("could not acquire the AgentGuard token lock")
        if path.is_symlink():
            raise RuntimeError(f"refusing to use symlink as token file: {path}")
        if path.exists():
            if not path.is_file():
                raise RuntimeError(f"refusing to use non-regular token file: {path}")
            try:
                existing = path.read_text(encoding="utf-8").strip()
                if existing:
                    os.chmod(path, PRIVATE_FILE_MODE)
                    return existing
            except OSError:
                pass
        token = secrets.token_urlsafe(32)
        write_private_text(path, token)
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

    @field_validator("host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        """The HTTP daemon is intentionally local-only; reject network exposure."""
        host = value.strip().lower()
        if host == "localhost":
            return "127.0.0.1"
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError("daemon.host must be an IPv4 loopback address") from exc
        # The daemon pre-bind, doctor probe and current hook transport are IPv4. Accepting
        # ::1 here would look secure but fail later with an address-family error.
        if not isinstance(address, ipaddress.IPv4Address) or not address.is_loopback:
            raise ValueError("daemon.host must be an IPv4 loopback address")
        return host

    @field_validator("port")
    @classmethod
    def valid_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("daemon.port must be between 1 and 65535")
        return value

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


class LatencyBudget(BaseModel):
    """SPEC §8 targets, expressed as enforceable numbers rather than aspirations."""

    deterministic_ms: float = Field(100.0, ge=0)
    repository_ms: float = Field(500.0, ge=0)
    # Hard ceiling for a hook round trip. Past this the client fails open so the
    # developer never waits on AgentGuard.
    hook_timeout_ms: float = Field(2000.0, gt=0)


class IndexSettings(BaseModel):
    max_file_bytes: int = Field(1_500_000, gt=0)
    max_files: int = Field(200_000, gt=0)
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

    raw_events_days: int = Field(14, ge=0)  # highest volume, lowest long-term value
    decisions_days: int = Field(30, ge=0)
    verifications_days: int = Field(60, ge=0)
    session_summaries_days: int = Field(270, ge=0)
    violations_days: int = Field(0, ge=0)  # long-term
    memories_days: int = Field(0, ge=0)  # long-term
    metrics_days: int = Field(90, ge=0)

    # Maintenance is deliberately infrequent and never runs while the agent is working.
    maintenance_interval_hours: float = Field(6.0, ge=0)
    vacuum_threshold_mb: float = Field(256.0, ge=0)


class DiskSettings(BaseModel):
    """Disk-space protection (Memory plan §8).

    The critical rule: "If SQLite fails or disk space becomes unavailable, AgentGuard's
    core reliability functionality must continue working." Below `critical_free_mb` the
    store stops writing entirely — evidence checks, challenges and verification carry on,
    because none of them depend on being able to persist a log line.
    """

    low_free_mb: float = Field(1024.0, ge=0)  # prune aggressively below this
    critical_free_mb: float = Field(256.0, ge=0)  # stop writing below this
    max_database_mb: float = Field(512.0, ge=0)


class ChallengeSettings(BaseModel):
    """Anti-nag governance (SPEC §17, §39).

    AgentGuard must not become "AI that constantly interrupts AI", so challenges are
    rationed: each distinct concern is raised at most once, and there is a hard ceiling
    per task after which AgentGuard defers to the host and the human.
    """

    max_per_task: int = Field(6, ge=0)
    max_per_fingerprint: int = Field(1, ge=0)
    max_stop_blocks_per_task: int = Field(2, ge=0)
    # Below this severity nothing is ever surfaced to the agent.
    min_severity: str = "medium"

    @field_validator("min_severity")
    @classmethod
    def valid_min_severity(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"info", "low", "medium", "high", "critical"}:
            raise ValueError("challenge.min_severity must be info, low, medium, high, or critical")
        return normalized


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
    _config_warning: str | None = PrivateAttr(default=None)

    @property
    def config_warning(self) -> str | None:
        """A non-fatal config load problem, for diagnostics such as ``doctor``."""
        return self._config_warning

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or (agentguard_home() / "config.toml")
        data: dict[str, Any] = {}
        warning: str | None = None
        if path.exists():
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (tomllib.TOMLDecodeError, OSError, UnicodeError) as exc:
                # A broken config must never stop the agent from working.
                data = {}
                warning = f"could not read {path}: {exc}"
        try:
            settings = cls.model_validate(data)
        except ValidationError as exc:
            # A malformed config must not crash the hook process. Fall back to safe
            # defaults, notably the loopback daemon host, rather than accepting an
            # unsafe/ambiguous network binding.
            settings = cls()
            warning = f"invalid configuration in {path}: {exc.errors()[0].get('msg', 'validation failed')}"
        settings._config_warning = warning
        if is_globally_disabled():
            settings.enabled = False
        if is_observe_only():
            settings.observe_only = True
        return settings

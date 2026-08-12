"""Persistence (SPEC §30 · Memory & Database Management Plan).

One SQLite database for every project, logically separated by project and session:

    AgentGuard SQLite
    ├── Projects
    │   ├── Project A -> Session 1, Session 2, ...
    │   └── Project B -> Session 1, ...
    └── Global configuration

Three properties this module is responsible for.

**Project isolation is structural.** The engine never holds the `Database`; it holds a
`ProjectStore` bound to one project, and there is no method on it that can express a
cross-project query. The memory plan's §4 — "AgentGuard must never accidentally use
Project A's architectural knowledge while working on Project B" — is therefore enforced by
the type, not by remembering to add a `WHERE`.

**Rows are bounded.** Per §6, a decision record holds the task, the verdict, evidence
*references* and a short summary — never a copy of the payload. Source code lives on disk
and in git; this database stores metadata and memory.

**Storage is never a dependency.** Per §8: "If SQLite fails or disk space becomes
unavailable, AgentGuard's core reliability functionality must continue working." Every
public method is wrapped so a failure returns a neutral value, and when free disk falls
below the critical threshold the store stops writing altogether. Evidence checks,
challenges and verification do not need to persist anything in order to work.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from agentguard.core.config import (
    DiskSettings,
    RetentionSettings,
    Settings,
    database_path,
)
from agentguard.core.events import AgentEvent
from agentguard.core.models import Decision

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3

# Per §6: cap what a single row can hold. A tool argument blob is evidence that an action
# happened, not a place to archive file contents.
MAX_ARGUMENT_BYTES = 4_000
MAX_TEXT_BYTES = 2_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    root       TEXT NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    git_remote TEXT NOT NULL DEFAULT '',
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL
);

-- Keyed on (id, project_id), not id alone. A session id is unique to the agent that
-- issued it, not to this database, so a bare `id` primary key means the *second* project
-- to report a given session id silently loses the row — and the census counts sessions.
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT NOT NULL,
    project_id TEXT NOT NULL,
    agent      TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at   REAL,
    meta       TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, project_id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, started_at);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    prompt      TEXT NOT NULL DEFAULT '',
    spec        TEXT NOT NULL DEFAULT '{}',
    complexity  REAL,
    depth       TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  REAL NOT NULL,
    closed_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(project_id, session_id, status);

CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    task_id    TEXT,
    type       TEXT NOT NULL,
    agent      TEXT NOT NULL DEFAULT '',
    tool       TEXT,
    arguments  TEXT NOT NULL DEFAULT '{}',
    ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(project_id, task_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS decisions (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    event_id   TEXT NOT NULL DEFAULT '',
    task_id    TEXT,
    session_id TEXT NOT NULL DEFAULT '',
    tool       TEXT,
    action     TEXT NOT NULL,
    -- In observe-only mode `action` is always 'allow'; `would_have` is what AgentGuard
    -- would have done. Empty outside observe-only mode.
    would_have TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    level      INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0,
    ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_task ON decisions(project_id, task_id);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(project_id, ts);

CREATE TABLE IF NOT EXISTS findings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    task_id     TEXT,
    category    TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    -- Which SPEC §3 failure mode this finding is evidence of. The census counts this
    -- column; inferring it from (category, verdict) at report time would make the
    -- taxonomy a guess made after the fact rather than a claim the detector made.
    failure_mode TEXT NOT NULL DEFAULT '',
    severity    TEXT NOT NULL,
    subject     TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT '[]',
    fingerprint TEXT NOT NULL DEFAULT '',
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_decision ON findings(project_id, decision_id);
CREATE INDEX IF NOT EXISTS idx_findings_fp ON findings(project_id, task_id, fingerprint);

CREATE TABLE IF NOT EXISTS challenges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    subject     TEXT NOT NULL DEFAULT '',
    times       INTEGER NOT NULL DEFAULT 1,
    resolved    INTEGER NOT NULL DEFAULT 0,
    first_ts    REAL NOT NULL,
    last_ts     REAL NOT NULL,
    UNIQUE(project_id, task_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS verifications (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}',
    started_at  REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_verifications_task ON verifications(project_id, task_id);

CREATE TABLE IF NOT EXISTS metrics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    name       TEXT NOT NULL,
    value      REAL NOT NULL,
    labels     TEXT NOT NULL DEFAULT '{}',
    ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_name_ts ON metrics(project_id, name, ts);

-- Persistent project memory (Memory plan §3 · ACT II "memory confidence").
-- Created here so that violations and verification results are recorded from Phase 4
-- onward in a shape that can be promoted in Phase 9 without a migration. Nothing writes
-- to it yet: the plan is explicit that only *validated* knowledge is promoted, and
-- deciding what qualifies is Phase 9's job.
CREATE TABLE IF NOT EXISTS memories (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    memory_type       TEXT NOT NULL,
    content           TEXT NOT NULL,
    source            TEXT NOT NULL DEFAULT '',
    source_files      TEXT NOT NULL DEFAULT '[]',
    confidence        REAL NOT NULL DEFAULT 0.5,
    validation_status TEXT NOT NULL DEFAULT 'unverified',
    session_id        TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL,
    last_verified     REAL
);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id, memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(project_id, validation_status);
"""


# -- project identity -------------------------------------------------------------


@dataclass(slots=True)
class ProjectIdentity:
    id: str
    root: str
    name: str
    git_remote: str


def _git_remote(root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.decode("utf-8", "replace").strip() if proc.returncode == 0 else ""


def project_identity(root: str | Path) -> ProjectIdentity:
    """Stable identity for a repository.

    The git remote is preferred over the path: a project moved, re-cloned or checked out
    as a worktree is still the same project, and its accumulated memory should follow it.
    Falls back to the canonical path outside git.
    """
    resolved = Path(root).expanduser().resolve()
    remote = _git_remote(resolved)
    basis = remote or str(resolved)
    return ProjectIdentity(
        id=hashlib.sha256(basis.encode()).hexdigest()[:16],
        root=str(resolved),
        name=resolved.name,
        git_remote=remote,
    )


# -- bounded serialisation --------------------------------------------------------


def summarize_value(value: Any) -> Any:
    """Per §6: keep the shape, drop the bulk."""
    if isinstance(value, str):
        if len(value) <= 200:
            return value
        return f"{value[:200]}… <{len(value)} chars>"
    if isinstance(value, dict):
        return {k: summarize_value(v) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        head = [summarize_value(v) for v in value[:10]]
        return [*head, f"… <{len(value)} items>"] if len(value) > 10 else head
    return value


def summarize_arguments(arguments: dict[str, Any]) -> str:
    try:
        text = json.dumps(summarize_value(arguments), default=str)
    except (TypeError, ValueError):
        return "{}"
    return text[:MAX_ARGUMENT_BYTES]


def _safe(default: Any = None):
    """Storage must never break the agent. Log and carry on (Memory plan §8)."""

    def deco(fn):
        def wrapper(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception:
                log.exception("agentguard store: %s failed", fn.__name__)
                return default() if callable(default) else default

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return deco


# -- the database -----------------------------------------------------------------


class Database:
    """The single shared connection, schema, maintenance and disk protection."""

    _instances: ClassVar[dict[str, Database]] = {}
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, path: Path, settings: Settings | None = None) -> None:
        settings = settings or Settings()
        self.path = path
        self.retention: RetentionSettings = settings.retention
        self.disk: DiskSettings = settings.disk
        self._lock = threading.RLock()
        self._last_maintenance = 0.0
        self._writes_disabled = False

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=3000")
        self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    # -- migration ----------------------------------------------------------------

    # Columns added after a table first shipped. `CREATE TABLE IF NOT EXISTS` is a no-op
    # on an existing table, so a new column has to be added explicitly or every database
    # created before this release keeps the old shape and every insert fails.
    #
    # Additive only, on purpose. A developer's decision history is not worth risking to a
    # rename, and every column here has a default that makes old rows readable: an empty
    # `failure_mode` means "recorded before the census existed", which is the truth.
    _ADDED_COLUMNS: ClassVar[tuple[tuple[str, str, str], ...]] = (
        ("findings", "failure_mode", "TEXT NOT NULL DEFAULT ''"),
        ("decisions", "would_have", "TEXT NOT NULL DEFAULT ''"),
    )

    def _migrate(self) -> None:
        """Bring an existing database up to `SCHEMA_VERSION`. Never destructive."""
        for table, column, declaration in self._ADDED_COLUMNS:
            existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue  # table absent entirely; the schema script just created it
            if column not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        self._rekey_sessions()

    def _rekey_sessions(self) -> None:
        """Widen the sessions primary key from `id` to `(id, project_id)`.

        The only non-additive migration here, and it fixes a real defect: with `id` alone
        as the key, the second project to report a given session id had its row dropped by
        `INSERT OR IGNORE` and vanished from every count. Unlikely in production (agents
        issue UUIDs) but not impossible, and the census divides by this number.

        Copy-and-rename rather than an ALTER, because SQLite cannot change a primary key
        in place. Failure leaves the old table exactly as it was: an over-strict key
        under-counts sessions, while a half-finished rebuild loses them, so the old shape
        is strictly the safer place to stop.
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='sessions'"
        ).fetchone()
        if row is None or "PRIMARY KEY (id, project_id)" in (row["sql"] or ""):
            return

        try:
            self._conn.execute("ALTER TABLE sessions RENAME TO sessions_legacy")
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions(id, project_id, agent, started_at, ended_at, meta) "
                "SELECT id, project_id, agent, started_at, ended_at, meta FROM sessions_legacy"
            )
            self._conn.execute("DROP TABLE sessions_legacy")
            self._conn.commit()
        except sqlite3.Error:
            log.exception("agentguard store: sessions re-key failed; keeping the old table")
            self._conn.rollback()
            with contextlib.suppress(sqlite3.Error):
                self._conn.execute("ALTER TABLE sessions_legacy RENAME TO sessions")
                self._conn.commit()

    @classmethod
    def shared(cls, settings: Settings | None = None) -> Database:
        path = database_path()
        key = str(path)
        with cls._registry_lock:
            existing = cls._instances.get(key)
            if existing is None:
                existing = cls(path, settings)
                cls._instances[key] = existing
            return existing

    @classmethod
    def reset_shared(cls) -> None:
        """Drop cached handles — used by tests that relocate AGENTGUARD_HOME."""
        with cls._registry_lock:
            for instance in cls._instances.values():
                with contextlib.suppress(Exception):
                    instance.close()
            cls._instances.clear()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- disk protection ----------------------------------------------------------

    def free_megabytes(self) -> float:
        try:
            return shutil.disk_usage(self.path.parent).free / (1024 * 1024)
        except OSError:
            return float("inf")  # unknowable: do not degrade on a bad reading

    def disk_state(self) -> str:
        free = self.free_megabytes()
        if free < self.disk.critical_free_mb:
            return "critical"
        if free < self.disk.low_free_mb:
            return "low"
        return "healthy"

    @property
    def writes_enabled(self) -> bool:
        if self._writes_disabled:
            return False
        return self.disk_state() != "critical"

    # -- primitives ---------------------------------------------------------------

    def write(self, sql: str, params: tuple) -> None:
        if not self.writes_enabled:
            return  # §8: reliability continues without persistence
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # -- maintenance (Memory plan §7) ---------------------------------------------

    def maintenance_due(self) -> bool:
        interval = self.retention.maintenance_interval_hours * 3600
        return (time.time() - self._last_maintenance) >= interval

    @_safe(default=dict)
    def maintain(self, force: bool = False) -> dict[str, int]:
        """Prune expired rows, checkpoint the WAL, reclaim pages.

        Never call this on the hook hot path. It runs on `SessionEnd`, when the agent has
        finished and a hundred milliseconds costs nobody anything (§7: "Avoid running
        expensive maintenance during active agent execution").
        """
        if not force and not self.maintenance_due():
            return {}
        self._last_maintenance = time.time()

        aggressive = self.disk_state() != "healthy"
        removed = self.prune(aggressive=aggressive)

        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("PRAGMA incremental_vacuum")
            self._conn.commit()

        if self.size_megabytes() > self.retention.vacuum_threshold_mb:
            with self._lock:
                self._conn.execute("VACUUM")
        return removed

    @_safe(default=dict)
    def prune(self, aggressive: bool = False) -> dict[str, int]:
        """Apply the retention tiers. `0` days means keep indefinitely."""
        scale = 0.25 if aggressive else 1.0
        rules = [
            ("events", self.retention.raw_events_days, "ts"),
            ("decisions", self.retention.decisions_days, "ts"),
            ("verifications", self.retention.verifications_days, "started_at"),
            ("metrics", self.retention.metrics_days, "ts"),
            ("sessions", self.retention.session_summaries_days, "started_at"),
        ]
        removed: dict[str, int] = {}
        now = time.time()
        for table, days, column in rules:
            if days <= 0:
                continue
            cutoff = now - (days * scale * 86400)
            with self._lock:
                cursor = self._conn.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))
                removed[table] = cursor.rowcount if cursor.rowcount > 0 else 0
                self._conn.commit()

        # Findings belong to decisions; a finding whose decision is gone is unreadable.
        # Violations worth keeping are promoted to `memories` in Phase 9.
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM findings WHERE decision_id NOT IN (SELECT id FROM decisions)"
            )
            removed["findings"] = max(cursor.rowcount, 0)
            self._conn.commit()
        return removed

    def size_megabytes(self) -> float:
        try:
            return self.path.stat().st_size / (1024 * 1024)
        except OSError:
            return 0.0

    @_safe(default=dict)
    def stats(self) -> dict[str, Any]:
        tables = (
            "projects", "sessions", "tasks", "events", "decisions",
            "findings", "challenges", "verifications", "metrics", "memories",
        )
        counts = {
            table: int(self.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]) for table in tables
        }
        return {
            "path": str(self.path),
            "size_mb": round(self.size_megabytes(), 2),
            "free_disk_mb": round(self.free_megabytes(), 1),
            "disk_state": self.disk_state(),
            "writes_enabled": self.writes_enabled,
            "rows": counts,
        }


# -- the project-scoped handle ----------------------------------------------------


class ProjectStore:
    """Everything the engine can do with storage, bound to one project.

    There is deliberately no method here that takes a project id. Cross-project access is
    not restricted — it is unrepresentable.
    """

    def __init__(self, db: Database, identity: ProjectIdentity) -> None:
        self.db = db
        self.identity = identity
        self.project_id = identity.id
        self._register()

    @classmethod
    def for_workspace(cls, workspace: str | Path, settings: Settings | None = None) -> ProjectStore:
        return cls(Database.shared(settings), project_identity(workspace))

    @_safe()
    def _register(self) -> None:
        now = time.time()
        self.db.write(
            "INSERT INTO projects(id, root, name, git_remote, first_seen, last_seen) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, root=excluded.root",
            (self.project_id, self.identity.root, self.identity.name, self.identity.git_remote, now, now),
        )

    def close(self) -> None:
        """A project handle owns nothing; the database is shared and stays open."""

    # -- sessions -----------------------------------------------------------------

    @_safe()
    def open_session(
        self, session_id: str, agent: str, workspace: str = "", meta: dict | None = None
    ) -> None:
        self.db.write(
            "INSERT OR IGNORE INTO sessions(id, project_id, agent, started_at, meta) VALUES(?,?,?,?,?)",
            (session_id, self.project_id, agent, time.time(), json.dumps(meta or {})[:MAX_TEXT_BYTES]),
        )

    @_safe()
    def close_session(self, session_id: str) -> None:
        self.db.write(
            "UPDATE sessions SET ended_at=? WHERE id=? AND project_id=?",
            (time.time(), session_id, self.project_id),
        )

    # -- tasks --------------------------------------------------------------------

    @_safe()
    def create_task(
        self,
        session_id: str,
        prompt: str,
        spec: dict | None = None,
        complexity: float | None = None,
        depth: str | None = None,
    ) -> str:
        task_id = uuid.uuid4().hex
        self.db.write(
            "INSERT INTO tasks(id, project_id, session_id, prompt, spec, complexity, depth, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                task_id,
                self.project_id,
                session_id,
                prompt[:MAX_TEXT_BYTES],
                json.dumps(spec or {}, default=str)[:MAX_ARGUMENT_BYTES],
                complexity,
                depth,
                time.time(),
            ),
        )
        return task_id

    @_safe()
    def current_task_id(self, session_id: str) -> str | None:
        rows = self.db.query(
            "SELECT id FROM tasks WHERE project_id=? AND session_id=? AND status='open' "
            "ORDER BY created_at DESC LIMIT 1",
            (self.project_id, session_id),
        )
        return rows[0]["id"] if rows else None

    @_safe()
    def close_task(self, task_id: str, status: str = "closed") -> None:
        self.db.write(
            "UPDATE tasks SET status=?, closed_at=? WHERE id=? AND project_id=?",
            (status, time.time(), task_id, self.project_id),
        )

    # -- events & decisions -------------------------------------------------------

    @_safe()
    def record_event(self, event: AgentEvent) -> None:
        self.db.write(
            "INSERT OR REPLACE INTO events"
            "(id, project_id, session_id, task_id, type, agent, tool, arguments, ts) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                event.event_id,
                self.project_id,
                event.session_id,
                event.task_id,
                str(event.event),
                event.agent,
                event.tool,
                summarize_arguments(event.arguments),
                event.ts,
            ),
        )

    @_safe(default="")
    def record_decision(self, event: AgentEvent, decision: Decision) -> str:
        decision_id = decision.decision_id or uuid.uuid4().hex
        now = time.time()
        self.db.write(
            "INSERT OR REPLACE INTO decisions"
            "(id, project_id, event_id, task_id, session_id, tool, action, would_have, reason, "
            " level, latency_ms, ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision_id,
                self.project_id,
                event.event_id,
                event.task_id,
                event.session_id,
                event.tool,
                str(decision.action),
                str(decision.would_have or ""),
                decision.reason[:MAX_TEXT_BYTES],
                int(decision.level),
                decision.latency_ms,
                now,
            ),
        )
        for f in decision.findings:
            self.db.write(
                "INSERT INTO findings"
                "(project_id, decision_id, task_id, category, verdict, failure_mode, severity, "
                " subject, summary, detail, evidence, fingerprint, ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    self.project_id,
                    decision_id,
                    event.task_id,
                    str(f.category),
                    str(f.verdict),
                    str(f.failure_mode),
                    str(f.severity),
                    f.subject[:200],
                    f.summary[:500],
                    f.detail[:MAX_TEXT_BYTES],
                    json.dumps(
                        [e.model_dump() for e in f.evidence], default=str
                    )[:MAX_ARGUMENT_BYTES],
                    f.fingerprint(),
                    now,
                ),
            )
        return decision_id

    @_safe(default=list)
    def recent_decisions(self, limit: int = 50) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM decisions WHERE project_id=? ORDER BY ts DESC LIMIT ?",
            (self.project_id, limit),
        )
        return [dict(r) for r in rows]

    @_safe(default=None)
    def decision_detail(self, decision_id: str) -> dict | None:
        rows = self.db.query(
            "SELECT * FROM decisions WHERE id=? AND project_id=?", (decision_id, self.project_id)
        )
        if not rows:
            return None
        out = dict(rows[0])
        out["findings"] = [
            dict(r)
            for r in self.db.query(
                "SELECT * FROM findings WHERE decision_id=? AND project_id=?",
                (decision_id, self.project_id),
            )
        ]
        return out

    # -- failure-mode census (SPEC §3 · Phase 7) ----------------------------------

    @_safe(default=list)
    def failure_mode_counts(self, since: float) -> list[dict]:
        """Observations per SPEC §3 failure mode, since `since`.

        Two counts, because they answer different questions. `occurrences` is how often
        the detector fired; `tasks` is how many distinct pieces of work were affected.
        A single stubborn edit retried six times is six occurrences and one task, and
        reporting only the first would make one bad afternoon look like an epidemic.

        Rows with an empty `failure_mode` predate the census and are excluded — counting
        them as anything would be inventing data.
        """
        rows = self.db.query(
            "SELECT failure_mode, COUNT(*) AS occurrences, "
            "       COUNT(DISTINCT COALESCE(task_id, decision_id)) AS tasks, "
            "       COUNT(DISTINCT fingerprint) AS distinct_subjects, "
            "       MAX(ts) AS last_seen "
            "FROM findings WHERE project_id=? AND ts>? AND failure_mode NOT IN ('', ?) "
            "GROUP BY failure_mode ORDER BY occurrences DESC",
            (self.project_id, since, "not_a_failure"),
        )
        return [dict(r) for r in rows]

    @_safe(default=list)
    def failure_mode_examples(self, mode: str, since: float, limit: int = 3) -> list[dict]:
        """A few real observations of one mode, for the report's evidence column."""
        rows = self.db.query(
            "SELECT subject, summary, severity, ts FROM findings "
            "WHERE project_id=? AND ts>? AND failure_mode=? "
            "GROUP BY fingerprint ORDER BY ts DESC LIMIT ?",
            (self.project_id, since, mode, limit),
        )
        return [dict(r) for r in rows]

    @_safe(default=dict)
    def activity(self, since: float) -> dict[str, Any]:
        """The denominators. A count of failures without them is not a rate."""

        def scalar(sql: str, params: tuple) -> int:
            rows = self.db.query(sql, params)
            return int(rows[0][0]) if rows else 0

        pid = self.project_id
        sessions = scalar(
            "SELECT COUNT(*) FROM sessions WHERE project_id=? AND started_at>?", (pid, since)
        )
        observed_sessions = scalar(
            "SELECT COUNT(*) FROM sessions WHERE project_id=? AND started_at>? "
            "AND meta LIKE '%\"observe_only\": true%'",
            (pid, since),
        )
        return {
            "sessions": sessions,
            "observe_only_sessions": observed_sessions,
            "tasks": scalar(
                "SELECT COUNT(*) FROM tasks WHERE project_id=? AND created_at>?", (pid, since)
            ),
            "decisions": scalar(
                "SELECT COUNT(*) FROM decisions WHERE project_id=? AND ts>?", (pid, since)
            ),
            "would_have_spoken": scalar(
                "SELECT COUNT(*) FROM decisions WHERE project_id=? AND ts>? "
                "AND would_have NOT IN ('', 'allow')",
                (pid, since),
            ),
            "first_seen": (
                self.db.query(
                    "SELECT COALESCE(MIN(started_at), 0) FROM sessions "
                    "WHERE project_id=? AND started_at>?",
                    (pid, since),
                )[0][0]
            ),
        }

    # -- challenge ledger (SPEC §17, §39) -----------------------------------------

    @_safe(default=0)
    def challenge_count(self, task_id: str, fingerprint: str | None = None) -> int:
        if fingerprint is None:
            rows = self.db.query(
                "SELECT COALESCE(SUM(times), 0) AS n FROM challenges WHERE project_id=? AND task_id=?",
                (self.project_id, task_id),
            )
        else:
            rows = self.db.query(
                "SELECT COALESCE(SUM(times), 0) AS n FROM challenges "
                "WHERE project_id=? AND task_id=? AND fingerprint=?",
                (self.project_id, task_id, fingerprint),
            )
        return int(rows[0]["n"]) if rows else 0

    @_safe()
    def record_challenge(self, task_id: str, fingerprint: str, subject: str = "") -> None:
        now = time.time()
        self.db.write(
            "INSERT INTO challenges(project_id, task_id, fingerprint, subject, times, first_ts, last_ts) "
            "VALUES(?,?,?,?,1,?,?) "
            "ON CONFLICT(project_id, task_id, fingerprint) "
            "DO UPDATE SET times=times+1, last_ts=excluded.last_ts",
            (self.project_id, task_id, fingerprint, subject[:200], now, now),
        )

    @_safe()
    def resolve_challenge(self, task_id: str, fingerprint: str) -> None:
        self.db.write(
            "UPDATE challenges SET resolved=1 WHERE project_id=? AND task_id=? AND fingerprint=?",
            (self.project_id, task_id, fingerprint),
        )

    # -- verification -------------------------------------------------------------

    @_safe(default="")
    def start_verification(self, task_id: str, kind: str) -> str:
        vid = uuid.uuid4().hex
        self.db.write(
            "INSERT INTO verifications(id, project_id, task_id, kind, status, started_at) "
            "VALUES(?,?,?,?,?,?)",
            (vid, self.project_id, task_id, kind, "running", time.time()),
        )
        return vid

    @_safe()
    def finish_verification(self, verification_id: str, status: str, detail: dict | None = None) -> None:
        self.db.write(
            "UPDATE verifications SET status=?, detail=?, finished_at=? WHERE id=? AND project_id=?",
            (
                status,
                json.dumps(summarize_value(detail or {}), default=str)[:MAX_ARGUMENT_BYTES],
                time.time(),
                verification_id,
                self.project_id,
            ),
        )

    @_safe(default=list)
    def verifications_for(self, task_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self.db.query(
                "SELECT * FROM verifications WHERE project_id=? AND task_id=?",
                (self.project_id, task_id),
            )
        ]

    # -- metrics (SPEC §37) -------------------------------------------------------

    @_safe()
    def record_metric(self, name: str, value: float, labels: dict | None = None) -> None:
        self.db.write(
            "INSERT INTO metrics(project_id, name, value, labels, ts) VALUES(?,?,?,?,?)",
            (self.project_id, name, float(value), json.dumps(labels or {})[:500], time.time()),
        )

    @_safe(default=list)
    def metric_values(self, name: str, limit: int = 10_000) -> list[float]:
        rows = self.db.query(
            "SELECT value FROM metrics WHERE project_id=? AND name=? ORDER BY ts DESC LIMIT ?",
            (self.project_id, name, limit),
        )
        return [float(r["value"]) for r in rows]

    # -- persistent memory (written from Phase 9) ---------------------------------

    @_safe(default=list)
    def memories(self, memory_type: str | None = None, limit: int = 100) -> list[dict]:
        if memory_type:
            rows = self.db.query(
                "SELECT * FROM memories WHERE project_id=? AND memory_type=? "
                "ORDER BY confidence DESC LIMIT ?",
                (self.project_id, memory_type, limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM memories WHERE project_id=? ORDER BY confidence DESC LIMIT ?",
                (self.project_id, limit),
            )
        return [dict(r) for r in rows]

    @_safe(default="")
    def remember(
        self,
        memory_type: str,
        content: str,
        source: str = "",
        source_files: list[str] | None = None,
        confidence: float = 0.5,
        validation_status: str = "unverified",
        session_id: str = "",
    ) -> str:
        """Promote one validated fact into long-term project memory.

        Deliberately not called from anywhere yet. The memory plan is explicit that only
        useful, validated, project-specific knowledge is promoted — "Do not automatically
        save every agent response" — and deciding what qualifies is Phase 9's work.
        """
        memory_id = uuid.uuid4().hex
        self.db.write(
            "INSERT INTO memories(id, project_id, memory_type, content, source, source_files, "
            "confidence, validation_status, session_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                memory_id,
                self.project_id,
                memory_type,
                content[:MAX_TEXT_BYTES],
                source[:500],
                json.dumps(source_files or [])[:MAX_TEXT_BYTES],
                float(confidence),
                validation_status,
                session_id,
                time.time(),
            ),
        )
        return memory_id


# Backwards-compatible alias: the engine and CLI talk to a project-scoped handle.
Store = ProjectStore

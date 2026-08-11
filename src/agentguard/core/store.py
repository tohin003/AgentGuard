"""SQLite persistence (SPEC §30).

Stores tasks, decisions, evidence, violations, agent actions, verification results and
benchmark metrics. Local-first, offline, no network.

Design notes
------------
* WAL + ``synchronous=NORMAL``: commits do not fsync, so a write costs tens of
  microseconds. This sits on the hook hot path, so that matters (SPEC §8).
* Every public method is wrapped so a storage failure can never propagate into a hook
  response. Losing a log line is acceptable; blocking the developer's agent is not.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from agentguard.core.config import workspace_state_dir
from agentguard.core.events import AgentEvent
from agentguard.core.models import Decision

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    agent      TEXT NOT NULL,
    workspace  TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at   REAL,
    meta       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    prompt      TEXT NOT NULL DEFAULT '',
    spec        TEXT NOT NULL DEFAULT '{}',
    complexity  REAL,
    depth       TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  REAL NOT NULL,
    closed_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);

CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    task_id    TEXT,
    type       TEXT NOT NULL,
    agent      TEXT NOT NULL DEFAULT '',
    tool       TEXT,
    arguments  TEXT NOT NULL DEFAULT '{}',
    ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS decisions (
    id         TEXT PRIMARY KEY,
    event_id   TEXT NOT NULL DEFAULT '',
    task_id    TEXT,
    session_id TEXT NOT NULL DEFAULT '',
    tool       TEXT,
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    level      INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0,
    ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_task ON decisions(task_id);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);

CREATE TABLE IF NOT EXISTS findings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL,
    task_id     TEXT,
    category    TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    severity    TEXT NOT NULL,
    subject     TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT '[]',
    fingerprint TEXT NOT NULL DEFAULT '',
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_decision ON findings(decision_id);
CREATE INDEX IF NOT EXISTS idx_findings_fp ON findings(task_id, fingerprint);

CREATE TABLE IF NOT EXISTS challenges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    subject     TEXT NOT NULL DEFAULT '',
    times       INTEGER NOT NULL DEFAULT 1,
    resolved    INTEGER NOT NULL DEFAULT 0,
    first_ts    REAL NOT NULL,
    last_ts     REAL NOT NULL,
    UNIQUE(task_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS verifications (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}',
    started_at  REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_verifications_task ON verifications(task_id);

CREATE TABLE IF NOT EXISTS metrics (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL,
    value  REAL NOT NULL,
    labels TEXT NOT NULL DEFAULT '{}',
    ts     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_name_ts ON metrics(name, ts);
"""


def _safe(default: Any = None):
    """Storage must never break the agent. Log and carry on."""

    def deco(fn):
        def wrapper(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception:
                log.exception("agentguard store: %s failed", fn.__name__)
                return default

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return deco


class Store:
    """Thread-safe SQLite store, one per workspace."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=3000")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    @classmethod
    def for_workspace(cls, workspace: str | Path) -> Store:
        return cls(workspace_state_dir(workspace) / "agentguard.db")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _write(self, sql: str, params: tuple) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # -- sessions -----------------------------------------------------------------

    @_safe()
    def open_session(self, session_id: str, agent: str, workspace: str, meta: dict | None = None) -> None:
        self._write(
            "INSERT OR IGNORE INTO sessions(id, agent, workspace, started_at, meta) VALUES(?,?,?,?,?)",
            (session_id, agent, str(workspace), time.time(), json.dumps(meta or {})),
        )

    @_safe()
    def close_session(self, session_id: str) -> None:
        self._write("UPDATE sessions SET ended_at=? WHERE id=?", (time.time(), session_id))

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
        self._write(
            "INSERT INTO tasks(id, session_id, prompt, spec, complexity, depth, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (task_id, session_id, prompt, json.dumps(spec or {}), complexity, depth, time.time()),
        )
        return task_id

    @_safe()
    def current_task_id(self, session_id: str) -> str | None:
        rows = self._query(
            "SELECT id FROM tasks WHERE session_id=? AND status='open' ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        )
        return rows[0]["id"] if rows else None

    @_safe()
    def close_task(self, task_id: str, status: str = "closed") -> None:
        self._write(
            "UPDATE tasks SET status=?, closed_at=? WHERE id=?", (status, time.time(), task_id)
        )

    # -- events & decisions -------------------------------------------------------

    @_safe()
    def record_event(self, event: AgentEvent) -> None:
        self._write(
            "INSERT OR REPLACE INTO events(id, session_id, task_id, type, agent, tool, arguments, ts) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                event.event_id,
                event.session_id,
                event.task_id,
                str(event.event),
                event.agent,
                event.tool,
                json.dumps(event.arguments, default=str)[:200_000],
                event.ts,
            ),
        )

    @_safe(default="")
    def record_decision(self, event: AgentEvent, decision: Decision) -> str:
        decision_id = decision.decision_id or uuid.uuid4().hex
        now = time.time()
        self._write(
            "INSERT OR REPLACE INTO decisions"
            "(id, event_id, task_id, session_id, tool, action, reason, level, latency_ms, ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                decision_id,
                event.event_id,
                event.task_id,
                event.session_id,
                event.tool,
                str(decision.action),
                decision.reason,
                int(decision.level),
                decision.latency_ms,
                now,
            ),
        )
        for f in decision.findings:
            self._write(
                "INSERT INTO findings"
                "(decision_id, task_id, category, verdict, severity, subject, summary, detail, "
                " evidence, fingerprint, ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    event.task_id,
                    str(f.category),
                    str(f.verdict),
                    str(f.severity),
                    f.subject,
                    f.summary,
                    f.detail,
                    json.dumps([e.model_dump() for e in f.evidence]),
                    f.fingerprint(),
                    now,
                ),
            )
        return decision_id

    @_safe(default=[])
    def recent_decisions(self, limit: int = 50) -> list[dict]:
        rows = self._query("SELECT * FROM decisions ORDER BY ts DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    @_safe(default=None)
    def decision_detail(self, decision_id: str) -> dict | None:
        rows = self._query("SELECT * FROM decisions WHERE id=?", (decision_id,))
        if not rows:
            return None
        out = dict(rows[0])
        out["findings"] = [
            dict(r) for r in self._query("SELECT * FROM findings WHERE decision_id=?", (decision_id,))
        ]
        return out

    # -- challenge ledger (SPEC §17, §39) -----------------------------------------

    @_safe(default=0)
    def challenge_count(self, task_id: str, fingerprint: str | None = None) -> int:
        if fingerprint is None:
            rows = self._query(
                "SELECT COALESCE(SUM(times), 0) AS n FROM challenges WHERE task_id=?", (task_id,)
            )
        else:
            rows = self._query(
                "SELECT COALESCE(SUM(times), 0) AS n FROM challenges WHERE task_id=? AND fingerprint=?",
                (task_id, fingerprint),
            )
        return int(rows[0]["n"]) if rows else 0

    @_safe()
    def record_challenge(self, task_id: str, fingerprint: str, subject: str = "") -> None:
        now = time.time()
        self._write(
            "INSERT INTO challenges(task_id, fingerprint, subject, times, first_ts, last_ts) "
            "VALUES(?,?,?,1,?,?) "
            "ON CONFLICT(task_id, fingerprint) DO UPDATE SET times=times+1, last_ts=excluded.last_ts",
            (task_id, fingerprint, subject, now, now),
        )

    @_safe()
    def resolve_challenge(self, task_id: str, fingerprint: str) -> None:
        self._write(
            "UPDATE challenges SET resolved=1 WHERE task_id=? AND fingerprint=?",
            (task_id, fingerprint),
        )

    # -- verification -------------------------------------------------------------

    @_safe(default="")
    def start_verification(self, task_id: str, kind: str) -> str:
        vid = uuid.uuid4().hex
        self._write(
            "INSERT INTO verifications(id, task_id, kind, status, started_at) VALUES(?,?,?,?,?)",
            (vid, task_id, kind, "running", time.time()),
        )
        return vid

    @_safe()
    def finish_verification(self, verification_id: str, status: str, detail: dict | None = None) -> None:
        self._write(
            "UPDATE verifications SET status=?, detail=?, finished_at=? WHERE id=?",
            (status, json.dumps(detail or {}, default=str), time.time(), verification_id),
        )

    @_safe(default=[])
    def verifications_for(self, task_id: str) -> list[dict]:
        return [dict(r) for r in self._query("SELECT * FROM verifications WHERE task_id=?", (task_id,))]

    # -- metrics (SPEC §37) -------------------------------------------------------

    @_safe()
    def record_metric(self, name: str, value: float, labels: dict | None = None) -> None:
        self._write(
            "INSERT INTO metrics(name, value, labels, ts) VALUES(?,?,?,?)",
            (name, float(value), json.dumps(labels or {}), time.time()),
        )

    @_safe(default=[])
    def metric_values(self, name: str, limit: int = 10_000) -> list[float]:
        rows = self._query(
            "SELECT value FROM metrics WHERE name=? ORDER BY ts DESC LIMIT ?", (name, limit)
        )
        return [float(r["value"]) for r in rows]

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Optional

DB_PATH = os.environ.get("LOG_DB", "/data/sms-gateway.db")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS request_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    token_name  TEXT    NOT NULL,
    method      TEXT    NOT NULL,
    endpoint    TEXT    NOT NULL,
    status_code INTEGER NOT NULL,
    recipient   TEXT
)
"""


class RequestLogger:
    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._lock = threading.Lock()
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._lock:
            with self._connect() as conn:
                conn.execute(_CREATE_TABLE)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ts    ON request_log(timestamp)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_token ON request_log(token_name)")
                conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def log(
        self,
        timestamp: str,
        token_name: str,
        method: str,
        endpoint: str,
        status_code: int,
        recipient: Optional[str] = None,
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO request_log "
                    "(timestamp, token_name, method, endpoint, status_code, recipient) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (timestamp, token_name, method, endpoint, status_code, recipient),
                )
                conn.commit()

    def get_logs(
        self,
        limit: int = 100,
        offset: int = 0,
        token_name: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        recipient: Optional[str] = None,
        endpoint: Optional[str] = None,
    ) -> tuple[list[dict], int]:
        conditions: list[str] = []
        params: list = []
        if token_name:
            conditions.append("token_name = ?")
            params.append(token_name)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp <= ?")
            params.append(until)
        if recipient:
            conditions.append("recipient = ?")
            params.append(recipient)
        if endpoint:
            conditions.append("endpoint = ?")
            params.append(endpoint)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        with self._lock:
            with self._connect() as conn:
                total: int = conn.execute(
                    f"SELECT COUNT(*) FROM request_log {where}", params
                ).fetchone()[0]
                rows = conn.execute(
                    f"SELECT id, timestamp, token_name, method, endpoint, status_code, recipient "
                    f"FROM request_log {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                    params + [limit, offset],
                ).fetchall()

        return [dict(r) for r in rows], total

    def get_token_stats(self, known_names: list[str]) -> list[dict]:
        """Per-token aggregates from the full log history."""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute("""
                    SELECT
                        token_name,
                        COUNT(*)                                                          AS request_count,
                        MAX(timestamp)                                                    AS last_used,
                        SUM(CASE WHEN endpoint = '/sms/send' AND status_code = 202
                                 THEN 1 ELSE 0 END)                                       AS sms_sent
                    FROM request_log
                    GROUP BY token_name
                """).fetchall()

        db_stats: dict[str, dict] = {r["token_name"]: dict(r) for r in rows}

        result = []
        for name in sorted(set(list(db_stats.keys()) + known_names)):
            if name in db_stats:
                s = db_stats[name]
                result.append({
                    "name": name,
                    "request_count": s["request_count"],
                    "last_used": s["last_used"],
                    "sms_sent": int(s["sms_sent"] or 0),
                })
            else:
                result.append({
                    "name": name,
                    "request_count": 0,
                    "last_used": None,
                    "sms_sent": 0,
                })
        return result

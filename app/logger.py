from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
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
    recipient   TEXT,
    client_ip   TEXT,
    api         TEXT
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
                for col in ("client_ip TEXT", "api TEXT"):
                    try:
                        conn.execute(f"ALTER TABLE request_log ADD COLUMN {col}")
                    except sqlite3.OperationalError:
                        pass  # column already exists
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
        client_ip: Optional[str] = None,
        api: Optional[str] = None,
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO request_log "
                    "(timestamp, token_name, method, endpoint, status_code, recipient, client_ip, api) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (timestamp, token_name, method, endpoint, status_code, recipient, client_ip, api),
                )
                conn.commit()

    def _since_ts(self, range_: str) -> Optional[str]:
        now = datetime.now(timezone.utc)
        if range_ == "24h":
            return (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if range_ == "7d":
            return (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if range_ == "30d":
            return (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return None  # "all"

    def get_stats(self, range_: str = "7d") -> dict:
        since = self._since_ts(range_)

        def cond(*extra: str) -> tuple[str, list]:
            parts = (["timestamp >= ?"] if since else []) + list(extra)
            where = ("WHERE " + " AND ".join(parts)) if parts else ""
            return where, ([since] if since else [])

        with self._lock:
            with self._connect() as conn:
                def q(sql: str, params: list) -> list:
                    return conn.execute(sql, params).fetchall()

                w, p = cond()
                total = q(f"SELECT COUNT(*) FROM request_log {w}", p)[0][0]

                w, p = cond("endpoint='/sms/send'", "status_code=202")
                sms_sent = q(f"SELECT COUNT(*) FROM request_log {w}", p)[0][0]

                w, p = cond("status_code >= 400")
                error_count = q(f"SELECT COUNT(*) FROM request_log {w}", p)[0][0]

                w, p = cond()
                unique_ips = q(f"SELECT COUNT(DISTINCT client_ip) FROM request_log {w}", p)[0][0]

                w, p = cond()
                by_endpoint = [dict(r) for r in q(
                    f"SELECT endpoint, COUNT(*) as cnt FROM request_log {w} "
                    f"GROUP BY endpoint ORDER BY cnt DESC LIMIT 30", p
                )]

                w, p = cond()
                by_token = [dict(r) for r in q(
                    f"SELECT token_name, COUNT(*) as cnt FROM request_log {w} "
                    f"GROUP BY token_name ORDER BY cnt DESC", p
                )]

                w, p = cond()
                by_api = [dict(r) for r in q(
                    f"SELECT COALESCE(api,'unknown') as api, COUNT(*) as cnt FROM request_log {w} "
                    f"GROUP BY api ORDER BY cnt DESC", p
                )]

                w, p = cond()
                by_ip = [dict(r) for r in q(
                    f"SELECT COALESCE(client_ip,'unknown') as client_ip, COUNT(*) as cnt "
                    f"FROM request_log {w} GROUP BY client_ip ORDER BY cnt DESC LIMIT 25", p
                )]

                w, p = cond()
                by_status = [dict(r) for r in q(
                    f"SELECT status_code, COUNT(*) as cnt FROM request_log {w} "
                    f"GROUP BY status_code ORDER BY cnt DESC", p
                )]

                w, p = cond("recipient IS NOT NULL")
                by_recipient = [dict(r) for r in q(
                    f"SELECT recipient, COUNT(*) as cnt FROM request_log {w} "
                    f"GROUP BY recipient ORDER BY cnt DESC LIMIT 20", p
                )]

                w, p = cond()
                daily = list(reversed([dict(r) for r in q(
                    f"SELECT DATE(timestamp) as date, COUNT(*) as cnt FROM request_log {w} "
                    f"GROUP BY date ORDER BY date DESC LIMIT 60", p
                )]))

                w, p = cond("endpoint='/sms/send'", "status_code=202")
                daily_sms = list(reversed([dict(r) for r in q(
                    f"SELECT DATE(timestamp) as date, COUNT(*) as cnt FROM request_log {w} "
                    f"GROUP BY date ORDER BY date DESC LIMIT 60", p
                )]))

                recent_logs = [dict(r) for r in q(
                    "SELECT id, timestamp, token_name, method, endpoint, status_code, "
                    "recipient, client_ip, api FROM request_log "
                    "ORDER BY timestamp DESC LIMIT 50",
                    []
                )]

        return {
            "range": range_,
            "total_requests": total,
            "sms_sent": sms_sent,
            "error_count": error_count,
            "unique_ips": unique_ips,
            "by_endpoint": by_endpoint,
            "by_token": by_token,
            "by_api": by_api,
            "by_ip": by_ip,
            "by_status": by_status,
            "by_recipient": by_recipient,
            "daily_requests": daily,
            "daily_sms": daily_sms,
            "recent_logs": recent_logs,
        }

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
                    f"SELECT id, timestamp, token_name, method, endpoint, status_code, "
                    f"recipient, client_ip, api "
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

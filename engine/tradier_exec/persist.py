"""Durable consumed_sends. Live 9/29 /health had consumed_send_ts=[].

SQLite for local/runtime. Postgres uses the SQL in engine.shared.gates.
Never DELETE except ET date rollover.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SQLITE_CREATE_SQL = """\
CREATE TABLE IF NOT EXISTS consumed_sends (
  session_date TEXT NOT NULL,
  send_ts REAL NOT NULL,
  dir TEXT NOT NULL,
  consumed_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (session_date, send_ts)
);
"""


class ConsumedSends:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(SQLITE_CREATE_SQL)
        self.conn.commit()

    def insert(self, session_date: str, send_ts: float, direction: str) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO consumed_sends (session_date, send_ts, dir) "
            "VALUES (?, ?, ?)",
            (session_date, float(send_ts), direction),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def load(self, session_date: str) -> list[tuple[float, str]]:
        rows = self.conn.execute(
            "SELECT send_ts, dir FROM consumed_sends WHERE session_date = ?",
            (session_date,),
        ).fetchall()
        return [(float(ts), str(d)) for ts, d in rows]

    def close(self) -> None:
        self.conn.close()

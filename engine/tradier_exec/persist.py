"""Durable consumed_sends + flatten_exits.

Live 9/29 /health had consumed_send_ts=[]. Live 10/13 dump clocks
died because get_orders is same-day and hold_watch.events reset at ET
midnight. flatten_exits survive the rollover.

SQLite for local/runtime. Postgres uses the SQL in engine.shared.gates.
Never DELETE flatten_exits on flatten or date rollover.
"""

from __future__ import annotations

import json
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

SQLITE_FLATTEN_SQL = """\
CREATE TABLE IF NOT EXISTS flatten_exits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_date TEXT NOT NULL,
  trigger_ts REAL NOT NULL,
  trigger_et TEXT NOT NULL,
  occ TEXT,
  dte INTEGER,
  qty INTEGER NOT NULL,
  fill_px REAL,
  trigger_bid REAL,
  reason TEXT NOT NULL,
  ladder_json TEXT NOT NULL,
  steps_json TEXT NOT NULL DEFAULT '[]',
  final_fill_px REAL,
  final_et TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _flatten_params(row: dict) -> tuple:
    return (
        str(row.get("session_date") or ""),
        float(row.get("trigger_ts") or 0.0),
        str(row.get("trigger_et") or ""),
        row.get("occ"),
        row.get("dte"),
        int(row.get("qty") or 0),
        row.get("fill_px"),
        row.get("trigger_bid"),
        str(row.get("reason") or ""),
        json.dumps(row.get("ladder_steps") or row.get("ladder") or []),
        json.dumps(row.get("steps") or []),
        row.get("final_fill_px"),
        row.get("final_et"),
    )


class ConsumedSends:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(SQLITE_CREATE_SQL)
        self.conn.execute(SQLITE_FLATTEN_SQL)
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

    def insert_flatten(self, row: dict) -> None:
        self.conn.execute(
            "INSERT INTO flatten_exits (session_date, trigger_ts, trigger_et, occ, "
            "dte, qty, fill_px, trigger_bid, reason, ladder_json, steps_json, "
            "final_fill_px, final_et) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _flatten_params(row),
        )
        self.conn.commit()

    def update_flatten(self, row: dict) -> None:
        """Patch steps / final fill on the latest matching trigger_ts."""
        self.conn.execute(
            "UPDATE flatten_exits SET steps_json = ?, final_fill_px = ?, "
            "final_et = ? WHERE trigger_ts = ? AND session_date = ?",
            (
                json.dumps(row.get("steps") or []),
                row.get("final_fill_px"),
                row.get("final_et"),
                float(row.get("trigger_ts") or 0.0),
                str(row.get("session_date") or ""),
            ),
        )
        self.conn.commit()

    def load_flattens(self, *, keep_days: int = 30, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT session_date, trigger_ts, trigger_et, occ, dte, qty, fill_px, "
            "trigger_bid, reason, ladder_json, steps_json, final_fill_px, final_et "
            "FROM flatten_exits WHERE created_at >= datetime('now', ?) "
            "ORDER BY trigger_ts DESC LIMIT ?",
            (f"-{int(keep_days)} days", int(limit)),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "session_date": r[0],
                    "trigger_ts": r[1],
                    "trigger_et": r[2],
                    "occ": r[3],
                    "dte": r[4],
                    "qty": r[5],
                    "fill_px": r[6],
                    "trigger_bid": r[7],
                    "reason": r[8],
                    "ladder_steps": json.loads(r[9] or "[]"),
                    "steps": json.loads(r[10] or "[]"),
                    "final_fill_px": r[11],
                    "final_et": r[12],
                }
            )
        return out

    def close(self) -> None:
        self.conn.close()

#!/usr/bin/env python3
"""Shore-up tradier_exec to GitHub cover-us gates.

Canonical source: engine/shared/gates.py and engine/tradier_exec/hooks.py.
Prefer importing engine.* on Railway. This script is the copy fallback:

    python3 shore_up_tradier_exec.py --apply
    python3 shore_up_tradier_exec.py --test

Do NOT lift session_halt on 2026-09-29.
Do NOT rewrite knobs. Wire decide_starter / decide_manage only.
SMS stays on sub_alert_send. Never BTO from admin rows.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GATES_SRC = ROOT / "engine" / "shared" / "gates.py"
if not GATES_SRC.exists():
    GATES_SRC = HERE / "gates.py"
HOOKS_NAME = "cover_us_hooks.py"
SQL_NAME = "consumed_sends.sql"

HOOKS_PY = r'''
"""Runtime hooks. tradier_exec must call these; do not bypass."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from gates import (
    SessionState,
    decide_manage,
    decide_starter,
    new_session,
)

ET = ZoneInfo("America/New_York")
SESSION_KEEP_HALT_DATE = "2026-09-29"


def today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def boot_state(loaded: SessionState | None = None) -> SessionState:
    d = today_et()
    st = loaded if loaded and loaded.session_date == d else new_session(d)
    if d == SESSION_KEEP_HALT_DATE:
        st.session_halt = True
        st.session_lost_blocks_send = True
        st.halt_lifted = True
        st.session_halt_reason = "session_loss_after_lift"
    return st


def before_bto(state: SessionState, **kwargs) -> dict:
    """Call immediately before any Tradier buy_to_open. If post is False, return."""
    return decide_starter(state, **kwargs)


def on_manage(state: SessionState, **kwargs) -> dict:
    """Call every manage tick with broker_qty, not intended qty."""
    return decide_manage(state, **kwargs)


def health_overlay(state: SessionState) -> dict:
    return {
        "gates_module": "exec/gates.py",
        "decide_starter": True,
        "skip_bounce_n": state.skip_bounce_n,
        "consumed_send_ts": list(state.consumed.keys()),
        "consumed_survives_flatten": True,
        "session_starters_n": state.session_starters_n,
        "skipped_dup_submit_n": state.skipped_dup_submit_n,
        "protect_fills_n": state.protect_fills_n,
        "ticket_risk_hits_n": state.ticket_risk_hits_n,
        "last_stc_ladder": state.last_stc_ladder,
        "session_halt": state.session_halt,
        "session_halt_reason": state.session_halt_reason,
        "session_lost_blocks_send": state.session_lost_blocks_send,
        "recover_lost_posts_bto": False,
    }
'''

SQL = """\
CREATE TABLE IF NOT EXISTS consumed_sends (
  session_date date NOT NULL,
  send_ts double precision NOT NULL,
  dir text NOT NULL,
  consumed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_date, send_ts)
);
-- Never DELETE except session_date rollover.
"""

WIRE = r"""
WIRE CONTRACT (must be in the live BTO/manage path, not comments):

1) KEEP HALT today
   session_halt = True and session_lost_blocks_send = True while session_date == 2026-09-29.
   No buy_to_open, including recover_lost.

2) BEFORE every starter BTO
   from cover_us_hooks import before_bto
   d = before_bto(state,
       send_ts=send_ts, direction=dir, send_spy=send_spy,
       spy=spy_last, bar_high=bar_high, bar_low=bar_low,
       et_hhmm=et.strftime("%H:%M"), ask=option_ask)
   if not d["post"]:
       log last_action; return
   place ONE limit BTO qty=d["qty"] client_order_id=d["client_order_id"]

3) EVERY manage tick
   from cover_us_hooks import on_manage
   m = on_manage(state, fill_px=fill, mark_bid=bid, qty=BROKER_QTY,
                 spy_adverse=spy_adverse, seconds_since_fill=age,
                 ticket_phase=phase, bid=bid)
   if m["flatten"]:
       STC ladder: bid, bid-0.05, bid-0.10, MARKET
       then state.on_flatten(realized, phase)  # MUST NOT delete consumed_sends

4) recover_lost
   state.recover_lost(broker_qty)  # STC only. NEVER BTO.

5) extra BTO
   only if state.extra_bto_ok(add_qty, mfe, mark_bid, avg_fill)

6) Postgres
   INSERT INTO consumed_sends ON CONFLICT DO NOTHING inside try_consume.
   Boot: SELECT send_ts, dir WHERE session_date = today_et.
   New ET date: new_session(today); do not carry halt from yesterday unless operator_halt.

7) /health must include health_overlay(state)
   After a real starter: consumed_send_ts nonempty and still nonempty after flatten.
   skip_bounce_n increments on $0.30-against with zero BTO.
   last_stc_ladder == "market" on envelope hit.

8) Size stays min(floor(2000/(ask*100)), 16). NEVER session_starter_cap 8.
   SMS/exec consume sub_alert_send only. admin_n >= sub_n.
"""


def apply(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if not GATES_SRC.exists():
        raise SystemExit(f"missing {GATES_SRC} — engine/shared/gates.py is required")
    shutil.copy2(GATES_SRC, dest / "gates.py")
    (dest / HOOKS_NAME).write_text(HOOKS_PY.lstrip("\n"), encoding="utf-8")
    (dest / SQL_NAME).write_text(SQL, encoding="utf-8")
    (dest / "WIRE_CONTRACT.txt").write_text(WIRE.lstrip("\n"), encoding="utf-8")
    print(f"wrote {dest / 'gates.py'}")
    print(f"wrote {dest / HOOKS_NAME}")
    print(f"wrote {dest / SQL_NAME}")
    print(f"wrote {dest / 'WIRE_CONTRACT.txt'}")
    print(WIRE)


def run_tests() -> int:
    root = HERE.parent if HERE.name == "exec" else HERE
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    loader = unittest.TestLoader()
    try:
        suite = loader.loadTestsFromName("exec.tests.test_gates")
    except Exception:
        sys.path.insert(0, str(HERE))
        suite = loader.discover(str(HERE / "tests"), pattern="test_*.py")
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if r.wasSuccessful() else 1


def find_bto_sites(root: Path) -> None:
    hits = []
    for p in root.rglob("*.py"):
        if p.name in {"gates.py", HOOKS_NAME, Path(__file__).name}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if any(k in low for k in ("buy_to_open", "buy to open", "place_option", "starter_bto")):
                hits.append(f"{p}:{i}:{line.strip()[:120]}")
    print("BTO call sites to wrap (must go through before_bto):")
    if not hits:
        print("  (none found — search the exec repo for buy_to_open and wrap them)")
        return
    for h in hits[:80]:
        print(" ", h)


def main() -> int:
    ap = argparse.ArgumentParser(description="Shore up tradier_exec to GitHub cover-us gates")
    ap.add_argument("--apply", action="store_true", help="write gates.py, hooks, SQL into --dest")
    ap.add_argument("--test", action="store_true", help="run exec.tests.test_gates")
    ap.add_argument("--find-bto", action="store_true", help="scan cwd for BTO call sites")
    ap.add_argument("--dest", default=".", help="tradier_exec directory (default: cwd)")
    args = ap.parse_args()
    if not (args.apply or args.test or args.find_bto):
        ap.print_help()
        print("\nTradier agent: run --apply then --test then --find-bto")
        print("Keep halt on 2026-09-29. Do not BTO.")
        return 2
    rc = 0
    if args.apply:
        apply(Path(args.dest).resolve())
    if args.test:
        rc = run_tests()
    if args.find_bto:
        find_bto_sites(Path(args.dest).resolve())
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

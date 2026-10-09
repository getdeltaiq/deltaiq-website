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
HOOKS_SRC = ROOT / "engine" / "tradier_exec" / "hooks.py"
if not HOOKS_SRC.exists():
    HOOKS_SRC = HERE / "hooks.py"
HOOKS_NAME = "cover_us_hooks.py"
SQL_NAME = "consumed_sends.sql"

HOOKS_PY = r'''
"""Runtime hooks. tradier_exec must call these; do not bypass."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from gates import (
    CHOP_SIZE,
    ENGINE_EXIT_MODE,
    SessionState,
    apply_manage_result,
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
    st.engine_exit_mode = ENGINE_EXIT_MODE
    st.refresh_halt()
    return st


def before_bto(state: SessionState, **kwargs) -> dict:
    """Call immediately before any Tradier buy_to_open. If post is False, return."""
    return decide_starter(state, **kwargs)


def on_manage(state: SessionState, **kwargs) -> dict:
    """Call every manage tick with broker_qty, not intended qty.

    If flatten is True, ignore trail and walk STC ladder to market.
    """
    return apply_manage_result(state, decide_manage(state, **kwargs))


def health_overlay(state: SessionState) -> dict:
    return {
        "gates_module": "exec/gates.py",
        "decide_starter": True,
        "before_bto": True,
        "on_manage": True,
        "skip_bounce_n": state.skip_bounce_n,
        "skip_quality_n": state.skip_quality_n,
        "consumed_send_ts": list(state.consumed.keys()),
        "consumed_survives_flatten": True,
        "consumed_never_delete": True,
        "session_starters_n": state.session_starters_n,
        "skipped_dup_submit_n": state.skipped_dup_submit_n,
        "protect_fills_n": state.protect_fills_n,
        "ticket_risk_hits_n": state.ticket_risk_hits_n,
        "last_stc_ladder": state.last_stc_ladder,
        "engine_exit_mode": ENGINE_EXIT_MODE,
        "override_trail": True,
        "use_trail": False,
        "chop_size": CHOP_SIZE,
        "skip_choppy_is_refuse": True,
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

2) BEFORE every starter BTO — pass 9/29 quality fields or it refuses
   from cover_us_hooks import before_bto
   d = before_bto(state,
       send_ts=send_ts, direction=dir, send_spy=send_spy,
       spy=spy_last, bar_high=bar_high, bar_low=bar_low,
       et_hhmm=et.strftime("%H:%M"), ask=option_ask,
       choppy=send.choppy, on_arm_bar=send.on_arm_bar,
       pre_move_spy=send.pre_move_spy, chase_spy=send.chase_spy,
       regime=send.regime, same_dir_age_sec=send.same_dir_age_sec,
       rip_1m_spy=send.rip_1m_spy, trend_3m_spy=send.trend_3m_spy,
       persist=lambda date, ts, direction: _insert_consumed(date, ts, direction))
   if not d["post"]:
       log last_action; return
   place ONE limit BTO qty=d["qty"] client_order_id=d["client_order_id"]
   Missing pre_move_spy or chase_spy => skip_quality_unknown (fail closed).
   Missing rip_1m_spy or trend_3m_spy => skip_1min_unconfirmed (fail closed).
   Stamp live tape with with_spy_deltas(c, last_four_1m_spy_closes) or
   signed_spy_deltas(closes). Do NOT default 1m/3m. Do NOT use
   trend_confirm_kwargs on live sends (replay only).
   1-minute print without a 3-minute trend => skip_1min_rip (no SMS, no BTO).
   Last minute not still with the send by $0.08 => skip_1min_stall.
   CHOPPY / weak 0.15–0.29 / strong pre-move ≥0.50 / chase ≥0.50 => no BTO.
   chop_size must be False. Do not resize into CHOPPY.

3) EVERY manage tick — STC only when flatten is True
   from cover_us_hooks import on_manage, before_stc
   m = on_manage(state, fill_px=fill, mark_bid=bid, qty=BROKER_QTY,
                 spy_adverse=spy_adverse, seconds_since_fill=age,
                 ticket_phase=phase, bid=bid)
   engine_exit_mode = m["engine_exit_mode"]  # MUST be ladder_to_market, never trail
   # 9/30 10:39: BTO 10 @ $1.84 then STC @ $1.83 in 3s because native code
   # posted a bid STC on fill / honored take_exit on HOLD ticks.
   on_bto_fill: state.on_bto_fill(qty, fill_px). Do NOT post STC. Do NOT
   rest a working bid/ask exit. book.take_exit leftover "bid" is ignored.
   if m["flatten"] is not True:
       take_exit = hold; post_stc = false; cancel any working STC; return
   stc = before_stc(state, fill_px=fill, mark_bid=bid, qty=BROKER_QTY,
                    spy_adverse=spy_adverse, seconds_since_fill=age,
                    ticket_phase=phase, bid=bid)
   if not stc["post"]:
       return
   if BROKER_QTY <= 0 or stc.get("reason") == "skip_already_flat":
       cancel working STC; return  # 11:04/12:02 reject spray
   STC ladder: bid, bid-0.05, bid-0.10, MARKET
   Disable native close_push / flatten_limit_thru_usd (set 0).
   then state.on_flatten(realized, phase)  # MUST NOT delete consumed_sends
   recover_lost: if last_action is recover_lost_fresh_fill, do NOT STC
   (unstamped qty stamps now — 10/8 13:20 9s hole).
   if last_action is recover_lost_owned: on_manage as normal (1DTE HOLD until $0.30).
   if last_action is recover_lost_adopt_stc_only / orphan_adopt: before_stc MUST post.

4) recover_lost
   kind = state.recover_lost(broker_qty)  # NEVER BTO.
   qty>0 and no stamp: stamp now, fresh_fill (HOLD_UNSTAMPED_FILL).
   fresh_fill: age < 15s, leave it. owned: stamped ticket, manage loop.
   adopt_stc_only: HOLD_UNSTAMPED_FILL false and no fill stamp.
   10/8 13:20 unstamped qty is a live fill, not a 90-minute orphan.
   10/2 12:52 1DTE 7-lot sat 90m because adopt after 15s on a stamped
   ticket looked like an orphan and before_stc HOLD. Owned != orphan.

5) Rec extra BTO (fill remaining room to 16 on RUN, MFE ≥ $0.20)
   Disable Railway extra_bto_on_run native path. Use gates only.
   if m.get("extra_bto") and m.get("extra_bto_qty"):
       place ONE extra BTO qty=m["extra_bto_qty"]  # same send, not a new starter
   Do not call decide_starter for the add. Do not consume a new send_ts.

6) Postgres (9/29 live consumed_send_ts was [])
   CREATE TABLE from consumed_sends.sql
   INSERT INTO consumed_sends ON CONFLICT DO NOTHING inside try_consume persist=.
   Boot: SELECT send_ts, dir WHERE session_date = today_et; state.load_consumed(rows).
   New ET date: new_session(today); do not carry halt from yesterday unless operator_halt.
   NEVER DELETE except session_date rollover.

7) Halt first-line (9/29 0-for-N never waited for a lift)
   −$750 session cash (3 envelope misses) OR 4 consecutive 0DTE FAILs halt even if halt_lifted is False.
   1DTE FAILs do not increment consecutive_fail_n.
   Pass option_symbol into before_bto (missing is skip_dte_unknown).
   1DTE without overlay TREND is skip_1dte_not_trend BEFORE 12:45 only.
   From 12:30 ET and in 10:00–10:20: overlay selects NEXT-DAY OCC
   (starter_dte_for_clock=1). TREND still required until 12:45.
   Leftover 0DTE is skip_0dte_near_cutover. Publish 09:30–15:50.
   From 12:45 ET: trade 1DTE without TREND. before_bto must POST that 1DTE.
   If option_symbol is still 0DTE after 12:45 => skip_0dte_after_cutover.
   Do not require TREND after cutover. 9/30 14:06 BEAR was skip_misfire — that is the bug.
   on_flatten(realized, phase, dte=option_dte(symbol, session_date), et_hhmm=).
   After a lift, additional −$750 / 4 fails-after-lift re-halt.
   apply_broker_session_cash(tradier option cash) every flatten.

8) /health must include health_overlay(state) and MUST match rec_book()
   rec_book=true, rec_book_ship=2026-10-09-min-hold, session_loss_halt_usd=750
   open_fade_1dte_book=true, rth_start_et=09:30, rth_end_et=15:50
   hold_unstamped_fill=true, fail_90_requires_reversal=true
   peak_giveback_min_sec=90, min_hold_sec=90
   clock_identity_lock=true, sms_at_seal=true
   clock=America/New_York unix send instant
   clock_fallback=historical_audits_only
   channels_aligned=true, sms_iff_sub_send=true, sms_from=sub_alert_send
   protective_stop_usd=0.15, protective_stop_1dte_usd=0.30, fail_sec_1dte=null
   queue_opposite=false (overwrite Railway true)
   take_exit=ladder_to_market (flatten mode only; HOLD ticks take_exit=hold)
   stc_requires_envelope=true, stc_on_bto_fill=false, working_stc_on_fill=false
   before_stc=true, post_stc=false until envelope
   fail_streak_0dte_only=true, extra_bto=true, extra_bto_fill_to=16
   skip_1dte_not_trend=true (morning only), dte_book_et=12:30,
   trade_1dte_from_book_et=true, dte_cutover_et=12:45,
   skip_0dte_after_cutover=true, trade_1dte_after_cutover=true,
   skip_0dte_open_fade=true (10:00–10:20 0DTE), skip_0dte_near_cutover=true (12:30–12:44 0DTE),
   skip_already_flat=true, flatten_limit_thru_usd=0,
   recover_lost_owned=true, orphan_adopt_flattens=true,
   skip_1min_rip=true, one_min_rip_usd=0.20, one_min_climax_frac=0.80,
   one_min_climax_usd=0.50, trend_3m_min_usd=0.20,
   skip_1min_stall=true, stall_1m_usd=0.08,
   protect_from_high=true, peak_giveback_usd=50, peak_giveback_min_sec=90,
   cooldown_after_fail_sec=480
   engine_exit_mode == ladder_to_market (not trail).
   After a real starter: consumed_send_ts nonempty and still nonempty after flatten.
   skip_bounce_n increments on $0.30-against with zero BTO.
   skip_quality_n increments on CHOPPY / weak / chase / unknown / open_fade / near_cutover.
   skip_misfire_n increments on 1DTE-not-trend / cooldown / missing OCC.
   last_stc_ladder == "market" on envelope hit.
   0DTE −$0.15 still flatten_protective. 0DTE fail_90 only if SPY reversed $0.30.
   1DTE −$0.21 must HOLD. 1DTE 90s FAIL must HOLD. Unstamped fill is fresh_fill.
   A 16-lot 0.79 marked 1.67 HOLDs. Marked 1.63 after 90s is flatten_peak_giveback.
   A 13-lot 1.17→1.10 at 62s with peak $78 HOLDs. Same at 90s is peak_giveback.
   chop_size == false. protect_fills_n increments on $0.15 down or peak_giveback.
   A 1-cent bid scratch in <90s must NOT increment protect_fills_n.

9) Size stays min(floor(2000/(ask*100)), 16). NEVER session_starter_cap 8.
   SMS/exec consume sub_alert_send only. admin_n >= sub_n.
   Overlay skip_reason and before_bto share sub_action_skip_reason.
   If overlay SMS, Tradier BTO that send (bounce/halt/inflight stay exec-only).
   aligned_copy=false is SMS body, not a non-trade.
   clock_identity_lock: SMS publication, overlay label, strike copy, and
   Tradier consume one unix send_ts. Do not use legacy_ledger_hhmm live.
   SMS only at seal (decide_sub_send send=True).
   Rec scenario is the production book. Do not keep a parallel knob set.
"""


def apply(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if not GATES_SRC.exists():
        raise SystemExit(f"missing {GATES_SRC} — engine/shared/gates.py is required")
    shutil.copy2(GATES_SRC, dest / "gates.py")
    hooks = HOOKS_SRC.read_text(encoding="utf-8")
    hooks = hooks.replace("from engine.shared.gates import", "from gates import")
    (dest / HOOKS_NAME).write_text(hooks, encoding="utf-8")
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

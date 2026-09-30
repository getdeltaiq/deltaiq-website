"""Last-week cover-us backtest. Lots only — no account ids, no broker calls.

Live 9/22–9/28 from closed option lots (newest-first as returned).
Live 9/29 from fixtures/2026-09-29-orders.json.

Cover-us is envelope + first-line halt + consume-once. Quality flags for
9/22–9/28 were never on the BTO path, so they are not applied here.
"""

from __future__ import annotations

import json
from pathlib import Path

from engine.shared.gates import (
    CONSECUTIVE_FAIL_HALT,
    PROTECTIVE_STOP_USD,
    SESSION_LOSS_HALT_USD,
    TICKET_RISK_USD,
    counts_toward_fail_streak,
    option_dte,
    starter_qty,
)

FIXTURE = Path(__file__).parent / "fixtures" / "week-2026-09-22-lots.json"

# Equity EOD (option cash + marks). 9/29 is live total equity after the session.
LIVE_EOD = {
    "2026-09-22": 7590.38,
    "2026-09-23": 6112.68,
    "2026-09-24": 6739.83,
    "2026-09-25": 6902.66,
    "2026-09-28": 6948.02,
    "2026-09-29": 5074.99,
}


def load_lots() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def envelope_lot(qty: float, cost: float, proceeds: float) -> tuple[float, bool]:
    """Keep winners. Cap losers at protective $0.15 / ticket_risk −$240."""
    live = round(proceeds - cost, 2)
    if live >= 0:
        return live, False
    cap = max(-PROTECTIVE_STOP_USD * qty * 100.0, -TICKET_RISK_USD)
    return round(max(live, cap), 2), True


def walk_session(
    lots: list[dict],
    *,
    oldest_first: bool = True,
    session_date: str | None = None,
) -> dict:
    ordered = list(reversed(lots)) if oldest_first else list(lots)
    session = 0.0
    fails = 0
    kept: list[dict] = []
    skipped: list[dict] = []
    halted = False
    halt_reason = "none"
    wins_kept = 0.0
    wins_skipped = 0.0
    losses_kept = 0.0
    losses_skipped = 0.0

    for lot in ordered:
        live = round(lot["proceeds"] - lot["cost"], 2)
        env, is_fail = envelope_lot(lot["qty"], lot["cost"], lot["proceeds"])
        dte = option_dte(lot.get("symbol"), session_date or lot.get("session_date") or "")
        row = {**lot, "live": live, "env": env, "fail": is_fail, "dte": dte}
        if halted:
            skipped.append(row)
            if live >= 0:
                wins_skipped += live
            else:
                losses_skipped += live
            continue
        session = round(session + env, 2)
        kept.append(row)
        if live >= 0:
            wins_kept += live
        else:
            losses_kept += env
        if counts_toward_fail_streak(dte):
            if is_fail:
                fails += 1
            else:
                fails = 0
        if session <= -SESSION_LOSS_HALT_USD:
            halted = True
            halt_reason = "session_loss"
        elif fails >= CONSECUTIVE_FAIL_HALT:
            halted = True
            halt_reason = "consecutive_fail"

    live_sum = round(sum(round(x["proceeds"] - x["cost"], 2) for x in lots), 2)
    return {
        "live_lots": live_sum,
        "cover": session,
        "halted": halted,
        "halt_reason": halt_reason,
        "n_kept": len(kept),
        "n_skipped": len(skipped),
        "wins_kept": round(wins_kept, 2),
        "wins_skipped": round(wins_skipped, 2),
        "losses_kept": round(losses_kept, 2),
        "losses_skipped": round(losses_skipped, 2),
        "kept": kept,
        "skipped": skipped,
    }


def replay_929(*, bounce_open: bool, cap_envelope: bool = True) -> dict:
    """Mutex/consume/halt on the 9/29 tape. Optionally cap STC at $0.15 down."""
    from engine.tradier_exec.replay import load_fixture, _et, _send_for, CLEAN_Q
    from engine.tradier_exec.runtime import CoverUsExec

    fx = load_fixture()
    overlay = fx["overlay_sends"]
    recycles = set(fx["fail_recycle_et"])
    ex = CoverUsExec(":memory:", fx["session_date"], keep_halt=False)
    avg_fill = 0.0
    broker = 0
    posted_pnl: list[float] = []

    for od in fx["orders"]:
        ts = _et(od["utc"])
        hhmm = ts.strftime("%H:%M")
        send_ts, direction, send_spy = _send_for(hhmm, overlay, recycles)
        spy = send_spy
        bar_high, bar_low = send_spy + 0.02, send_spy - 0.08
        if bounce_open and hhmm == "10:00":
            spy = 765.31
            bar_high = 765.63

        if od["side"] == "buy_to_open":
            d = ex.starter(
                send_ts=send_ts,
                direction=direction,
                send_spy=send_spy,
                spy=spy,
                bar_high=bar_high,
                bar_low=bar_low,
                et_hhmm=hhmm,
                ask=float(od["price"]),
                **CLEAN_Q,
            )
            if d.get("post"):
                broker = int(d["qty"])
                avg_fill = float(od["price"])
                ex.on_bto_fill(broker, avg_fill)
            continue

        if broker <= 0:
            continue
        stc_px = od["price"]
        if stc_px is None:
            stc_px = round(avg_fill - PROTECTIVE_STOP_USD, 2)
        stc_px = float(stc_px)
        if cap_envelope and avg_fill - stc_px >= PROTECTIVE_STOP_USD:
            stc_px = round(avg_fill - PROTECTIVE_STOP_USD, 2)
        ex.manage(
            fill_px=avg_fill,
            mark_bid=stc_px,
            qty=broker,
            spy_adverse=0.20,
            seconds_since_fill=12.0,
            ticket_phase="FAIL",
            bid=stc_px,
        )
        realized = round((stc_px - avg_fill) * 100.0 * broker, 2)
        posted_pnl.append(realized)
        ex.flatten(realized, "FAIL")
        broker = 0
        avg_fill = 0.0

    posted = [x for x in ex.log if x.get("kind") == "starter" and x.get("post")]
    return {
        "cover": ex.state.session_realized_usd,
        "halted": ex.state.session_halt,
        "halt_reason": ex.state.session_halt_reason,
        "n_kept": len(posted),
        "qty": [x.get("qty") for x in posted],
        "posted_pnl": posted_pnl,
        "starters_n": ex.state.session_starters_n,
    }


def live_session_pnl() -> dict[str, float]:
    dates = list(LIVE_EOD)
    out: dict[str, float] = {}
    for i, d in enumerate(dates):
        if i == 0:
            continue
        prev = dates[i - 1]
        out[d] = round(LIVE_EOD[d] - LIVE_EOD[prev], 2)
    return out


def week_summary() -> dict:
    data = load_lots()
    live_eq = live_session_pnl()
    days = {}
    cover_sum = 0.0
    live_sum = 0.0
    wins_skipped = 0.0

    for day, lots in data["days"].items():
        w = walk_session(lots, oldest_first=True, session_date=day)
        live = live_eq.get(day, w["live_lots"])
        days[day] = {
            "live_equity": live,
            "live_lots": w["live_lots"],
            "cover": w["cover"],
            "halt_reason": w["halt_reason"],
            "n_kept": w["n_kept"],
            "n_skipped": w["n_skipped"],
            "wins_kept": w["wins_kept"],
            "wins_skipped": w["wins_skipped"],
        }
        cover_sum += w["cover"]
        live_sum += live
        wins_skipped += w["wins_skipped"]

    r_nb = replay_929(bounce_open=False, cap_envelope=True)
    r_b = replay_929(bounce_open=True, cap_envelope=True)
    days["2026-09-29"] = {
        "live_equity": live_eq["2026-09-29"],
        "live_lots": live_eq["2026-09-29"],
        "cover": r_b["cover"],
        "cover_no_bounce": r_nb["cover"],
        "halt_reason": r_b["halt_reason"],
        "n_kept": r_b["n_kept"],
        "n_skipped": None,
        "wins_kept": 0.0,
        "wins_skipped": 0.0,
    }
    cover_sum += r_b["cover"]
    live_sum += live_eq["2026-09-29"]

    return {
        "days": days,
        "live_week": round(live_sum, 2),
        "cover_week": round(cover_sum, 2),
        "wins_skipped_usd": round(wins_skipped, 2),
        "929_bounce": r_b,
        "929_no_bounce": r_nb,
    }


def main() -> int:
    s = week_summary()
    print("day          live_eq   lots     cover  halt              kept/skip  wins_skipped")
    for day, d in s["days"].items():
        skip = d["n_skipped"] if d["n_skipped"] is not None else "-"
        print(
            f"{day}  {d['live_equity']:8.0f} {d['live_lots']:8.0f} {d['cover']:8.0f}  "
            f"{d['halt_reason']:16} {d['n_kept']}/{skip}  {d['wins_skipped']:8.0f}"
        )
    print("week live", s["live_week"], "cover", s["cover_week"], "wins_skipped", s["wins_skipped_usd"])
    print("929 bounce", s["929_bounce"])
    print("929 no_bounce", s["929_no_bounce"])
    print("starter_qty 1.36", starter_qty(1.36))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

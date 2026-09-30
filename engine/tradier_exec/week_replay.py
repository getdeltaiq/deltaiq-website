"""Weekly Rec vs live recap. Lots only — no account ids, no broker calls.

30 trading days through 9/29, rolled to Mon–Fri weeks.
Live 9/15–9/28 from closed option lots (newest-first as returned).
Live 9/29 from fixtures/2026-09-29-orders.json. Earlier sessions have no
SPY lots; Rec equals live (impact $0). 9/11 $7,000 cash-in is stripped.

Rec = envelope + first-line halt + extra BTO on RUN (fill to 16) +
skip_1dte_not_trend except TREND (9/25 runner).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from engine.shared.gates import (
    CONSECUTIVE_FAIL_HALT,
    PROTECTIVE_STOP_USD,
    SESSION_LOSS_HALT_USD,
    TICKET_RISK_USD,
    counts_toward_fail_streak,
    extra_bto_qty,
    option_dte,
)

FIXTURE = Path(__file__).parent / "fixtures" / "week-2026-09-22-lots.json"
FIXTURE_BEFORE = Path(__file__).parent / "fixtures" / "week-2026-09-15-lots.json"

# 9/25 1DTE runner was TREND. Other sessions: missing regime is a 1DTE refuse.
TREND_SESSIONS = frozenset({"2026-09-25"})

# Equity EOD (option cash + marks). 8/17 is the close before the 30-session window.
# 9/29 is live total equity after that session (MONTH curve stops at 9/28).
LIVE_EOD = {
    "2026-08-17": 554.93,
    "2026-08-18": 544.77,
    "2026-08-19": 542.49,
    "2026-08-20": 540.75,
    "2026-08-21": 536.76,
    "2026-08-24": 525.10,
    "2026-08-25": 534.89,
    "2026-08-26": 527.29,
    "2026-08-27": 564.79,
    "2026-08-28": 545.36,
    "2026-08-31": 550.25,
    "2026-09-01": 542.37,
    "2026-09-02": 557.13,
    "2026-09-03": 566.40,
    "2026-09-04": 568.56,
    "2026-09-08": 556.82,
    "2026-09-09": 551.62,
    "2026-09-10": 539.89,
    "2026-09-11": 7539.75,
    "2026-09-14": 7525.09,
    "2026-09-15": 7366.46,
    "2026-09-16": 7392.40,
    "2026-09-17": 7396.17,
    "2026-09-18": 7024.00,
    "2026-09-21": 7543.07,
    "2026-09-22": 7590.38,
    "2026-09-23": 6112.68,
    "2026-09-24": 6739.83,
    "2026-09-25": 6902.66,
    "2026-09-28": 6948.02,
    "2026-09-29": 5074.99,
}

# 9/11 equity 539.89 → 7539.75. Strip $7,000 funding so the week is trading P&L.
FUNDING_USD = {"2026-09-11": 7000.0}
WINDOW_END = "2026-09-29"
WINDOW_SESSIONS = 30

WEEK_BEFORE_DAYS = (
    "2026-09-15",
    "2026-09-16",
    "2026-09-17",
    "2026-09-18",
    "2026-09-21",
)
LAST_WEEK_DAYS = (
    "2026-09-22",
    "2026-09-23",
    "2026-09-24",
    "2026-09-25",
    "2026-09-28",
    "2026-09-29",
)


def load_lots() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def load_lots_before() -> dict:
    return json.loads(FIXTURE_BEFORE.read_text(encoding="utf-8"))


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
    from engine.shared.gates import past_dte_cutover

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
            q_tick = dict(CLEAN_Q)
            if past_dte_cutover(hhmm) and q_tick.get("option_symbol"):
                q_tick["option_symbol"] = q_tick["option_symbol"].replace(
                    "260929", "260930"
                )
            d = ex.starter(
                send_ts=send_ts,
                direction=direction,
                send_spy=send_spy,
                spy=spy,
                bar_high=bar_high,
                bar_low=bar_low,
                et_hhmm=hhmm,
                ask=float(od["price"]),
                **q_tick,
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
        ex.flatten(realized, "FAIL", et_hhmm=hhmm)
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


def skip_1dte_lots(
    lots: list[dict],
    session_date: str,
    *,
    trend_days: frozenset[str] = TREND_SESSIONS,
) -> tuple[list[dict], list[dict]]:
    """Refuse 1DTE unless the session is a known TREND day (9/25 runner)."""
    if session_date in trend_days:
        return list(lots), []
    kept: list[dict] = []
    skipped: list[dict] = []
    for lot in lots:
        dte = option_dte(lot.get("symbol"), session_date)
        if dte is not None and dte >= 1:
            skipped.append(lot)
        else:
            kept.append(lot)
    return kept, skipped


def extra_bto_on_kept(kept_rows: list[dict]) -> tuple[float, int]:
    """Fill remaining room to 16 on kept winners at the same ROP."""
    add = 0.0
    n = 0
    for row in kept_rows:
        live = float(row["live"])
        if live <= 0:
            continue
        qty = int(row["qty"])
        extra = extra_bto_qty(qty)
        if extra <= 0:
            continue
        add += extra * (live / qty)
        n += 1
    return round(add, 2), n


def recap_session(
    lots: list[dict],
    session_date: str,
    *,
    skip_1dte: bool = True,
) -> dict:
    tradable, skipped_1dte = (
        skip_1dte_lots(lots, session_date) if skip_1dte else (list(lots), [])
    )
    w = walk_session(tradable, oldest_first=True, session_date=session_date)
    xbto, n_xbto = extra_bto_on_kept(w["kept"])
    skip_live = round(
        sum(round(x["proceeds"] - x["cost"], 2) for x in skipped_1dte), 2
    )
    return {
        "live_lots": w["live_lots"],
        "cover": w["cover"],
        "xbto": xbto,
        "n_xbto": n_xbto,
        "rec": round(w["cover"] + xbto, 2),
        "halt_reason": w["halt_reason"],
        "n_kept": w["n_kept"],
        "n_skipped": w["n_skipped"],
        "n_skip_1dte": len(skipped_1dte),
        "skip_1dte_live": skip_live,
        "wins_kept": w["wins_kept"],
        "wins_skipped": w["wins_skipped"],
        "cover_all_dte": walk_session(
            lots, oldest_first=True, session_date=session_date
        )["cover"],
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


def live_trading_pnl() -> dict[str, float]:
    """Session equity change with cash-in stripped. Rec does not rewrite funding."""
    raw = live_session_pnl()
    return {d: round(pnl - FUNDING_USD.get(d, 0.0), 2) for d, pnl in raw.items()}


def window_sessions(end: str = WINDOW_END, n: int = WINDOW_SESSIONS) -> list[str]:
    days = [d for d in LIVE_EOD if d <= end]
    if days and days[0] == min(LIVE_EOD):
        days = days[1:]  # drop the prior-close anchor
    if len(days) < n:
        raise ValueError(f"need {n} sessions through {end}, have {len(days)}")
    return days[-n:]


def _monday(day: str) -> str:
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def _week_label(days: list[str]) -> str:
    a = date.fromisoformat(days[0])
    b = date.fromisoformat(days[-1])
    if a.month == b.month:
        return f"{a.strftime('%b')} {a.day}–{b.day}"
    return f"{a.strftime('%b')} {a.day}–{b.strftime('%b')} {b.day}"


def _session_rec(day: str, live_eq: dict[str, float], lots_by_day: dict[str, list]) -> dict:
    """Rec on SPY lots; days with no Rec tape keep live trading P&L (impact $0)."""
    live = live_eq[day]
    if day == "2026-09-29":
        r = replay_929(bounce_open=True, cap_envelope=True)
        rec = r["cover"]
        return {
            "live": live,
            "rec": rec,
            "impact": round(rec - live, 2),
            "has_rec_tape": True,
        }
    lots = lots_by_day.get(day)
    if not lots:
        return {"live": live, "rec": live, "impact": 0.0, "has_rec_tape": False}
    rec = recap_session(lots, day, skip_1dte=True)["rec"]
    return {
        "live": live,
        "rec": rec,
        "impact": round(rec - live, 2),
        "has_rec_tape": True,
    }


def _load_all_lots() -> dict[str, list]:
    out: dict[str, list] = {}
    out.update(load_lots_before()["days"])
    out.update(load_lots()["days"])
    return out


def weekly_impact() -> dict:
    """30 trading days through 9/29, rolled to Mon–Fri weeks. No daily rows."""
    live_eq = live_trading_pnl()
    lots_by_day = _load_all_lots()
    sessions = window_sessions()
    buckets: dict[str, list[str]] = {}
    for day in sessions:
        buckets.setdefault(_monday(day), []).append(day)

    weeks = []
    for monday, days in buckets.items():
        rows = [_session_rec(d, live_eq, lots_by_day) for d in days]
        live = round(sum(r["live"] for r in rows), 2)
        rec = round(sum(r["rec"] for r in rows), 2)
        impact = round(rec - live, 2)
        weeks.append(
            {
                "week": _week_label(days),
                "monday": monday,
                "sessions": len(days),
                "live": live,
                "rec": rec,
                "impact": impact,
                "result": "win" if impact > 0 else ("lose" if impact < 0 else "flat"),
                "rec_tape": any(r["has_rec_tape"] for r in rows),
            }
        )

    live_sum = round(sum(w["live"] for w in weeks), 2)
    rec_sum = round(sum(w["rec"] for w in weeks), 2)
    impact_sum = round(rec_sum - live_sum, 2)
    return {
        "window": f"{sessions[0]} → {sessions[-1]}",
        "sessions": len(sessions),
        "weeks": weeks,
        "live": live_sum,
        "rec": rec_sum,
        "impact": impact_sum,
        "result": "win" if impact_sum > 0 else ("lose" if impact_sum < 0 else "flat"),
    }


def _day_row(day: str, rec: dict, live_eq: dict[str, float]) -> dict:
    live = live_eq.get(day, rec["live_lots"])
    return {
        "live_equity": live,
        "live_lots": rec["live_lots"],
        "cover": rec["cover"],
        "cover_all_dte": rec["cover_all_dte"],
        "xbto": rec["xbto"],
        "rec": rec["rec"],
        "halt_reason": rec["halt_reason"],
        "n_kept": rec["n_kept"],
        "n_skipped": rec["n_skipped"],
        "n_skip_1dte": rec["n_skip_1dte"],
        "skip_1dte_live": rec["skip_1dte_live"],
        "wins_kept": rec["wins_kept"],
        "wins_skipped": rec["wins_skipped"],
    }


def two_week_recap() -> dict:
    """Envelope + 0DTE fail-streak + extra BTO + 1DTE skip except TREND."""
    live_eq = live_session_pnl()
    before_lots = load_lots_before()["days"]
    last_lots = load_lots()["days"]
    before: dict[str, dict] = {}
    last: dict[str, dict] = {}
    for day in WEEK_BEFORE_DAYS:
        rec = recap_session(before_lots[day], day, skip_1dte=True)
        before[day] = _day_row(day, rec, live_eq)
    for day in LAST_WEEK_DAYS:
        if day == "2026-09-29":
            continue
        rec = recap_session(last_lots[day], day, skip_1dte=True)
        last[day] = _day_row(day, rec, live_eq)
    r_nb = replay_929(bounce_open=False, cap_envelope=True)
    r_b = replay_929(bounce_open=True, cap_envelope=True)
    last["2026-09-29"] = {
        "live_equity": live_eq["2026-09-29"],
        "live_lots": live_eq["2026-09-29"],
        "cover": r_b["cover"],
        "cover_all_dte": r_b["cover"],
        "cover_no_bounce": r_nb["cover"],
        "xbto": 0.0,
        "rec": r_b["cover"],
        "halt_reason": r_b["halt_reason"],
        "n_kept": r_b["n_kept"],
        "n_skipped": None,
        "n_skip_1dte": 0,
        "skip_1dte_live": 0.0,
        "wins_kept": 0.0,
        "wins_skipped": 0.0,
    }
    return {
        "week_before": before,
        "last_week": last,
        "week_before_live": round(sum(d["live_equity"] for d in before.values()), 2),
        "week_before_rec": round(sum(d["rec"] for d in before.values()), 2),
        "last_week_live": round(sum(d["live_equity"] for d in last.values()), 2),
        "last_week_rec": round(sum(d["rec"] for d in last.values()), 2),
        "two_week_rec": round(
            sum(d["rec"] for d in before.values()) + sum(d["rec"] for d in last.values()),
            2,
        ),
        "929_bounce": r_b,
        "929_no_bounce": r_nb,
    }


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


def _print_week(title: str, days: dict, live_sum: float, rec_sum: float) -> None:
    print(title)
    print(
        "day          live_eq  lots   cover  xbto    rec  halt             "
        "kept/skip  skip_1dte"
    )
    for day, d in days.items():
        skip = d["n_skipped"] if d["n_skipped"] is not None else "-"
        print(
            f"{day}  {d['live_equity']:8.0f} {d['live_lots']:6.0f} {d['cover']:7.0f} "
            f"{d['xbto']:5.0f} {d['rec']:7.0f}  {d['halt_reason']:16} "
            f"{d['n_kept']}/{skip}  {d['n_skip_1dte']}"
        )
    print(f"  week live {live_sum:.0f}  rec {rec_sum:.0f}")
    print()


def _print_weekly(w: dict) -> None:
    print(f"Rec vs live  {w['window']}  ({w['sessions']} sessions)")
    print("week              sess      live       rec    impact")
    for row in w["weeks"]:
        print(
            f"{row['week']:<16}  {row['sessions']:4}  {row['live']:8.0f}  "
            f"{row['rec']:8.0f}  {row['impact']:+8.0f}  {row['result']}"
        )
    print(
        f"{'30-day':<16}  {w['sessions']:4}  {w['live']:8.0f}  "
        f"{w['rec']:8.0f}  {w['impact']:+8.0f}  {w['result']}"
    )


def main() -> int:
    _print_weekly(weekly_impact())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

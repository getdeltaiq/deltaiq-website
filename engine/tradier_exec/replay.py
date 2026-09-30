"""Replay 2026-09-29 filled tickets through cover-us gates. No broker orders."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from engine.tradier_exec.runtime import CoverUsExec

ET = ZoneInfo("America/New_York")
FIXTURE = Path(__file__).parent / "fixtures" / "2026-09-29-orders.json"

# 9/29 fail-closed requires these. Structure replay uses a tradable band
# so mutex / consume / halt can fire. Quality replay omits them.
CLEAN_Q = dict(
    choppy=False,
    on_arm_bar=False,
    pre_move_spy=0.40,
    chase_spy=0.10,
    regime="TREND",
    plot="sub_alert_send",
    overlay_queued=True,
    is_opposite=False,
    option_symbol="SPY260929P00765000",
)


def _et(utc: str) -> datetime:
    return datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(ET)


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _send_for(et_hhmm: str, overlay: list[dict], recycles: set[str]) -> tuple[float, str, float]:
    """Map a ticket time to overlay send_ts / direction / spy. Recycles share 10:00."""
    by_et = {r["et"]: r for r in overlay}
    key = et_hhmm
    if key in recycles:
        row = by_et["10:00"]
        return 10.00, row["direction"], row["spy"]
    if key in by_et:
        row = by_et[key]
        return float(key.replace(":", ".")), row["direction"], row["spy"]
    # Halt-leak extras (15:32) — not an overlay send.
    nearest = min(by_et, key=lambda t: abs(int(t[:2]) * 60 + int(t[3:]) - (int(key[:2]) * 60 + int(key[3:]))))
    row = by_et[nearest]
    return float(key.replace(":", ".")), row["direction"], row["spy"]


def replay(
    *,
    keep_halt: bool = False,
    bounce_open: bool = False,
    quality: dict | None = None,
    db_path: str = ":memory:",
) -> CoverUsExec:
    fx = load_fixture()
    overlay = fx["overlay_sends"]
    recycles = set(fx["fail_recycle_et"])
    q = CLEAN_Q if quality is None else quality
    ex = CoverUsExec(db_path, fx["session_date"], keep_halt=keep_halt)
    avg_fill = 0.0
    broker = 0

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
                **q,
            )
            if d.get("post"):
                fill_qty = int(d["qty"])
                # Broker filled the posted size; never add a second starter.
                broker = fill_qty
                avg_fill = float(od["price"])
                ex.on_bto_fill(broker, avg_fill)
            continue

        if broker <= 0:
            ex.log.append({"kind": "stc_skip_flat", "et": hhmm})
            continue
        stc_px = od["price"]
        if stc_px is None:
            stc_px = round(avg_fill - 0.15, 2)
        m = ex.manage(
            fill_px=avg_fill,
            mark_bid=float(stc_px),
            qty=broker,
            spy_adverse=0.20,
            seconds_since_fill=12.0,
            ticket_phase="FAIL",
            bid=float(stc_px),
        )
        realized = round((float(stc_px) - avg_fill) * 100.0 * broker, 2)
        ex.flatten(realized, "FAIL", et_hhmm=hhmm)
        broker = 0
        avg_fill = 0.0

    return ex


def main() -> int:
    ex = replay(keep_halt=False)
    posted = [x for x in ex.log if x.get("kind") == "starter" and x.get("post")]
    skipped = [x for x in ex.log if x.get("kind") == "starter" and not x.get("post")]
    print("posted", len(posted), "skipped", len(skipped))
    print("session_realized_usd", ex.state.session_realized_usd)
    print("halt", ex.state.session_halt, ex.state.session_halt_reason)
    print("consumed", list(ex.state.consumed.keys()))
    print("starters_n", ex.state.session_starters_n)
    print("skip_quality_n", ex.state.skip_quality_n)
    print("health_exit", ex.health()["engine_exit_mode"])
    for row in skipped:
        print(" skip", row.get("action"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

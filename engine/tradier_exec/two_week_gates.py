"""Replay climax + 12:30 1DTE book on two weeks of SPY 1-minute closes.

Yahoo RTH tape 2026-09-24 through 2026-10-08. No account ids. No broker
orders. Each bar is TREND, not choppy, chase $0.10. OCC tenor is
starter_dte_for_clock. This is the skip stack, not inflight / consume-once.

Oct 8 tape ends 13:47 ET (session still open when the fixture was pulled).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from engine.overlay.publish import Candidate, decide_sub_send, in_publish_window, with_spy_deltas
from engine.shared.gates import (
    ONE_MIN_RIP_USD,
    STALL_1M_USD,
    TREND_3M_MIN_USD,
    past_dte_cutover,
    signed_spy_deltas,
    starter_dte_for_clock,
)

FIXTURE = Path(__file__).parent / "fixtures" / "spy-1m-2026-09-24-2026-10-08.json"
SESSIONS = (
    "2026-09-24",
    "2026-09-25",
    "2026-09-28",
    "2026-09-29",
    "2026-09-30",
    "2026-10-01",
    "2026-10-02",
    "2026-10-05",
    "2026-10-06",
    "2026-10-07",
    "2026-10-08",
)


def load_tape() -> dict[str, list[dict]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _candidate(et_hhmm: str, spy: float, four: list[float], dte: int) -> Candidate:
    dlt = signed_spy_deltas(four)
    direction = "BULL" if (dlt["trend_3m_spy"] or 0) >= 0 else "BEAR"
    c = Candidate(
        ts=1.0,
        direction=direction,
        spy=spy,
        pre_move_spy=0.40,
        armed=True,
        choppy=False,
        on_arm_bar=False,
        chase_spy=0.10,
        et_hhmm=et_hhmm,
        dte=dte,
        regime="TREND",
        rip_1m_spy=None,
        trend_3m_spy=None,
    )
    return with_spy_deltas(c, four)


def bar_row(
    day: str,
    et_hhmm: str,
    four: list[float],
    spy: float,
    *,
    dte_fn: Callable[[str], int] = starter_dte_for_clock,
) -> dict:
    dte = dte_fn(et_hhmm)
    c = _candidate(et_hhmm, spy, four, dte)
    d = decide_sub_send(c)
    r1 = c.rip_1m_spy
    t3 = c.trend_3m_spy
    frac = abs(r1) / abs(t3) if r1 is not None and t3 else None
    return {
        "day": day,
        "et": et_hhmm,
        "dir": c.direction,
        "dte": dte,
        "spy": spy,
        "rip_1m_spy": r1,
        "trend_3m_spy": t3,
        "frac": frac,
        "send": d["send"],
        "reason": d["reason"],
    }


def walk_tape(
    tape: dict[str, list[dict]] | None = None,
    *,
    dte_fn: Callable[[str], int] = starter_dte_for_clock,
) -> list[dict]:
    tape = tape if tape is not None else load_tape()
    rows: list[dict] = []
    for day, bars in tape.items():
        for i, bar in enumerate(bars):
            et = bar["t"]
            if not in_publish_window(et) or i < 3:
                continue
            four = [bars[i - 3]["c"], bars[i - 2]["c"], bars[i - 1]["c"], bar["c"]]
            rows.append(bar_row(day, et, four, bar["c"], dte_fn=dte_fn))
    return rows


def sends(rows: list[dict] | None = None) -> list[dict]:
    rows = rows if rows is not None else walk_tape()
    return [r for r in rows if r["send"]]


def lookup(rows: list[dict], day: str, et: str) -> dict | None:
    for r in rows:
        if r["day"] == day and r["et"] == et:
            return r
    return None


def _old_dte(et_hhmm: str) -> int:
    return 1 if past_dte_cutover(et_hhmm) else 0


def _old_rip_allows(r1: float, t3: float, direction: str) -> bool:
    """Stall + 100% one-bar rip only. No 80% / $0.50 climax."""
    want = 1.0 if direction == "BULL" else -1.0
    if t3 * want <= 0 or abs(t3) < TREND_3M_MIN_USD:
        return False
    if abs(r1) >= abs(t3) - 1e-9 and abs(r1) >= ONE_MIN_RIP_USD:
        return False
    if abs(r1) > abs(t3):
        return False
    if r1 * want < STALL_1M_USD - 1e-9:
        return False
    return True


def replay_vs_pre_climax_book(tape: dict[str, list[dict]] | None = None) -> dict:
    """Current book vs stall-1m (1DTE from 12:45, no 80% climax)."""
    tape = tape if tape is not None else load_tape()
    new_rows = walk_tape(tape)
    old_rows = walk_tape(tape, dte_fn=_old_dte)
    old_sends: list[dict] = []
    for r in old_rows:
        if r["rip_1m_spy"] is None or r["trend_3m_spy"] is None:
            continue
        clock_ok = r["reason"] not in {
            "skip_0dte_open_fade",
            "skip_0dte_near_cutover",
            "skip_0dte_after_cutover",
            "skip_1dte_not_trend",
            "skip_outside_window",
            "skip_dte_unknown",
        }
        if r["dte"] == 0 and "12:30" <= r["et"] < "12:45":
            clock_ok = False
        if not clock_ok:
            continue
        if _old_rip_allows(r["rip_1m_spy"], r["trend_3m_spy"], r["dir"]):
            old_sends.append({**r, "send": True, "reason": "sub_alert_send"})
    new_sends = [r for r in new_rows if r["send"]]
    new_keys = {(r["day"], r["et"], r["dir"]) for r in new_sends}
    old_keys = {(r["day"], r["et"], r["dir"]) for r in old_sends}
    added = [r for r in new_sends if (r["day"], r["et"], r["dir"]) not in old_keys]
    removed = [r for r in old_sends if (r["day"], r["et"], r["dir"]) not in new_keys]
    return {
        "sessions": list(tape),
        "new_send_n": len(new_sends),
        "old_send_n": len(old_sends),
        "added": added,
        "removed": removed,
        "new_sends": new_sends,
        "rows": new_rows,
    }


def main() -> None:
    d = replay_vs_pre_climax_book()
    print(
        "sessions",
        len(d["sessions"]),
        "old_sends",
        d["old_send_n"],
        "new_sends",
        d["new_send_n"],
        "added",
        len(d["added"]),
        "removed",
        len(d["removed"]),
    )
    print("removed climax/clock:")
    for r in d["removed"]:
        print(
            f"  {r['day']} {r['et']} {r['dir']} dte={r['dte']} "
            f"1m={r['rip_1m_spy']:+.3f} 3m={r['trend_3m_spy']:+.3f}"
        )
    print("added 12:30 1DTE book:")
    for r in d["added"]:
        print(
            f"  {r['day']} {r['et']} {r['dir']} dte={r['dte']} "
            f"1m={r['rip_1m_spy']:+.3f} 3m={r['trend_3m_spy']:+.3f}"
        )


if __name__ == "__main__":
    main()

"""Canonical cover-us exec gates. Path: engine/shared/gates.py.

Railway tradier_exec must call these functions before any Tradier BTO/STC.
Do not re-implement a parallel knob set on Railway.

SMS and broker orders consume sub_alert_send only.
Admin ledger is the full candidate set. Sub is the action subset.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

Direction = Literal["BULL", "BEAR"]
HaltReason = Literal[
    "none",
    "operator_halt",
    "session_loss",
    "session_loss_after_lift",
    "consecutive_fail",
]
EnvelopeReason = Literal[
    "protective",
    "ticket_risk",
    "cata_opt",
    "cata_spy",
    "fail_90",
    "orphan_adopt",
    "peak_giveback",
]


STARTER_NOTIONAL_USD = 2000.0
RISK_QTY_CAP = 16
SESSION_STARTER_CAP = 16  # omit-or-16; NEVER 8
TICKET_RISK_USD = 240.0
PROTECTIVE_STOP_USD = 0.15
# 10/1 14:04 1DTE BULL printed −$0.21 option in 1m (SPY −$0.42) then ran
# into the 15:50 close. 0DTE keeps $0.15 (9/29 16-lot). 1DTE uses $0.30.
PROTECTIVE_STOP_1DTE_USD = 0.30
QUOTE_GRACE_SEC = 8.0
# 10/8 13:20 1DTE filled and recover_lost flattened in 9s because the
# fill stamp never landed. Unstamped broker qty is a new ticket, not a
# 90-minute orphan. Stamp and HOLD. Envelope after 15s still clips a
# real 10/2-style leftover (1DTE $0.30).
HOLD_UNSTAMPED_FILL = True
FRESH_FILL_SEC = 15.0
# 10/8 10:23 / 11:11 0DTE fail_90 fired with no SPY reversal. Immediate
# STC is only for a major move against the send (bounce $0.30) or the
# option/protective/cata envelope above. 1DTE still has no fail_90.
FAIL_90_REQUIRES_REVERSAL = True
CATASTROPHIC_OPTION_USD = 0.40
CATASTROPHIC_SPY = 0.50
FAIL_SEC = 90.0
# 10/1 14:26 1DTE BULL never tagged $0.15, then 90s FAIL scratched a
# hold-to-flatten path winner. 1DTE does not 90s-FAIL.
FAIL_SEC_1DTE = None
BOUNCE_AGAINST_SPY = 0.30
OPEN_REVERSAL_WINDOW = ("10:00", "10:02")
# 9/30 10:14 BEAR 10-lot 1.84→1.65 −$190. First 20 minutes of the midday
# 0DTE book is fade tape, not a dark gap: overlay scores 1DTE TREND here
# (9/25 runner). 10:21–12:29 0DTE still posts. RTH publish is 09:30–15:50.
OPEN_FADE_WINDOW = ("10:00", "10:20")
RTH_START_ET = "09:30"
RTH_END_ET = "15:50"
# 9/30 12:44 BULL 16-lot 1.19→1.03 −$256, STC at 12:45 cutover. Do not
# open a fresh 0DTE in the last 15 minutes of the 0DTE book.
NEAR_CUTOVER_0DTE_ET = "12:30"
# 10/8: 12:30–12:44 skipped 0DTE but overlay still scored 0DTE, so the
# 12:37–12:38 BEAR dump had no product. From 12:30 overlay MUST select
# 1DTE OCC (TREND still required until 12:45). 0DTE stays the book
# 10:21–12:29. Do not freeze 0DTE TREND before 12:30.
DTE_BOOK_ET = NEAR_CUTOVER_0DTE_ET
EXTRA_BTO_MFE_USD = 0.20
# 10/5 16-lot 773C 0.79 ran to ~1.67 (~+$1,408). Fill $0.15 stop was 0.64
# (~$1,650 giveback). Once the ticket prints a high of at least $50,
# flatten if unrealized gives back $50 from that high. Ratchet the high
# on the bid. Fill $0.15 / 1DTE $0.30 still protect tickets that never
# made a high. Do not STC on fill (9/30 10:39).
PEAK_GIVEBACK_USD = 50.0
# 10/9 10:45 BULL 13-lot: fill 1.17, print 1.23 ($78), STC 62s at 1.10.
# Peak lock on a one-minute flicker dumps the rip. Same $50/$75 giveback
# still fires after 90s (10/5 773C was minutes, not 62s). First 90s only
# flatten on fill $0.15 / cata / ticket_risk — not peak, not fail_90
# without a $0.30 SPY reversal. Do not move the $0.15 stop to the print
# high. Do not fire a starter $37.50 giveback before 90s.
PEAK_GIVEBACK_MIN_SEC = FAIL_SEC
# One send identity: unix send_ts in America/New_York is SMS time, overlay
# label, strike copy, and Tradier consume. Do not use legacy_ledger_hhmm
# for live SMS (10:32 vs 10:45 same send). Do not SMS at arm/receipt then
# consume a later opposite seal (11:04 BULL 777/778 vs 11:07 BEAR put).
ET = ZoneInfo("America/New_York")
LIVE_SEND_TS_MIN = 1_000_000_000.0
CLOCK = "America/New_York unix send instant"
CLOCK_FALLBACK = "historical_audits_only"
CLOCK_IDENTITY_LOCK = True
SMS_AT_SEAL = True
SESSION_LOSS_HALT_USD = 750.0  # 3 envelope misses (~$240) before the day stops
CONSECUTIVE_FAIL_HALT = 4
# 9/28: 1DTE wiggles printed 4 FAILs and halted before the 0DTE 767-put.
# Count only 0DTE toward the streak. 1DTE still has envelope + dollar halt.
FAIL_STREAK_0DTE_ONLY = True
COOLDOWN_AFTER_FAIL_SEC = 480.0
# 9/28–9/29 misfire: next-day paper in CHOPPY/RANGE/unknown BEFORE cutover.
# 0DTE may still starter until 12:29. From 12:30 overlay scores 1DTE
# (TREND required until 12:45). After 12:45 leftover 0DTE is refused.
# Morning 1DTE still needs TREND (9/25 runner).
TREND_REGIMES = frozenset({"TREND", "TRENDING"})
DTE_CUTOVER_ET = "12:45"
# Cover-us exits are STC ladder → market. Do not hold a loser on trail.
ENGINE_EXIT_MODE = "ladder_to_market"
TAKE_EXIT = "ladder_to_market"  # flatten mode only; never rest a loser on bid
HOLD_EXIT = "hold"
CHOP_SIZE = False  # 9/29 sized into CHOPPY; refuse instead.
BTO_SOURCE = "sub_alert_send"  # never BTO from admin_alert_ledger
QUEUE_OPPOSITE = False  # exec must not invent opposite rips
# 9/30 10:39: BTO 10 @ $1.84 then STC @ $1.83 in 3s. Rec envelope cannot
# do that. Native bid-exit on fill / take_exit on HOLD ticks caused it.
STC_REQUIRES_ENVELOPE = True
STC_ON_BTO_FILL = False
WORKING_STC_ON_FILL = False
# Rec scenario (9/15–9/29 replay): envelope + 0DTE halt + extra BTO to 16
# + 1DTE skip unless TREND before 12:45 + 1DTE book from 12:30 (TREND
# until 12:45, then trade it). Overlay MUST call starter_dte_for_clock.
# RTH is 09:30–15:50 with a product at every clock. Open-fade 0DTE stays
# skip; overlay scores 1DTE TREND in 10:00–10:20. Do not scratch a fill
# that has no stamp. fail_90 only on a $0.30 SPY reversal. Peak giveback
# waits 90s so a 62s option tick cannot flatten a live rip. SMS / overlay
# / Tradier share one unix send instant (clock identity lock).
# Railway must advertise this dict on /health
# and ignore leftover knobs (queue_opposite=true, take_exit=bid).
REC_BOOK_SHIP = "2026-10-09-rip-hold-clock"


def rec_book() -> dict:
    """Production book that matched the Rec column. Do not fork these knobs."""
    return {
        "rec_book": True,
        "rec_book_ship": REC_BOOK_SHIP,
        "engine_exit_mode": ENGINE_EXIT_MODE,
        "take_exit": TAKE_EXIT,
        "bto_source": BTO_SOURCE,
        "queue_opposite": QUEUE_OPPOSITE,
        "chop_size": CHOP_SIZE,
        "fail_streak_0dte_only": FAIL_STREAK_0DTE_ONLY,
        "extra_bto": True,
        "extra_bto_fill_to": RISK_QTY_CAP,
        "extra_bto_mfe_usd": EXTRA_BTO_MFE_USD,
        "skip_1dte_not_trend": True,
        "dte_cutover_et": DTE_CUTOVER_ET,
        "dte_book_et": DTE_BOOK_ET,
        "trade_1dte_from_book_et": True,
        "open_fade_1dte_book": True,
        "skip_0dte_after_cutover": True,
        "trade_1dte_after_cutover": True,
        "skip_0dte_open_fade": True,
        "open_fade_window": list(OPEN_FADE_WINDOW),
        "skip_0dte_near_cutover": True,
        "near_cutover_0dte_et": NEAR_CUTOVER_0DTE_ET,
        "channels_aligned": True,
        "sms_iff_sub_send": True,
        "sms_from": BTO_SOURCE,
        "cooldown_after_fail_sec": COOLDOWN_AFTER_FAIL_SEC,
        "session_loss_halt_usd": SESSION_LOSS_HALT_USD,
        "consecutive_fail_halt": CONSECUTIVE_FAIL_HALT,
        "protective_stop_usd": PROTECTIVE_STOP_USD,
        "protective_stop_1dte_usd": PROTECTIVE_STOP_1DTE_USD,
        "fail_sec_1dte": FAIL_SEC_1DTE,
        "ticket_risk_usd": TICKET_RISK_USD,
        "risk_qty_cap": RISK_QTY_CAP,
        "starter_notional_usd": STARTER_NOTIONAL_USD,
        "override_trail": True,
        "use_trail": False,
        "stc_requires_envelope": STC_REQUIRES_ENVELOPE,
        "stc_on_bto_fill": STC_ON_BTO_FILL,
        "working_stc_on_fill": WORKING_STC_ON_FILL,
        "before_stc": True,
        "skip_already_flat": True,
        "recover_lost_owned": True,
        "orphan_adopt_flattens": True,
        "hold_unstamped_fill": HOLD_UNSTAMPED_FILL,
        "fresh_fill_sec": FRESH_FILL_SEC,
        "fail_90_requires_reversal": FAIL_90_REQUIRES_REVERSAL,
        "protect_from_high": True,
        "peak_giveback_usd": PEAK_GIVEBACK_USD,
        "peak_giveback_min_sec": PEAK_GIVEBACK_MIN_SEC,
        "skip_1min_rip": True,
        "one_min_rip_usd": ONE_MIN_RIP_USD,
        "one_min_climax_frac": ONE_MIN_CLIMAX_FRAC,
        "one_min_climax_usd": ONE_MIN_CLIMAX_USD,
        "trend_3m_min_usd": TREND_3M_MIN_USD,
        "skip_1min_stall": True,
        "stall_1m_usd": STALL_1M_USD,
        "flatten_limit_thru_usd": 0.0,
        "quote_grace_sec": QUOTE_GRACE_SEC,
        "rth_start_et": RTH_START_ET,
        "rth_end_et": RTH_END_ET,
        "fail_sec": FAIL_SEC,
        "hold_exit": HOLD_EXIT,
        "clock": CLOCK,
        "clock_fallback": CLOCK_FALLBACK,
        "clock_identity_lock": CLOCK_IDENTITY_LOCK,
        "sms_at_seal": SMS_AT_SEAL,
    }


def et_hhmm_from_ts(send_ts: float) -> str:
    return datetime.fromtimestamp(float(send_ts), tz=ET).strftime("%H:%M")


def et_hms_from_ts(send_ts: float) -> str:
    return datetime.fromtimestamp(float(send_ts), tz=ET).strftime("%H:%M:%S")


def locked_et_hhmm(send_ts: float, et_hhmm: str | None = None) -> str:
    """Live sends use unix instant. Replay/tiny ts keep the passed label."""
    if float(send_ts) >= LIVE_SEND_TS_MIN:
        return et_hhmm_from_ts(send_ts)
    return et_hhmm or ""


def strike_copy(direction: Direction, spy: float) -> dict:
    """ITM/OTM from the sealed send. BULL=calls, BEAR=puts.

    10/9 11:04 SMS 777 ITM / 778 OTM at 777.41 is BULL. 11:07 BEAR 776.92
    is 777 ITM put / 776 OTM put. Do not attach the BULL copy to the BEAR
    consume.
    """
    lo = int(math.floor(float(spy)))
    if direction == "BULL":
        return {"itm": lo, "otm": lo + 1, "kind": "call"}
    return {"itm": lo + 1, "otm": lo, "kind": "put"}


def seal_identity(
    send_ts: float,
    direction: Direction,
    spy: float,
    et_hhmm: str | None = None,
) -> dict:
    live = float(send_ts) >= LIVE_SEND_TS_MIN
    hhmm = locked_et_hhmm(send_ts, et_hhmm)
    strikes = strike_copy(direction, spy)
    return {
        "send_ts": float(send_ts),
        "et_hhmm": hhmm,
        "et_hms": et_hms_from_ts(send_ts) if live else None,
        "direction": direction,
        "spy": float(spy),
        "strike_itm": strikes["itm"],
        "strike_otm": strikes["otm"],
        "strike_kind": strikes["kind"],
        "clock": CLOCK,
        "clock_identity_lock": CLOCK_IDENTITY_LOCK,
        "sms_at_seal": SMS_AT_SEAL,
        "publication_et": et_hms_from_ts(send_ts) if live else hhmm,
    }

# 2026-09-29 learn (exec still traded these): weak pre-move 1/9 −$160,
# pre-move 0.50+ 0/5 −$256, chase ≥ $0.50, CHOPPY 0/8 −$297.
# Overlay skipped some; BTO never saw the flags. Refuse at before_bto.
PRE_MOVE_WEAK_LO = 0.15
PRE_MOVE_WEAK_HI = 0.29
PRE_MOVE_STRONG = 0.50
CHASE_SPY = 0.50
SAME_DIR_LOCK_SEC = 1080.0
# 10/2 SUB losers were 1-minute prints (10:34, 10:46, 11:42, 11:52, 12:13,
# 12:27). The 10:54 BEAR winner had a 3-minute dump (−$0.87) and a smaller
# last minute (−$0.28). SUB requires the 3-minute trend; a 1-minute rip
# is admin-only. Overlay must pass signed SPY deltas (up is +).
ONE_MIN_RIP_USD = 0.20
TREND_3M_MIN_USD = 0.20
# 10/8 12:17 BULL +$1.55 / +$1.61 (96% of the 3-minute in one bar) was a
# climax, not a trend. Skip when the last minute is ≥ 80% of the 3-minute
# AND at least $0.50. Floor stays $0.50 so 9/28 10:42 BEAR −$0.24 / −$0.26
# (93% but a grind, not a spike) still sends. 10/2 10:54 −$0.28 / −$0.87
# and 10/5 12:22 +$0.175 / +$0.235 still send.
ONE_MIN_CLIMAX_FRAC = 0.80
ONE_MIN_CLIMAX_USD = 0.50
# 10/5 large losers were 3-minute rips that had already stalled: last
# minute +$0.02–$0.03 while 3-minute was still ≥ $0.20 (10:58 BULL −$182,
# 11:42 BULL −$320, 10:40 BULL −$64). Require the last minute still print
# WITH the send by at least $0.08. 10/5 12:22 +$1,344 was +$0.175 / +$0.235.
# 9/24 10:43 BEAR, 9/25 11:55 BULL, 9/28 10:42 BEAR, 10/2 10:54 BEAR keep.
STALL_1M_USD = 0.08

CONSUMED_CREATE_SQL = """\
CREATE TABLE IF NOT EXISTS consumed_sends (
  session_date date NOT NULL,
  send_ts double precision NOT NULL,
  dir text NOT NULL,
  consumed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_date, send_ts)
);
"""
CONSUMED_INSERT_SQL = (
    "INSERT INTO consumed_sends (session_date, send_ts, dir) "
    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING"
)
CONSUMED_LOAD_SQL = (
    "SELECT send_ts, dir FROM consumed_sends WHERE session_date = %s"
)


def trend_confirm_kwargs(direction: Direction) -> dict:
    """Signed 1m/3m that pass the 10/2 1-minute filter (3-minute trend)."""
    sign = 1.0 if direction == "BULL" else -1.0
    return {
        "rip_1m_spy": round(sign * 0.25, 2),
        "trend_3m_spy": round(sign * 0.60, 2),
    }


def signed_spy_deltas(closes: Sequence[float] | None) -> dict[str, float | None]:
    """Stamp overlay Candidate from the last four 1-minute SPY closes.

    closes[-1] is now. Up is +. Production overlay MUST call this on every
    candidate. Do not invent defaults — a short tape is skip_1min_unconfirmed.
    10/2 11:42: last four closes that yield +0.44 / +0.12.
    10/2 10:54: last four closes that yield −0.28 / −0.87.
    """
    if closes is None or len(closes) < 4:
        return {"rip_1m_spy": None, "trend_3m_spy": None}
    try:
        now = float(closes[-1])
        m1 = float(closes[-2])
        m3 = float(closes[-4])
    except (TypeError, ValueError):
        return {"rip_1m_spy": None, "trend_3m_spy": None}
    return {
        "rip_1m_spy": round(now - m1, 4),
        "trend_3m_spy": round(now - m3, 4),
    }


def one_bar_rip_skip_reason(
    *,
    direction: Direction | None = None,
    rip_1m_spy: float | None = None,
    trend_3m_spy: float | None = None,
) -> str | None:
    """Refuse a 1-minute print that is not a 3-minute trend still in motion.

    10/2 11:42 +$0.44 bar / 3-minute +$0.12 → skip. 10/2 10:54 BEAR
    1-minute −$0.28 / 3-minute −$0.87 → send. 10/5 10:58 / 11:42 stalled
    last minute (+$0.03 / +$0.02) after a 3-minute rip → skip_1min_stall.
    Missing fields fail closed.
    """
    if direction is None or rip_1m_spy is None or trend_3m_spy is None:
        return "skip_1min_unconfirmed"
    want = 1.0 if direction == "BULL" else -1.0
    r1 = float(rip_1m_spy)
    t3 = float(trend_3m_spy)
    if t3 * want <= 0 or abs(t3) < TREND_3M_MIN_USD:
        return "skip_1min_rip"
    if abs(r1) >= abs(t3) - 1e-9 and abs(r1) >= ONE_MIN_RIP_USD:
        return "skip_1min_rip"
    if abs(r1) > abs(t3):
        return "skip_1min_rip"
    # 10/8 12:17 +$1.55 / +$1.61: one bar did 96% of the 3-minute.
    if (
        abs(r1) >= ONE_MIN_CLIMAX_USD - 1e-9
        and abs(r1) >= abs(t3) * ONE_MIN_CLIMAX_FRAC - 1e-9
    ):
        return "skip_1min_rip"
    if r1 * want < STALL_1M_USD - 1e-9:
        return "skip_1min_stall"
    return None


def starter_qty(ask: float) -> int:
    """Max debit ~$2000. Option multiplier is 100."""
    if ask <= 0:
        return 0
    contracts = int(STARTER_NOTIONAL_USD // (ask * 100.0))
    return max(1, min(contracts, RISK_QTY_CAP))


def extra_bto_qty(broker_qty: int) -> int:
    """Contracts left to the 16-lot cap. 9/24 winners had 3–5 of headroom."""
    return max(0, RISK_QTY_CAP - int(broker_qty))


_OCC_EXP = re.compile(r"(\d{6})[CP]")


def option_dte(option_symbol: str | None, session_date: str) -> int | None:
    """Days from session_date to OCC expiry. None = unknown (treat as 0DTE)."""
    if not option_symbol:
        return None
    m = _OCC_EXP.search(option_symbol.upper())
    if not m:
        return None
    raw = m.group(1)
    try:
        exp = date(2000 + int(raw[0:2]), int(raw[2:4]), int(raw[4:6]))
        sess = date.fromisoformat(session_date)
    except ValueError:
        return None
    return (exp - sess).days


def past_dte_cutover(et_hhmm: str, cutover: str = DTE_CUTOVER_ET) -> bool:
    """True from 12:45 ET onward. Leftover 0DTE is refused."""
    if not et_hhmm:
        return False
    return et_hhmm >= cutover


def past_dte_book(et_hhmm: str, book_et: str = DTE_BOOK_ET) -> bool:
    """True from 12:30 ET. Overlay must score 1DTE; 0DTE is admin-only."""
    if not et_hhmm:
        return False
    return et_hhmm >= book_et


def starter_dte_for_clock(et_hhmm: str) -> int:
    """OCC tenor overlay must select for a new starter.

    0DTE: 09:30–09:59 and 10:21–12:29 (quality bar still applies).
    1DTE TREND: 10:00–10:20 (0DTE is skip_0dte_open_fade, not a dark gap)
    and from 12:30 (TREND until 12:45, then 1DTE book).
    Do not freeze 0DTE TREND before 12:30 except the 10:00–10:20 fade tape.
    """
    if past_dte_book(et_hhmm):
        return 1
    if et_hhmm and hhmm_in_window(et_hhmm, *OPEN_FADE_WINDOW):
        return 1
    return 0


def counts_toward_fail_streak(dte: int | None, et_hhmm: str | None = None) -> bool:
    """1DTE+ does not increment the morning 0DTE streak.

    After 12:45 the live book is 1DTE, so those FAILs count.
    """
    if past_dte_cutover(et_hhmm or ""):
        return True
    if not FAIL_STREAK_0DTE_ONLY:
        return True
    return dte is None or dte <= 0


def hhmm_in_window(hhmm: str, start: str, end: str) -> bool:
    return start <= hhmm <= end


def hhmm_to_minutes(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def in_fail_cooldown(
    last_fail_hhmm: str | None,
    et_hhmm: str,
    cooldown_sec: float = COOLDOWN_AFTER_FAIL_SEC,
) -> bool:
    """True when this send is inside the 8-minute post-FAIL lockout."""
    if not last_fail_hhmm:
        return False
    a = hhmm_to_minutes(last_fail_hhmm)
    b = hhmm_to_minutes(et_hhmm)
    if a is None or b is None:
        return False
    gap_sec = (b - a) * 60
    return 0 <= gap_sec < cooldown_sec


def misfire_skip_reason(
    *,
    dte: int | None = None,
    regime: str | None = None,
    last_fail_hhmm: str | None = None,
    et_hhmm: str = "",
) -> str | None:
    """DTE clock + FAIL cooldown.

    0DTE is the midday book through 12:29. From 12:30 overlay scores 1DTE
    (TREND required until 12:45). 1DTE in CHOP/RANGE is skip_1dte_not_trend
    (9/28 misfire). TREND 1DTE still posts (9/25 runner, 10/8 12:37 dump).
    From 12:45: all orders are 1DTE. Trade them. Refuse leftover 0DTE
    (skip_0dte_after_cutover). Do not require TREND after cutover — 9/30
    14:06 BEAR was a real sub send that Rec ate as misfire.
    """
    if in_fail_cooldown(last_fail_hhmm, et_hhmm):
        return "skip_cooldown_after_fail"
    if past_dte_cutover(et_hhmm):
        if dte is not None and dte <= 0:
            return "skip_0dte_after_cutover"
        return None
    if dte is not None and dte >= 1:
        reg = (regime or "").strip().upper()
        if reg not in TREND_REGIMES:
            return "skip_1dte_not_trend"
    return None


def clock_quality_skip_reason(
    *,
    dte: int | None = None,
    et_hhmm: str = "",
) -> str | None:
    """Refuse the two 9/30 morning envelope losses >$150. Not a 0DTE freeze.

    10:14 BEAR −$190 sat in the first 20 minutes of midday 0DTE.
    12:44 BULL −$256 opened 0DTE one minute before the 1DTE cutover.
    1DTE (9/25 TREND, 15:16 runner) and 10:21–12:29 0DTE still post.
    Missing dte is treated as 0DTE (fail closed) inside these windows.
    """
    if not et_hhmm:
        return None
    if past_dte_cutover(et_hhmm):
        return None
    if dte is not None and dte >= 1:
        return None
    if hhmm_in_window(et_hhmm, *OPEN_FADE_WINDOW):
        return "skip_0dte_open_fade"
    if et_hhmm >= NEAR_CUTOVER_0DTE_ET:
        return "skip_0dte_near_cutover"
    return None


QUALITY_SKIP_CODES = frozenset(
    {
        "skip_choppy",
        "skip_arm_bar",
        "skip_weak_pre_move",
        "skip_strong_pre_move",
        "skip_chase",
        "skip_quality_unknown",
        "skip_same_dir_lock",
        "skip_1min_rip",
        "skip_1min_unconfirmed",
        "skip_1min_stall",
        "skip_0dte_open_fade",
        "skip_0dte_near_cutover",
        "skip_not_sub",
        "skip_queue_opposite",
    }
)
MISFIRE_SKIP_CODES = frozenset(
    {
        "skip_dte_unknown",
        "skip_1dte_not_trend",
        "skip_0dte_after_cutover",
        "skip_cooldown_after_fail",
    }
)


def sub_action_skip_reason(
    *,
    choppy: bool = False,
    on_arm_bar: bool = False,
    pre_move_spy: float | None = None,
    chase_spy: float | None = None,
    regime: str | None = None,
    same_dir_age_sec: float | None = None,
    dte: int | None = None,
    et_hhmm: str = "",
    last_fail_hhmm: str | None = None,
    direction: Direction | None = None,
    rip_1m_spy: float | None = None,
    trend_3m_spy: float | None = None,
) -> str | None:
    """One skip stack for SUB SMS and Tradier BTO. None = both may fire.

    Overlay SMS and before_bto must call this. A SUB_signals text is a
    sub_alert_send. If this returns a code, do not SMS and do not BTO.
    Halt / bounce / inflight stay exec-only (live tape).
    """
    q = quality_skip_reason(
        choppy=choppy,
        on_arm_bar=on_arm_bar,
        pre_move_spy=pre_move_spy,
        chase_spy=chase_spy,
        regime=regime,
        same_dir_age_sec=same_dir_age_sec,
        direction=direction,
        rip_1m_spy=rip_1m_spy,
        trend_3m_spy=trend_3m_spy,
    )
    if q is not None:
        return q
    if dte is None:
        return "skip_dte_unknown"
    mf = misfire_skip_reason(
        dte=dte,
        regime=regime,
        last_fail_hhmm=last_fail_hhmm,
        et_hhmm=et_hhmm,
    )
    if mf is not None:
        return mf
    return clock_quality_skip_reason(dte=dte, et_hhmm=et_hhmm)


def source_skip_reason(
    *,
    plot: str | None = None,
    is_opposite: bool = False,
    overlay_queued: bool = False,
) -> str | None:
    """Tie cash to the overlay sub send. Admin / exec-queued opposite are not the book."""
    if plot != BTO_SOURCE:
        return "skip_not_sub"
    if is_opposite and not overlay_queued:
        return "skip_queue_opposite"
    return None


def quality_skip_reason(
    *,
    choppy: bool = False,
    on_arm_bar: bool = False,
    pre_move_spy: float | None = None,
    chase_spy: float | None = None,
    regime: str | None = None,
    same_dir_age_sec: float | None = None,
    direction: Direction | None = None,
    rip_1m_spy: float | None = None,
    trend_3m_spy: float | None = None,
) -> str | None:
    """Return a skip code if this send is in a 9/29 losing slice. None = ok to size.

    Missing pre_move/chase is a refuse (skip_quality_unknown). 9/29 BTO
    defaulted those kwargs off and bought the overlay-skipped tape.
    10/2 1-minute rips: missing 1m/3m is skip_1min_unconfirmed. A 1-minute
    print without a 3-minute trend is skip_1min_rip. 10/5 stalled last
    minute (< $0.08 with the send) is skip_1min_stall.
    """
    if choppy or (regime or "").strip().upper() == "CHOPPY":
        return "skip_choppy"
    if on_arm_bar:
        return "skip_arm_bar"
    if pre_move_spy is None or chase_spy is None:
        return "skip_quality_unknown"
    mag = abs(pre_move_spy)
    if PRE_MOVE_WEAK_LO <= mag <= PRE_MOVE_WEAK_HI:
        return "skip_weak_pre_move"
    if mag >= PRE_MOVE_STRONG:
        return "skip_strong_pre_move"
    if chase_spy >= CHASE_SPY:
        return "skip_chase"
    if same_dir_age_sec is not None and same_dir_age_sec < SAME_DIR_LOCK_SEC:
        return "skip_same_dir_lock"
    return one_bar_rip_skip_reason(
        direction=direction,
        rip_1m_spy=rip_1m_spy,
        trend_3m_spy=trend_3m_spy,
    )


def bounce_against(
    direction: Direction,
    send_spy: float,
    spy: float,
    bar_high: float,
    bar_low: float,
    et_hhmm: str,
) -> bool:
    """True when the micro-trend has already reversed against the send."""
    if direction == "BEAR":
        if spy > send_spy + BOUNCE_AGAINST_SPY:
            return True
        if bar_high > send_spy + BOUNCE_AGAINST_SPY:
            return True
    else:
        if spy < send_spy - BOUNCE_AGAINST_SPY:
            return True
        if bar_low < send_spy - BOUNCE_AGAINST_SPY:
            return True
    if hhmm_in_window(et_hhmm, *OPEN_REVERSAL_WINDOW):
        if direction == "BEAR" and (
            spy > send_spy + BOUNCE_AGAINST_SPY
            or bar_high > send_spy + BOUNCE_AGAINST_SPY
        ):
            return True
        if direction == "BULL" and (
            spy < send_spy - BOUNCE_AGAINST_SPY
            or bar_low < send_spy - BOUNCE_AGAINST_SPY
        ):
            return True
    return False


def unrealized_dollars(mark_bid: float, avg_fill: float, qty: int) -> float:
    return (mark_bid - avg_fill) * 100.0 * qty


def protective_stop_usd(dte: int | None = None) -> float:
    """0DTE $0.15. 1DTE $0.30. Missing dte is 0DTE (fail closed)."""
    if dte is not None and dte >= 1:
        return PROTECTIVE_STOP_1DTE_USD
    return PROTECTIVE_STOP_USD


def fail_sec_for(dte: int | None = None) -> float | None:
    """90s FAIL on 0DTE only. 1DTE holds to protective / ticket_risk / flatten."""
    if dte is not None and dte >= 1:
        return FAIL_SEC_1DTE
    return FAIL_SEC


def envelope_hit(
    *,
    fill_px: float,
    mark_bid: float,
    qty: int,
    spy_adverse: float,
    seconds_since_fill: float,
    ticket_phase: str | None,
    dte: int | None = None,
    peak_unrealized: float = 0.0,
) -> EnvelopeReason | None:
    """Evaluate combined broker qty. qty is broker longs, not intended starter size."""
    if qty <= 0:
        return None
    if seconds_since_fill < QUOTE_GRACE_SEC:
        return None
    down = round(fill_px - mark_bid, 4)
    u = round(unrealized_dollars(mark_bid, fill_px, qty), 2)
    peak = max(float(peak_unrealized or 0.0), u)
    if (
        seconds_since_fill >= PEAK_GIVEBACK_MIN_SEC - 1e-9
        and peak >= PEAK_GIVEBACK_USD - 1e-9
        and peak - u >= PEAK_GIVEBACK_USD - 1e-9
    ):
        return "peak_giveback"
    stop = protective_stop_usd(dte)
    if down >= stop:
        return "protective"
    if u <= -TICKET_RISK_USD:
        return "ticket_risk"
    if down >= CATASTROPHIC_OPTION_USD:
        return "cata_opt"
    if spy_adverse >= CATASTROPHIC_SPY:
        return "cata_spy"
    fail_s = fail_sec_for(dte)
    if fail_s is not None and seconds_since_fill >= fail_s and ticket_phase == "FAIL":
        # 10/8: 91s STC with no reversal. Timer scratch only if SPY has
        # already moved $0.30 against the send. Protective $0.15 / cata
        # still flatten a real option dump without waiting for SPY.
        if FAIL_90_REQUIRES_REVERSAL and spy_adverse < BOUNCE_AGAINST_SPY - 1e-9:
            return None
        return "fail_90"
    return None


def stc_ladder_prices(bid: float) -> list[tuple[str, float | None]]:
    """Limit at bid, bid-0.05, bid-0.10, then market."""
    return [
        ("limit", round(bid, 2)),
        ("limit", round(bid - 0.05, 2)),
        ("limit", round(bid - 0.10, 2)),
        ("market", None),
    ]


def ledger_invariant(admin_n: int, sub_n: int) -> bool:
    return admin_n >= sub_n >= 0


@dataclass
class SessionState:
    session_date: str
    consumed: dict[float, str] = field(default_factory=dict)
    skipped_dup_submit_n: int = 0
    skip_bounce_n: int = 0
    skip_quality_n: int = 0
    skip_misfire_n: int = 0
    session_starters_n: int = 0
    pending_entry: bool = False
    inflight: bool = False
    broker_qty: int = 0
    ticket_phase: str | None = None
    last_bto_client_key: str | None = None
    last_action: str | None = None
    last_stc_ladder: str | None = None
    engine_exit_mode: str = ENGINE_EXIT_MODE
    protect_fills_n: int = 0
    ticket_risk_hits_n: int = 0
    cata_fills_n: int = 0
    session_realized_usd: float = 0.0
    halt_baseline_usd: float = 0.0
    halt_lifted: bool = False
    session_halt: bool = False
    session_lost_blocks_send: bool = False
    session_halt_reason: HaltReason = "none"
    fails_after_lift_n: int = 0
    consecutive_fail_n: int = 0
    operator_halt: bool = False
    last_fail_hhmm: str | None = None
    last_option_symbol: str | None = None
    ticket_fill_ts: float | None = None
    avg_fill_px: float | None = None
    orphan_adopt: bool = False
    ticket_peak_unrealized: float = 0.0

    def additional_loss_usd(self) -> float:
        return self.session_realized_usd - self.halt_baseline_usd

    def may_starter_bto(self) -> bool:
        if self.operator_halt or self.session_halt or self.session_lost_blocks_send:
            return False
        if self.pending_entry or self.inflight:
            return False
        if self.broker_qty > 0:
            return False
        return True

    def try_consume(
        self,
        send_ts: float,
        direction: Direction,
        *,
        count_starter: bool = True,
        persist=None,
    ) -> bool:
        """INSERT ON CONFLICT DO NOTHING. Never delete on flatten.

        persist(session_date, send_ts, direction) -> bool must run
        CONSUMED_INSERT_SQL and return True only when the row is new.
        Live 9/29 had consumed_send_ts=[] after a full session.
        """
        if send_ts in self.consumed:
            self.skipped_dup_submit_n += 1
            self.last_action = "skip_dup_send_ts"
            return False
        if persist is not None:
            inserted = persist(self.session_date, send_ts, direction)
            if not inserted:
                self.consumed[send_ts] = direction
                self.skipped_dup_submit_n += 1
                self.last_action = "skip_dup_send_ts"
                return False
        self.consumed[send_ts] = direction
        if count_starter:
            self.session_starters_n += 1
            self.last_bto_client_key = f"{send_ts}:{direction}"
            self.pending_entry = True
        return True

    def load_consumed(self, rows) -> None:
        """Boot: SELECT send_ts, dir FROM consumed_sends WHERE session_date = today."""
        for send_ts, direction in rows:
            self.consumed[float(send_ts)] = direction

    def on_flatten(
        self,
        realized_delta: float,
        phase: str,
        *,
        dte: int | None = None,
        et_hhmm: str | None = None,
    ) -> None:
        """Flatten must NOT un-consume send_ts.

        1DTE FAILs/wins before 12:45 are invisible to consecutive_fail_n.
        9/28 1DTE scratches had halted the 0DTE book before the 767-put printed.
        After 12:45 the book is 1DTE, so those FAILs count.
        Any FAIL starts the 8-minute misfire cooldown.
        """
        self.session_realized_usd += realized_delta
        self.pending_entry = False
        self.inflight = False
        self.broker_qty = 0
        self.ticket_phase = None
        self.ticket_fill_ts = None
        self.avg_fill_px = None
        self.orphan_adopt = False
        self.ticket_peak_unrealized = 0.0
        if phase == "FAIL" and et_hhmm:
            self.last_fail_hhmm = et_hhmm
        if counts_toward_fail_streak(dte, et_hhmm):
            if phase == "FAIL":
                self.consecutive_fail_n += 1
                if self.halt_lifted:
                    self.fails_after_lift_n += 1
            else:
                self.consecutive_fail_n = 0
        self.refresh_halt()

    def _trip_halt(self, reason: HaltReason) -> None:
        self.session_halt = True
        self.session_lost_blocks_send = True
        self.session_halt_reason = reason

    def refresh_halt(self) -> None:
        # Once halted this ET date, stay halted. Dashboard flatten must not clear it.
        if self.session_halt or self.session_lost_blocks_send or self.operator_halt:
            self.session_halt = True
            self.session_lost_blocks_send = True
            if self.operator_halt:
                self.session_halt_reason = "operator_halt"
            elif self.session_halt_reason == "none":
                self.session_halt_reason = "session_loss"
            return
        if self.halt_lifted:
            if self.additional_loss_usd() <= -SESSION_LOSS_HALT_USD:
                self._trip_halt("session_loss_after_lift")
                return
            if self.fails_after_lift_n >= CONSECUTIVE_FAIL_HALT:
                self._trip_halt("consecutive_fail")
            return
        # First-line stop: −$750 session cash (3 envelope misses) or 4
        # consecutive 0DTE FAILs, no lift required.
        if self.session_realized_usd <= -SESSION_LOSS_HALT_USD:
            self._trip_halt("session_loss")
            return
        if self.consecutive_fail_n >= CONSECUTIVE_FAIL_HALT:
            self._trip_halt("consecutive_fail")

    def apply_broker_session_cash(self, realized_usd: float) -> None:
        """Halt accounting from Tradier option cash (STC proceeds − BTO cost), not engine book."""
        self.session_realized_usd = round(float(realized_usd), 2)
        self.refresh_halt()

    def recover_lost(self, broker_qty: int, *, now: float | None = None) -> str:
        """Adopt existing longs only. Never BTO. Never clear halt.

        9/30 10:39: lost-scan ran 3s after a live fill and the native path
        STCd the bid. A fill younger than quote grace is not an orphan.

        10/2 12:52 1DTE 7-lot sat 90m until a manual STC. After 15s the
        lost-scan used to return adopt_stc_only even when on_bto_fill had
        stamped the ticket, so a managed 1DTE HOLD looked like an orphan.
        Owned tickets stay on the manage loop (1DTE $0.30 / no fail_90).
        Unstamped qty is a live fill when HOLD_UNSTAMPED_FILL: stamp and
        HOLD. True orphans (HOLD_UNSTAMPED_FILL off) flatten through
        before_stc (orphan_adopt).
        """
        self.refresh_halt()
        self.orphan_adopt = False
        if broker_qty > 0:
            self.broker_qty = int(broker_qty)
            t = float(now if now is not None else time.time())
            if self.ticket_fill_ts is None and HOLD_UNSTAMPED_FILL:
                # 10/8 13:20: qty up, no stamp, STC in 9s. Missing stamp
                # is a live fill, not a 90-minute orphan. Stamp and HOLD.
                # Next scans are owned; envelope still clips a marked-down
                # leftover (10/2 1DTE $0.30) after FRESH_FILL_SEC.
                self.ticket_fill_ts = t
                self.orphan_adopt = False
                self.last_action = "recover_lost_fresh_fill"
                return "fresh_fill"
            if self.ticket_fill_ts is not None:
                age = self.ticket_age_sec(1e9, now=now)
                if age < max(QUOTE_GRACE_SEC, FRESH_FILL_SEC):
                    self.last_action = "recover_lost_fresh_fill"
                    return "fresh_fill"
                self.last_action = "recover_lost_owned"
                return "owned"
            self.orphan_adopt = True
            self.last_action = "recover_lost_adopt_stc_only"
            return "adopt_stc_only"
        self.last_action = "recover_lost_flat"
        return "flat"

    def on_bto_fill(self, qty: int, fill_px: float, *, now: float | None = None) -> None:
        """Stamp this ticket's clock. Never post STC from this hook."""
        new_ticket = self.ticket_fill_ts is None
        self.inflight = False
        self.pending_entry = False
        self.broker_qty = int(qty)
        self.ticket_phase = "FAIL"
        self.avg_fill_px = float(fill_px)
        self.ticket_fill_ts = float(now if now is not None else time.time())
        self.orphan_adopt = False
        if new_ticket:
            self.ticket_peak_unrealized = 0.0
        self.last_action = "bto_fill"

    def ticket_age_sec(
        self, seconds_since_fill: float, *, now: float | None = None
    ) -> float:
        """Clamp caller age to this fill so a prior FAIL clock cannot fire."""
        passed = float(seconds_since_fill)
        if self.ticket_fill_ts is None:
            return passed
        t = float(now if now is not None else time.time())
        local = max(0.0, t - self.ticket_fill_ts)
        return min(passed, local)

    def extra_bto_ok(self, add_qty: int, mfe_usd: float, mark_bid: float, avg_fill: float) -> bool:
        if self.operator_halt or self.session_halt or self.session_lost_blocks_send:
            return False
        if self.ticket_phase != "RUN" or self.broker_qty <= 0:
            return False
        if mfe_usd < EXTRA_BTO_MFE_USD:
            return False
        combined = self.broker_qty + add_qty
        if combined > RISK_QTY_CAP:
            return False
        projected = unrealized_dollars(mark_bid, avg_fill, combined)
        return projected > -TICKET_RISK_USD


def decide_starter(
    state: SessionState,
    *,
    send_ts: float,
    direction: Direction,
    send_spy: float,
    spy: float,
    bar_high: float,
    bar_low: float,
    et_hhmm: str,
    ask: float,
    choppy: bool = False,
    on_arm_bar: bool = False,
    pre_move_spy: float | None = None,
    chase_spy: float | None = None,
    regime: str | None = None,
    same_dir_age_sec: float | None = None,
    persist=None,
    plot: str | None = None,
    is_opposite: bool = False,
    overlay_queued: bool = False,
    option_symbol: str | None = None,
    dte: int | None = None,
    rip_1m_spy: float | None = None,
    trend_3m_spy: float | None = None,
) -> dict:
    """Single entry point before any Tradier buy_to_open."""
    if not ledger_ok_placeholder():
        pass
    state.refresh_halt()
    et_hhmm = locked_et_hhmm(send_ts, et_hhmm)
    ident = seal_identity(send_ts, direction, send_spy, et_hhmm)
    src = source_skip_reason(
        plot=plot, is_opposite=is_opposite, overlay_queued=overlay_queued
    )
    if src is not None:
        state.try_consume(send_ts, direction, count_starter=False, persist=persist)
        state.skip_quality_n += 1
        state.last_action = src
        return {"action": src, "qty": 0, "post": False}
    dte_val = dte if dte is not None else option_dte(option_symbol, state.session_date)
    act = sub_action_skip_reason(
        choppy=choppy,
        on_arm_bar=on_arm_bar,
        pre_move_spy=pre_move_spy,
        chase_spy=chase_spy,
        regime=regime,
        same_dir_age_sec=same_dir_age_sec,
        dte=dte_val,
        et_hhmm=et_hhmm,
        last_fail_hhmm=state.last_fail_hhmm,
        direction=direction,
        rip_1m_spy=rip_1m_spy,
        trend_3m_spy=trend_3m_spy,
    )
    if act is not None:
        state.try_consume(send_ts, direction, count_starter=False, persist=persist)
        if act in MISFIRE_SKIP_CODES:
            state.skip_misfire_n += 1
        else:
            state.skip_quality_n += 1
        state.last_action = act
        return {"action": act, "qty": 0, "post": False}
    if bounce_against(direction, send_spy, spy, bar_high, bar_low, et_hhmm):
        state.try_consume(send_ts, direction, count_starter=False, persist=persist)
        state.skip_bounce_n += 1
        state.last_action = "skip_bounce_against"
        return {"action": "skip_bounce_against", "qty": 0, "post": False}
    if not state.may_starter_bto():
        # Consume so an illicit halt lift cannot fire this send later the same day.
        state.try_consume(send_ts, direction, count_starter=False, persist=persist)
        state.last_action = "skip_halt_or_inflight"
        return {"action": "skip_halt_or_inflight", "qty": 0, "post": False}
    if send_ts in state.consumed:
        state.skipped_dup_submit_n += 1
        state.last_action = "skip_dup_send_ts"
        return {"action": "skip_dup_send_ts", "qty": 0, "post": False}
    if not state.try_consume(send_ts, direction, persist=persist):
        return {"action": "skip_dup_send_ts", "qty": 0, "post": False}
    qty = starter_qty(ask)
    state.last_option_symbol = option_symbol
    return {
        "action": "bto",
        "qty": qty,
        "post": True,
        "client_order_id": state.last_bto_client_key,
        "dte": dte_val,
        "option_symbol": option_symbol,
        **ident,
    }


def ledger_ok_placeholder() -> bool:
    return True


def flatten_now(
    state: SessionState, reason: EnvelopeReason, bid: float
) -> dict:
    """Single flatten path. Orphans use this so before_stc cannot HOLD."""
    if reason == "protective" or reason == "peak_giveback":
        state.protect_fills_n += 1
    elif reason == "ticket_risk":
        state.ticket_risk_hits_n += 1
    elif reason in ("cata_opt", "cata_spy"):
        state.cata_fills_n += 1
    state.last_action = f"flatten_{reason}"
    ladder = stc_ladder_prices(float(bid or 0.0))
    state.last_stc_ladder = "market"
    return apply_manage_result(
        state,
        {
            "action": "flatten",
            "flatten": True,
            "post_stc": True,
            "working_stc": False,
            "reason": reason,
            "ladder": ladder,
            "last_stc_ladder": "market",
            "override_trail": True,
            "engine_exit_mode": ENGINE_EXIT_MODE,
            "ignore_trail": True,
        },
    )


def decide_manage(
    state: SessionState,
    *,
    fill_px: float,
    mark_bid: float,
    qty: int,
    spy_adverse: float,
    seconds_since_fill: float,
    ticket_phase: str | None,
    bid: float,
    now: float | None = None,
    dte: int | None = None,
) -> dict:
    if qty > 0:
        state.broker_qty = int(qty)
        u = round(unrealized_dollars(mark_bid, fill_px, qty), 2)
        state.ticket_peak_unrealized = max(state.ticket_peak_unrealized, max(0.0, u))
    if state.orphan_adopt and qty > 0:
        # 10/2 12:52 1DTE: recover_lost then before_stc HOLD left the 7-lot.
        return flatten_now(state, "orphan_adopt", bid)
    age = state.ticket_age_sec(seconds_since_fill, now=now)
    dte_val = (
        dte
        if dte is not None
        else option_dte(state.last_option_symbol, state.session_date)
    )
    reason = envelope_hit(
        fill_px=fill_px,
        mark_bid=mark_bid,
        qty=qty,
        spy_adverse=spy_adverse,
        seconds_since_fill=age,
        ticket_phase=ticket_phase,
        dte=dte_val,
        peak_unrealized=state.ticket_peak_unrealized,
    )
    if reason is None:
        mfe = round(mark_bid - fill_px, 4)
        extra_ok = False
        extra = 0
        if mfe >= EXTRA_BTO_MFE_USD:
            state.ticket_phase = "RUN"
            extra = extra_bto_qty(state.broker_qty)
            extra_ok = extra > 0 and state.extra_bto_ok(extra, mfe, mark_bid, fill_px)
        return apply_manage_result(
            state,
            {
                "action": "extra_bto" if extra_ok else "hold",
                "flatten": False,
                "post_stc": False,
                "working_stc": False,
                "override_trail": False,
                "ignore_trail": False,
                "engine_exit_mode": ENGINE_EXIT_MODE,
                "ticket_phase": state.ticket_phase,
                "extra_bto": extra_ok,
                "extra_bto_qty": extra if extra_ok else 0,
            },
        )
    return flatten_now(state, reason, bid)


def decide_stc(state: SessionState, **kwargs) -> dict:
    """Call immediately before any Tradier sell_to_close.

    9/30 10:39 posted STC on BTO fill. Rec hold ticks must not sell.
    9/30 11:04 / 12:02 sprayed the ladder after the bid STC already filled
    (reject 0/14). Never STC when broker qty is 0.
    10/2 12:52 1DTE: recover_lost adopt_stc_only (HOLD_UNSTAMPED_FILL
    off) must flatten here even when envelope has not printed
    (orphan_adopt). Unstamped live fills are fresh_fill. Owned 1DTE
    HOLDs still wait for $0.30 / ticket_risk / 15:50.
    """
    if "qty" in kwargs and kwargs["qty"] is not None:
        qty = int(kwargs["qty"])
    else:
        qty = int(state.broker_qty or 0)
    if qty <= 0:
        state.last_action = "skip_already_flat"
        state.broker_qty = 0
        return {
            "post": False,
            "post_stc": False,
            "flatten": False,
            "working_stc": False,
            "cancel_working_stc": True,
            "reason": "skip_already_flat",
            "take_exit": HOLD_EXIT,
            "engine_exit_mode": ENGINE_EXIT_MODE,
        }
    m = decide_manage(state, **kwargs)
    if not m.get("flatten"):
        state.last_action = "hold_no_stc"
        m["post"] = False
        m["post_stc"] = False
        m["working_stc"] = False
        m["cancel_working_stc"] = True
        m["reason"] = m.get("reason") or "stc_requires_envelope"
        return m
    m["post"] = True
    m["post_stc"] = True
    m["working_stc"] = False
    m["cancel_working_stc"] = False
    return m


def apply_manage_result(state: SessionState, m: dict) -> dict:
    """Force cover-us flatten off trail. Live 9/29 still advertised engine_exit_mode=trail.

    HOLD ticks must advertise take_exit=hold. Live 9/30 sold the bid in 3s
    because take_exit=ladder_to_market was set even when flatten was False.
    """
    state.engine_exit_mode = ENGINE_EXIT_MODE
    m["engine_exit_mode"] = ENGINE_EXIT_MODE
    m["chop_size"] = CHOP_SIZE
    if m.get("flatten"):
        m["use_trail"] = False
        m["disable_trail"] = True
        m["trail_armed"] = False
        m["ignore_trail"] = True
        m["override_trail"] = True
        m["take_exit"] = TAKE_EXIT
        m["post_stc"] = True
        m["working_stc"] = False
    else:
        m.setdefault("use_trail", False)
        m.setdefault("disable_trail", False)
        m["take_exit"] = HOLD_EXIT
        m["post_stc"] = False
        m["working_stc"] = False
        m["flatten"] = False
        m["cancel_working_stc"] = True
    return m


def new_session(session_date: str) -> SessionState:
    """ET date rollover: clear consume, halt, fail streak, starters."""
    return SessionState(session_date=session_date)

"""Admin vs subscriber publish. GitHub is the source of truth.

Admin ledger = 100% of scored candidates.
Sub send = actionable subset only (armed_rip after quality gates).
SMS/Pushover and Tradier consume sub_alert_send only. Never BTO from admin rows.
skip_reason calls sub_action_skip_reason so SMS and BTO share one skip stack.
aligned_copy=false is SMS wording, not a non-trade.
Overlay SUB markers ARE the Pushover publications (same send_ts / et_hms).
Receipts do not SMS. Overlay does not plot a clock that was not texted.
Invariant: admin_n >= sub_n >= 0. overlay_sends == pushover_sends.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from engine.shared.gates import (
    CLOCK,
    CLOCK_FALLBACK,
    CLOCK_IDENTITY_LOCK,
    FOLLOW_DELIVERED_SUB,
    INFLIGHT_DOES_NOT_CONSUME,
    OVERLAY_IFF_SMS,
    OVERLAY_SUB_FORCES_QUEUED,
    SUB_CLOCK_IS_TRADIER,
    DTE_BOOK_ET,
    DTE_CUTOVER_ET,
    FAIL_90_REQUIRES_REVERSAL,
    PEAK_GIVEBACK_MIN_SEC,
    FRESH_FILL_SEC,
    HOLD_UNSTAMPED_FILL,
    MIN_HOLD_SEC,
    NEAR_CUTOVER_0DTE_ET,
    ONE_MIN_CLIMAX_FRAC,
    ONE_MIN_CLIMAX_USD,
    OPEN_FADE_WINDOW,
    RTH_END_ET,
    RTH_START_ET,
    SMS_AT_SEAL,
    STALL_1M_USD,
    locked_et_hhmm,
    seal_identity,
    signed_spy_deltas,
    sub_action_skip_reason,
    ledger_invariant as _qty_ledger_ok,
)

Direction = Literal["BULL", "BEAR"]

ADMIN_ROWS = "admin_alert_ledger"
PLOT = "sub_alert_send"
SMS_FROM = "sub_alert_send"
COPY = "armed_rip"
ALIGNED_COPY = False
CHANNELS_ALIGNED = True
SMS_IFF_SUB_SEND = True
PUSHOVER_FROM = SMS_FROM
SCORED_FANOUT = "candidate_only"

PUBLISH_ARM_SPY = 0.20
PUBLISH_ARM_SEC = 180.0
PRE_MOVE_WEAK_LO = 0.15
PRE_MOVE_WEAK_HI = 0.29
PRE_MOVE_STRONG = 0.50
CHASE_SPY = 0.50
SAME_DIR_LOCK_SEC = 1080.0
SKIP_ARM_BAR = True
SKIP_CHOPPY = True
SKIP_WEAK_PRE_MOVE = True
SKIP_STRONG_PRE_MOVE = True
SKIP_CHASE = True
# Continuous RTH. 09:41–09:59 used to be skip_outside_window (dark).
# 10:00–10:20 0DTE stays skip_0dte_open_fade; overlay scores 1DTE TREND.
RTH_WINDOW = (RTH_START_ET, RTH_END_ET)
MORNING_WINDOW = RTH_WINDOW
MIDDAY_WINDOW = RTH_WINDOW
PUBLISH_END_ET = RTH_END_ET


def ledger_invariant(admin_n: int, sub_n: int) -> bool:
    return _qty_ledger_ok(admin_n, sub_n)


def in_publish_window(et_hhmm: str) -> bool:
    if not et_hhmm:
        return False
    return RTH_START_ET <= et_hhmm <= RTH_END_ET


@dataclass(frozen=True)
class Candidate:
    """One scored overlay candidate. Always eligible for the admin ledger."""

    ts: float
    direction: Direction
    spy: float
    pre_move_spy: float
    armed: bool
    choppy: bool
    on_arm_bar: bool
    chase_spy: float
    et_hhmm: str
    copy: str = COPY
    same_dir_age_sec: float | None = None
    dte: int | None = None
    regime: str | None = None
    last_fail_hhmm: str | None = None
    rip_1m_spy: float | None = None
    trend_3m_spy: float | None = None


def with_spy_deltas(c: Candidate, closes: Sequence[float] | None) -> Candidate:
    """Production overlay: stamp signed 1m/3m from the last four 1-minute closes."""
    return replace(c, **signed_spy_deltas(closes))


def with_clock_identity(c: Candidate) -> Candidate:
    """Live unix ts overwrites et_hhmm. Replay/tiny ts keep the passed label."""
    return replace(c, et_hhmm=locked_et_hhmm(c.ts, c.et_hhmm))


def skip_reason(c: Candidate) -> str | None:
    """Why this candidate is admin-only (not a subscriber/Tradier send). None = sub send."""
    if not in_publish_window(c.et_hhmm):
        return "skip_outside_window"
    if c.copy != COPY:
        return "skip_copy"
    if not c.armed:
        return "skip_not_armed"
    act = sub_action_skip_reason(
        choppy=c.choppy,
        on_arm_bar=c.on_arm_bar,
        pre_move_spy=c.pre_move_spy,
        chase_spy=c.chase_spy,
        regime=c.regime,
        same_dir_age_sec=c.same_dir_age_sec,
        dte=c.dte,
        et_hhmm=c.et_hhmm,
        last_fail_hhmm=c.last_fail_hhmm,
        direction=c.direction,
        rip_1m_spy=c.rip_1m_spy,
        trend_3m_spy=c.trend_3m_spy,
    )
    if act is not None:
        return act
    return None


def decide_sub_send(c: Candidate) -> dict:
    c = with_clock_identity(c)
    ident = seal_identity(c.ts, c.direction, c.spy, c.et_hhmm)
    reason = skip_reason(c)
    if reason is not None:
        return {"send": False, "reason": reason, "plot": PLOT, "sms": False, **ident}
    return {
        "send": True,
        "reason": "sub_alert_send",
        "plot": PLOT,
        "sms": True,
        **ident,
    }


def _publication_clock(d: dict) -> str:
    return d.get("et_hms") or d.get("publication_et") or d.get("et_hhmm") or ""


def overlay_send_row(c: Candidate) -> dict | None:
    """Chart/table marker for one SUB send. None if this candidate is admin-only."""
    d = decide_sub_send(c)
    if not d["send"]:
        return None
    clock = _publication_clock(d)
    return {
        "send_ts": d["send_ts"],
        "et_hhmm": d["et_hhmm"],
        "et_hms": clock,
        "publication_et": clock,
        "direction": d["direction"],
        "spy": d["spy"],
        "strike_itm": d["strike_itm"],
        "strike_otm": d["strike_otm"],
        "strike_kind": d["strike_kind"],
        "plot": PLOT,
        "channel": "sub",
    }


def pushover_from_send(d: dict) -> dict | None:
    """Twilio/Pushover payload. None unless decide_sub_send send=True."""
    if not d.get("send") or not d.get("sms"):
        return None
    if FOLLOW_DELIVERED_SUB:
        raise AssertionError("follow_delivered_sub must stay false")
    clock = _publication_clock(d)
    return {
        "send_ts": d["send_ts"],
        "et_hhmm": d["et_hhmm"],
        "et_hms": clock,
        "publication_et": clock,
        "publication": f"publication available at {clock} ET",
        "direction": d["direction"],
        "spy": d["spy"],
        "strike_itm": d["strike_itm"],
        "strike_otm": d["strike_otm"],
        "strike_kind": d["strike_kind"],
        "channel": "sub",
        "sms": True,
        "plot": PLOT,
    }


def _channel_key(row: dict) -> tuple:
    clock = row.get("et_hms") or row.get("publication_et") or row.get("et_hhmm")
    return (
        round(float(row["send_ts"]), 3),
        clock,
        row["direction"],
        round(float(row["spy"]), 2),
    )


def overlay_matches_pushover(
    overlay_rows: Sequence[dict], pushover_rows: Sequence[dict]
) -> bool:
    """True iff overlay SUB markers are the Pushover publications, same order."""
    if not OVERLAY_IFF_SMS:
        return False
    if len(overlay_rows) != len(pushover_rows):
        return False
    return [_channel_key(r) for r in overlay_rows] == [
        _channel_key(r) for r in pushover_rows
    ]


@dataclass
class PublishLedgers:
    """Admin is the full set. Sub is the subset Tradier/SMS may act on."""

    session_date: str
    admin: list[Candidate] = field(default_factory=list)
    sub: list[Candidate] = field(default_factory=list)
    last_skip: str | None = None

    @property
    def admin_n(self) -> int:
        return len(self.admin)

    @property
    def sub_n(self) -> int:
        return len(self.sub)

    def overlay_sends(self) -> list[dict]:
        """SUB markers for alert_overlay.html (every view, including observational)."""
        return [row for c in self.sub if (row := overlay_send_row(c)) is not None]

    def pushover_sends(self) -> list[dict]:
        """Pushover/Twilio publications. Same list as overlay_sends()."""
        rows: list[dict] = []
        for c in self.sub:
            payload = pushover_from_send(decide_sub_send(c))
            if payload is not None:
                rows.append(payload)
        return rows

    def ingest(self, c: Candidate) -> dict:
        c = with_clock_identity(c)
        self.admin.append(c)
        d = decide_sub_send(c)
        overlay = overlay_send_row(c)
        pushover = pushover_from_send(d)
        if d["send"]:
            if overlay is None or pushover is None:
                raise AssertionError("sub send missing overlay or pushover payload")
            if not overlay_matches_pushover([overlay], [pushover]):
                raise AssertionError(
                    f"overlay/pushover clock split overlay={overlay} pushover={pushover}"
                )
            self.sub.append(c)
            self.last_skip = None
        else:
            if overlay is not None or pushover is not None:
                raise AssertionError("skip must not plot or SMS")
            self.last_skip = d["reason"]
        if not ledger_invariant(self.admin_n, self.sub_n):
            raise AssertionError(
                f"ledger inverted: admin_n={self.admin_n} sub_n={self.sub_n}"
            )
        if not overlay_matches_pushover(self.overlay_sends(), self.pushover_sends()):
            raise AssertionError("overlay_sends != pushover_sends")
        return {
            "admin_rows": ADMIN_ROWS,
            "plot": PLOT,
            "sms_from": SMS_FROM,
            "pushover_from": PUSHOVER_FROM,
            "aligned_copy": ALIGNED_COPY,
            "admin_n": self.admin_n,
            "sub_n": self.sub_n,
            "sent": d["send"],
            "sms": d["sms"],
            "reason": d["reason"],
            "overlay": overlay,
            "pushover": pushover,
            "send_ts": d["send_ts"],
            "et_hhmm": d["et_hhmm"],
            "et_hms": d.get("et_hms"),
            "publication_et": d.get("publication_et"),
            "invariant_ok": True,
            "overlay_iff_sms": True,
        }

    def health(self) -> dict:
        return {
            "plot": PLOT,
            "admin_rows": ADMIN_ROWS,
            "sms_from": SMS_FROM,
            "admin_n": self.admin_n,
            "sub_n": self.sub_n,
            "admin_n_ge_sub_n": self.admin_n >= self.sub_n,
            "invariant_ok": ledger_invariant(self.admin_n, self.sub_n),
            "aligned_copy": ALIGNED_COPY,
            "copy": COPY,
            "publish_arm_spy": PUBLISH_ARM_SPY,
            "publish_arm_sec": PUBLISH_ARM_SEC,
            "skip_choppy": SKIP_CHOPPY,
            "skip_chase": SKIP_CHASE,
            "chase_spy": CHASE_SPY,
            "skip_strong_pre_move": SKIP_STRONG_PRE_MOVE,
            "pre_move_strong": PRE_MOVE_STRONG,
            "same_dir_lock_sec": SAME_DIR_LOCK_SEC,
            "skip_0dte_open_fade": True,
            "open_fade_window": list(OPEN_FADE_WINDOW),
            "open_fade_1dte_book": True,
            "skip_0dte_near_cutover": True,
            "near_cutover_0dte_et": NEAR_CUTOVER_0DTE_ET,
            "dte_book_et": DTE_BOOK_ET,
            "trade_1dte_from_book_et": True,
            "dte_cutover_et": DTE_CUTOVER_ET,
            "rth_start_et": RTH_START_ET,
            "rth_end_et": RTH_END_ET,
            "hold_unstamped_fill": HOLD_UNSTAMPED_FILL,
            "fresh_fill_sec": FRESH_FILL_SEC,
            "fail_90_requires_reversal": FAIL_90_REQUIRES_REVERSAL,
            "peak_giveback_min_sec": PEAK_GIVEBACK_MIN_SEC,
            "min_hold_sec": MIN_HOLD_SEC,
            "channels_aligned": CHANNELS_ALIGNED,
            "sms_iff_sub_send": SMS_IFF_SUB_SEND,
            "skip_1min_rip": True,
            "one_min_rip_usd": 0.20,
            "one_min_climax_frac": ONE_MIN_CLIMAX_FRAC,
            "one_min_climax_usd": ONE_MIN_CLIMAX_USD,
            "trend_3m_min_usd": 0.20,
            "skip_1min_stall": True,
            "stall_1m_usd": STALL_1M_USD,
            "clock": CLOCK,
            "clock_fallback": CLOCK_FALLBACK,
            "clock_identity_lock": CLOCK_IDENTITY_LOCK,
            "sub_clock_is_tradier_clock": SUB_CLOCK_IS_TRADIER,
            "overlay_sub_forces_queued": OVERLAY_SUB_FORCES_QUEUED,
            "inflight_does_not_consume": INFLIGHT_DOES_NOT_CONSUME,
            "sms_at_seal": SMS_AT_SEAL,
            "follow_delivered_sub": FOLLOW_DELIVERED_SUB,
            "overlay_iff_sms": OVERLAY_IFF_SMS,
            "pushover_from": PUSHOVER_FROM,
        }

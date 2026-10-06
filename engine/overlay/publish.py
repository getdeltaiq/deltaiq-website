"""Admin vs subscriber publish. GitHub is the source of truth.

Admin ledger = 100% of scored candidates.
Sub send = actionable subset only (armed_rip after quality gates).
SMS and Tradier consume sub_alert_send only. Never BTO from admin rows.
skip_reason calls sub_action_skip_reason so SMS and BTO share one skip stack.
aligned_copy=false is SMS wording, not a non-trade.
Invariant: admin_n >= sub_n >= 0.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from engine.shared.gates import (
    DTE_CUTOVER_ET,
    NEAR_CUTOVER_0DTE_ET,
    OPEN_FADE_WINDOW,
    STALL_1M_USD,
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
MORNING_WINDOW = ("09:36", "09:40")
MIDDAY_WINDOW = ("10:00", "15:50")
PUBLISH_END_ET = "15:50"


def ledger_invariant(admin_n: int, sub_n: int) -> bool:
    return _qty_ledger_ok(admin_n, sub_n)


def in_publish_window(et_hhmm: str) -> bool:
    if MORNING_WINDOW[0] <= et_hhmm <= MORNING_WINDOW[1]:
        return True
    if MIDDAY_WINDOW[0] <= et_hhmm <= MIDDAY_WINDOW[1]:
        return True
    return False


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
    reason = skip_reason(c)
    if reason is not None:
        return {"send": False, "reason": reason, "plot": PLOT}
    return {"send": True, "reason": "sub_alert_send", "plot": PLOT}


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

    def ingest(self, c: Candidate) -> dict:
        self.admin.append(c)
        d = decide_sub_send(c)
        if d["send"]:
            self.sub.append(c)
            self.last_skip = None
        else:
            self.last_skip = d["reason"]
        if not ledger_invariant(self.admin_n, self.sub_n):
            raise AssertionError(
                f"ledger inverted: admin_n={self.admin_n} sub_n={self.sub_n}"
            )
        return {
            "admin_rows": ADMIN_ROWS,
            "plot": PLOT,
            "sms_from": SMS_FROM,
            "aligned_copy": ALIGNED_COPY,
            "admin_n": self.admin_n,
            "sub_n": self.sub_n,
            "sent": d["send"],
            "reason": d["reason"],
            "invariant_ok": True,
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
            "skip_0dte_near_cutover": True,
            "near_cutover_0dte_et": NEAR_CUTOVER_0DTE_ET,
            "dte_cutover_et": DTE_CUTOVER_ET,
            "channels_aligned": CHANNELS_ALIGNED,
            "sms_iff_sub_send": SMS_IFF_SUB_SEND,
            "skip_1min_rip": True,
            "one_min_rip_usd": 0.20,
            "trend_3m_min_usd": 0.20,
            "skip_1min_stall": True,
            "stall_1m_usd": STALL_1M_USD,
        }

"""Admin vs subscriber publish. GitHub is the source of truth.

Admin ledger = 100% of scored candidates.
Sub send = actionable subset only (armed_rip after quality gates).
SMS/Pushover SUB and Tradier consume sub_alert_send only. Never BTO from admin rows.
skip_reason calls sub_action_skip_reason so SMS and BTO share one skip stack.
aligned_copy=false is SUB SMS wording ("sign in to view"), not a non-trade.
Pushover admin = every scored candidate. Pushover sub = sub_alert_send only.
A SUB text must have an equivalent admin alert (same send_ts / direction / spy).
Admin may fire with no SUB. Health/outage never uses admin or SUB channels.
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
PUSHOVER_ADMIN = "admin"
PUSHOVER_SUB = "sub"
PUSHOVER_FROM = SMS_FROM
PUSHOVER_ADMIN_ALL = True
PUSHOVER_SUB_SUBSET = True
FOLLOW_DELIVERED_SUB = False
HEALTH_NEVER_SUB = True
HEALTH_NEVER_ADMIN = True
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
    ident = {
        "send_ts": c.ts,
        "et_hhmm": c.et_hhmm,
        "direction": c.direction,
        "spy": c.spy,
    }
    reason = skip_reason(c)
    if reason is not None:
        return {"send": False, "sms": False, "reason": reason, "plot": PLOT, **ident}
    return {
        "send": True,
        "sms": True,
        "reason": "sub_alert_send",
        "plot": PLOT,
        **ident,
    }


def _alert_key(row: dict) -> tuple:
    return (
        round(float(row["send_ts"]), 3),
        row["direction"],
        round(float(row["spy"]), 2),
        str(row.get("et_hhmm") or ""),
    )


def pushover_admin_from_candidate(c: Candidate, *, reason: str) -> dict:
    """Every scored candidate. Admin Pushover. Never a health/outage row."""
    return {
        "channel": PUSHOVER_ADMIN,
        "kind": "overlay_alert",
        "send_ts": c.ts,
        "et_hhmm": c.et_hhmm,
        "direction": c.direction,
        "spy": c.spy,
        "reason": reason,
        "sub_send": reason == "sub_alert_send",
        "plot": ADMIN_ROWS,
    }


def pushover_sub_from_send(d: dict) -> dict | None:
    """SUB_signals payload. None unless decide_sub_send send=True.

    aligned_copy=false is the SUB body ("publication available… sign in to view").
    Receipts / follow_delivered_sub must not call this.
    """
    if FOLLOW_DELIVERED_SUB:
        raise AssertionError("follow_delivered_sub must stay false")
    if not d.get("send") or not d.get("sms"):
        return None
    clock = d.get("et_hhmm") or ""
    return {
        "channel": PUSHOVER_SUB,
        "kind": "overlay_alert",
        "send_ts": d["send_ts"],
        "et_hhmm": clock,
        "direction": d["direction"],
        "spy": d["spy"],
        "reason": "sub_alert_send",
        "publication": f"New publication available at {clock} ET",
        "aligned_copy": ALIGNED_COPY,
        "sms": True,
        "plot": PLOT,
    }


def health_may_pushover(channel: str) -> bool:
    """Tasty /ES outage etc. Never SUB_signals. Never overlay admin."""
    return channel not in {PUSHOVER_ADMIN, PUSHOVER_SUB}


def sub_has_admin_twin(sub: dict, admin_rows: Sequence[dict]) -> bool:
    """Every SUB fire must have the equivalent admin alert."""
    want = _alert_key(sub)
    return any(_alert_key(row) == want for row in admin_rows)


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

    def admin_pushovers(self) -> list[dict]:
        """Admin Pushover: one row per scored candidate."""
        rows: list[dict] = []
        for c in self.admin:
            d = decide_sub_send(c)
            rows.append(pushover_admin_from_candidate(c, reason=d["reason"]))
        return rows

    def sub_pushovers(self) -> list[dict]:
        """SUB_signals: only the sub_alert_send subset."""
        rows: list[dict] = []
        for c in self.sub:
            payload = pushover_sub_from_send(decide_sub_send(c))
            if payload is not None:
                rows.append(payload)
        return rows

    def ingest(self, c: Candidate) -> dict:
        self.admin.append(c)
        d = decide_sub_send(c)
        admin_push = pushover_admin_from_candidate(c, reason=d["reason"])
        sub_push = pushover_sub_from_send(d)
        if d["send"]:
            if sub_push is None:
                raise AssertionError("sub send missing SUB Pushover payload")
            if not sub_has_admin_twin(sub_push, [admin_push]):
                raise AssertionError(
                    f"SUB without admin twin sub={sub_push} admin={admin_push}"
                )
            self.sub.append(c)
            self.last_skip = None
        else:
            if sub_push is not None:
                raise AssertionError("skip must not SMS SUB")
            self.last_skip = d["reason"]
        if not ledger_invariant(self.admin_n, self.sub_n):
            raise AssertionError(
                f"ledger inverted: admin_n={self.admin_n} sub_n={self.sub_n}"
            )
        for row in self.sub_pushovers():
            if not sub_has_admin_twin(row, self.admin_pushovers()):
                raise AssertionError("SUB Pushover missing admin twin")
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
            "admin_pushover": admin_push,
            "sub_pushover": sub_push,
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
            "pushover_from": PUSHOVER_FROM,
            "pushover_admin": PUSHOVER_ADMIN,
            "pushover_sub": PUSHOVER_SUB,
            "pushover_admin_all": PUSHOVER_ADMIN_ALL,
            "pushover_sub_subset": PUSHOVER_SUB_SUBSET,
            "follow_delivered_sub": FOLLOW_DELIVERED_SUB,
            "health_never_sub": HEALTH_NEVER_SUB,
            "health_never_admin": HEALTH_NEVER_ADMIN,
            "skip_1min_rip": True,
            "one_min_rip_usd": 0.20,
            "trend_3m_min_usd": 0.20,
            "skip_1min_stall": True,
            "stall_1m_usd": STALL_1M_USD,
        }

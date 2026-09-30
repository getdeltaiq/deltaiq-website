"""Canonical cover-us exec gates. Path: engine/shared/gates.py.

Railway tradier_exec must call these functions before any Tradier BTO/STC.
Do not re-implement a parallel knob set on Railway.

SMS and broker orders consume sub_alert_send only.
Admin ledger is the full candidate set. Sub is the action subset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

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
]


STARTER_NOTIONAL_USD = 2000.0
RISK_QTY_CAP = 16
SESSION_STARTER_CAP = 16  # omit-or-16; NEVER 8
TICKET_RISK_USD = 240.0
PROTECTIVE_STOP_USD = 0.15
QUOTE_GRACE_SEC = 8.0
CATASTROPHIC_OPTION_USD = 0.40
CATASTROPHIC_SPY = 0.50
FAIL_SEC = 90.0
BOUNCE_AGAINST_SPY = 0.30
OPEN_REVERSAL_WINDOW = ("10:00", "10:02")
EXTRA_BTO_MFE_USD = 0.20
SESSION_LOSS_HALT_USD = 500.0
CONSECUTIVE_FAIL_HALT = 4
# 9/28: 1DTE wiggles printed 4 FAILs and halted before the 0DTE 767-put.
# Count only 0DTE toward the streak. 1DTE still has envelope + dollar halt.
FAIL_STREAK_0DTE_ONLY = True
COOLDOWN_AFTER_FAIL_SEC = 480.0
# 9/28–9/29 misfire: next-day paper in CHOPPY/RANGE/unknown. 0DTE may still
# starter; 1DTE only when overlay says TREND (9/25 runner).
TREND_REGIMES = frozenset({"TREND", "TRENDING"})
# Cover-us exits are STC ladder → market. Do not hold a loser on trail.
ENGINE_EXIT_MODE = "ladder_to_market"
TAKE_EXIT = "ladder_to_market"  # never rest a loser on bid
CHOP_SIZE = False  # 9/29 sized into CHOPPY; refuse instead.
BTO_SOURCE = "sub_alert_send"  # never BTO from admin_alert_ledger
QUEUE_OPPOSITE = False  # exec must not invent opposite rips
# Rec scenario (9/15–9/29 replay): envelope + 0DTE halt + extra BTO to 16
# + 1DTE skip unless TREND. Railway must advertise this dict on /health
# and ignore leftover knobs (queue_opposite=true, take_exit=bid).
REC_BOOK_SHIP = "2026-09-30-rec"


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
        "cooldown_after_fail_sec": COOLDOWN_AFTER_FAIL_SEC,
        "session_loss_halt_usd": SESSION_LOSS_HALT_USD,
        "consecutive_fail_halt": CONSECUTIVE_FAIL_HALT,
        "protective_stop_usd": PROTECTIVE_STOP_USD,
        "ticket_risk_usd": TICKET_RISK_USD,
        "risk_qty_cap": RISK_QTY_CAP,
        "starter_notional_usd": STARTER_NOTIONAL_USD,
        "override_trail": True,
        "use_trail": False,
    }

# 2026-09-29 learn (exec still traded these): weak pre-move 1/9 −$160,
# pre-move 0.50+ 0/5 −$256, chase ≥ $0.50, CHOPPY 0/8 −$297.
# Overlay skipped some; BTO never saw the flags. Refuse at before_bto.
PRE_MOVE_WEAK_LO = 0.15
PRE_MOVE_WEAK_HI = 0.29
PRE_MOVE_STRONG = 0.50
CHASE_SPY = 0.50
SAME_DIR_LOCK_SEC = 1080.0

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


def counts_toward_fail_streak(dte: int | None) -> bool:
    """1DTE+ does not increment or reset the 0DTE fail streak."""
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
    """Recurring 9/28–9/29 hole: rapid FAIL re-entry and 1DTE in non-trend.

    Quality (CHOPPY / weak / strong / chase) is applied first. This layer
    is DTE + cooldown. Missing TREND on a known 1DTE is a refuse.
    """
    if in_fail_cooldown(last_fail_hhmm, et_hhmm):
        return "skip_cooldown_after_fail"
    if dte is not None and dte >= 1:
        reg = (regime or "").strip().upper()
        if reg not in TREND_REGIMES:
            return "skip_1dte_not_trend"
    return None


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
) -> str | None:
    """Return a skip code if this send is in a 9/29 losing slice. None = ok to size.

    Missing pre_move/chase is a refuse (skip_quality_unknown). 9/29 BTO
    defaulted those kwargs off and bought the overlay-skipped tape.
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
    return None


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


def envelope_hit(
    *,
    fill_px: float,
    mark_bid: float,
    qty: int,
    spy_adverse: float,
    seconds_since_fill: float,
    ticket_phase: str | None,
) -> EnvelopeReason | None:
    """Evaluate combined broker qty. qty is broker longs, not intended starter size."""
    if qty <= 0:
        return None
    if seconds_since_fill < QUOTE_GRACE_SEC:
        return None
    down = round(fill_px - mark_bid, 4)
    u = round(unrealized_dollars(mark_bid, fill_px, qty), 2)
    if down >= PROTECTIVE_STOP_USD:
        return "protective"
    if u <= -TICKET_RISK_USD:
        return "ticket_risk"
    if down >= CATASTROPHIC_OPTION_USD:
        return "cata_opt"
    if spy_adverse >= CATASTROPHIC_SPY:
        return "cata_spy"
    if seconds_since_fill >= FAIL_SEC and ticket_phase == "FAIL":
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

        1DTE FAILs/wins are invisible to consecutive_fail_n. 9/28 1DTE
        scratches had halted the 0DTE book before the 767-put printed.
        Any FAIL starts the 8-minute misfire cooldown.
        """
        self.session_realized_usd += realized_delta
        self.pending_entry = False
        self.inflight = False
        self.broker_qty = 0
        self.ticket_phase = None
        if phase == "FAIL" and et_hhmm:
            self.last_fail_hhmm = et_hhmm
        if counts_toward_fail_streak(dte):
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
        # 9/29: 0-for-N never tripped halt until a prior lift. First-line stop:
        # −$500 session cash or 4 consecutive 0DTE FAILs, no lift required.
        if self.session_realized_usd <= -SESSION_LOSS_HALT_USD:
            self._trip_halt("session_loss")
            return
        if self.consecutive_fail_n >= CONSECUTIVE_FAIL_HALT:
            self._trip_halt("consecutive_fail")

    def apply_broker_session_cash(self, realized_usd: float) -> None:
        """Halt accounting from Tradier option cash (STC proceeds − BTO cost), not engine book."""
        self.session_realized_usd = round(float(realized_usd), 2)
        self.refresh_halt()

    def recover_lost(self, broker_qty: int) -> str:
        """Adopt existing longs only. Never BTO. Never clear halt."""
        self.refresh_halt()
        if broker_qty > 0:
            self.broker_qty = broker_qty
            self.last_action = "recover_lost_adopt_stc_only"
            return "adopt_stc_only"
        self.last_action = "recover_lost_flat"
        return "flat"

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
) -> dict:
    """Single entry point before any Tradier buy_to_open."""
    if not ledger_ok_placeholder():
        pass
    state.refresh_halt()
    src = source_skip_reason(
        plot=plot, is_opposite=is_opposite, overlay_queued=overlay_queued
    )
    if src is not None:
        state.try_consume(send_ts, direction, count_starter=False, persist=persist)
        state.skip_quality_n += 1
        state.last_action = src
        return {"action": src, "qty": 0, "post": False}
    q = quality_skip_reason(
        choppy=choppy,
        on_arm_bar=on_arm_bar,
        pre_move_spy=pre_move_spy,
        chase_spy=chase_spy,
        regime=regime,
        same_dir_age_sec=same_dir_age_sec,
    )
    if q is not None:
        state.try_consume(send_ts, direction, count_starter=False, persist=persist)
        state.skip_quality_n += 1
        state.last_action = q
        return {"action": q, "qty": 0, "post": False}
    dte_val = dte if dte is not None else option_dte(option_symbol, state.session_date)
    if dte_val is None:
        # Rec 1DTE skip cannot fire without OCC expiry. Fail closed.
        state.try_consume(send_ts, direction, count_starter=False, persist=persist)
        state.skip_misfire_n += 1
        state.last_action = "skip_dte_unknown"
        return {"action": "skip_dte_unknown", "qty": 0, "post": False}
    mf = misfire_skip_reason(
        dte=dte_val,
        regime=regime,
        last_fail_hhmm=state.last_fail_hhmm,
        et_hhmm=et_hhmm,
    )
    if mf is not None:
        state.try_consume(send_ts, direction, count_starter=False, persist=persist)
        state.skip_misfire_n += 1
        state.last_action = mf
        return {"action": mf, "qty": 0, "post": False}
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
    }


def ledger_ok_placeholder() -> bool:
    return True


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
) -> dict:
    if qty > 0:
        state.broker_qty = int(qty)
    reason = envelope_hit(
        fill_px=fill_px,
        mark_bid=mark_bid,
        qty=qty,
        spy_adverse=spy_adverse,
        seconds_since_fill=seconds_since_fill,
        ticket_phase=ticket_phase,
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
                "override_trail": False,
                "ignore_trail": False,
                "engine_exit_mode": ENGINE_EXIT_MODE,
                "ticket_phase": state.ticket_phase,
                "extra_bto": extra_ok,
                "extra_bto_qty": extra if extra_ok else 0,
            },
        )
    if reason == "protective":
        state.protect_fills_n += 1
    elif reason == "ticket_risk":
        state.ticket_risk_hits_n += 1
    elif reason in ("cata_opt", "cata_spy"):
        state.cata_fills_n += 1
    state.last_action = f"flatten_{reason}"
    ladder = stc_ladder_prices(bid)
    state.last_stc_ladder = "market"
    return apply_manage_result(
        state,
        {
            "action": "flatten",
            "flatten": True,
            "reason": reason,
            "ladder": ladder,
            "last_stc_ladder": "market",
            "override_trail": True,
            "engine_exit_mode": ENGINE_EXIT_MODE,
            "ignore_trail": True,
        },
    )


def apply_manage_result(state: SessionState, m: dict) -> dict:
    """Force cover-us flatten off trail. Live 9/29 still advertised engine_exit_mode=trail."""
    state.engine_exit_mode = ENGINE_EXIT_MODE
    m["engine_exit_mode"] = ENGINE_EXIT_MODE
    m["chop_size"] = CHOP_SIZE
    if m.get("flatten") or m.get("override_trail") or m.get("ignore_trail"):
        m["use_trail"] = False
        m["disable_trail"] = True
        m["trail_armed"] = False
        m["ignore_trail"] = True
        m["override_trail"] = True
        m["take_exit"] = TAKE_EXIT
    else:
        m.setdefault("use_trail", False)
        m.setdefault("disable_trail", False)
        m["take_exit"] = TAKE_EXIT
    return m


def new_session(session_date: str) -> SessionState:
    """ET date rollover: clear consume, halt, fail streak, starters."""
    return SessionState(session_date=session_date)

"""Canonical cover-us exec gates. Path: engine/shared/gates.py.

Railway tradier_exec must call these functions before any Tradier BTO/STC.
Do not re-implement a parallel knob set on Railway.

SMS and broker orders consume sub_alert_send only.
Admin ledger is the full candidate set. Sub is the action subset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
COOLDOWN_AFTER_FAIL_SEC = 480.0
# Cover-us exits are STC ladder → market. Do not hold a loser on trail.
ENGINE_EXIT_MODE = "ladder_to_market"
CHOP_SIZE = False  # 9/29 sized into CHOPPY; refuse instead.

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


def hhmm_in_window(hhmm: str, start: str, end: str) -> bool:
    return start <= hhmm <= end


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

    def on_flatten(self, realized_delta: float, phase: str) -> None:
        """Flatten must NOT un-consume send_ts."""
        self.session_realized_usd += realized_delta
        self.pending_entry = False
        self.inflight = False
        self.broker_qty = 0
        self.ticket_phase = None
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
        # −$500 session cash or 4 consecutive FAILs, no lift required.
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
) -> dict:
    """Single entry point before any Tradier buy_to_open."""
    if not ledger_ok_placeholder():
        pass
    state.refresh_halt()
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
    return {
        "action": "bto",
        "qty": qty,
        "post": True,
        "client_order_id": state.last_bto_client_key,
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
    reason = envelope_hit(
        fill_px=fill_px,
        mark_bid=mark_bid,
        qty=qty,
        spy_adverse=spy_adverse,
        seconds_since_fill=seconds_since_fill,
        ticket_phase=ticket_phase,
    )
    if reason is None:
        return apply_manage_result(
            state,
            {
                "action": "hold",
                "flatten": False,
                "override_trail": False,
                "ignore_trail": False,
                "engine_exit_mode": ENGINE_EXIT_MODE,
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
        m["take_exit"] = "ladder_to_market"
    else:
        m.setdefault("use_trail", False)
        m.setdefault("disable_trail", False)
    return m


def new_session(session_date: str) -> SessionState:
    """ET date rollover: clear consume, halt, fail streak, starters."""
    return SessionState(session_date=session_date)

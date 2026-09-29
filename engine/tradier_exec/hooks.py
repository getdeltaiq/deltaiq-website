"""Runtime hooks. tradier_exec must call these; do not bypass.

Canonical module: engine.tradier_exec.hooks
Railway copies or imports this. Do not keep a second knob-only copy.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from engine.shared.gates import (
    CHOP_SIZE,
    ENGINE_EXIT_MODE,
    SessionState,
    apply_manage_result,
    decide_manage,
    decide_starter,
    new_session,
)

ET = ZoneInfo("America/New_York")
SESSION_KEEP_HALT_DATE = "2026-09-29"


def today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def enforce_keep_halt(state: SessionState) -> None:
    """9/29 stay halted even if a dashboard flag was flipped."""
    if today_et() != SESSION_KEEP_HALT_DATE:
        return
    state.session_halt = True
    state.session_lost_blocks_send = True
    state.halt_lifted = True
    state.session_halt_reason = "session_loss_after_lift"


def boot_state(loaded: SessionState | None = None) -> SessionState:
    d = today_et()
    st = loaded if loaded and loaded.session_date == d else new_session(d)
    enforce_keep_halt(st)
    st.engine_exit_mode = ENGINE_EXIT_MODE
    st.refresh_halt()
    return st


def before_bto(state: SessionState, **kwargs) -> dict:
    """Call immediately before any Tradier buy_to_open. If post is False, return."""
    enforce_keep_halt(state)
    return decide_starter(state, **kwargs)


def on_manage(state: SessionState, **kwargs) -> dict:
    """Call every manage tick with broker_qty, not intended qty.

    If flatten is True, ignore trail and walk STC ladder to market.
    """
    enforce_keep_halt(state)
    return apply_manage_result(state, decide_manage(state, **kwargs))


def before_extra_bto(state: SessionState, **kwargs) -> dict:
    """Size-up only when extra_bto_ok. Never a second starter."""
    enforce_keep_halt(state)
    ok = state.extra_bto_ok(
        int(kwargs.get("add_qty") or 0),
        float(kwargs.get("mfe_usd") or 0.0),
        float(kwargs.get("mark_bid") or 0.0),
        float(kwargs.get("avg_fill") or 0.0),
    )
    if not ok:
        state.last_action = "top_up_blocked"
        return {"post": False, "action": "top_up_blocked"}
    return {"post": True, "action": "extra_bto"}


def health_overlay(state: SessionState) -> dict:
    return {
        "gates_module": "engine.shared.gates",
        "decide_starter": True,
        "before_bto": True,
        "on_manage": True,
        "bto_requires_new_send": True,
        "skip_bounce_n": state.skip_bounce_n,
        "skip_quality_n": state.skip_quality_n,
        "consumed_send_ts": list(state.consumed.keys()),
        "consumed_survives_flatten": True,
        "consumed_never_delete": True,
        "session_starters_n": state.session_starters_n,
        "skipped_dup_submit_n": state.skipped_dup_submit_n,
        "protect_fills_n": state.protect_fills_n,
        "ticket_risk_hits_n": state.ticket_risk_hits_n,
        "last_stc_ladder": state.last_stc_ladder,
        "engine_exit_mode": ENGINE_EXIT_MODE,
        "override_trail": True,
        "use_trail": False,
        "chop_size": CHOP_SIZE,
        "skip_choppy_is_refuse": True,
        "session_halt": state.session_halt,
        "session_halt_reason": state.session_halt_reason,
        "session_lost_blocks_send": state.session_lost_blocks_send,
        "recover_lost_posts_bto": False,
    }

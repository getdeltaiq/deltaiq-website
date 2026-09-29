"""Runtime hooks. tradier_exec must call these; do not bypass.

Canonical module: engine.tradier_exec.hooks
Railway copies or imports this. Do not keep a second knob-only copy.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from engine.shared.gates import (
    SessionState,
    decide_manage,
    decide_starter,
    new_session,
)

ET = ZoneInfo("America/New_York")
SESSION_KEEP_HALT_DATE = "2026-09-29"


def today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def boot_state(loaded: SessionState | None = None) -> SessionState:
    d = today_et()
    st = loaded if loaded and loaded.session_date == d else new_session(d)
    if d == SESSION_KEEP_HALT_DATE:
        st.session_halt = True
        st.session_lost_blocks_send = True
        st.halt_lifted = True
        st.session_halt_reason = "session_loss_after_lift"
    return st


def before_bto(state: SessionState, **kwargs) -> dict:
    """Call immediately before any Tradier buy_to_open. If post is False, return."""
    return decide_starter(state, **kwargs)


def on_manage(state: SessionState, **kwargs) -> dict:
    """Call every manage tick with broker_qty, not intended qty."""
    return decide_manage(state, **kwargs)


def health_overlay(state: SessionState) -> dict:
    return {
        "gates_module": "engine.shared.gates",
        "decide_starter": True,
        "skip_bounce_n": state.skip_bounce_n,
        "consumed_send_ts": list(state.consumed.keys()),
        "consumed_survives_flatten": True,
        "session_starters_n": state.session_starters_n,
        "skipped_dup_submit_n": state.skipped_dup_submit_n,
        "protect_fills_n": state.protect_fills_n,
        "ticket_risk_hits_n": state.ticket_risk_hits_n,
        "last_stc_ladder": state.last_stc_ladder,
        "session_halt": state.session_halt,
        "session_halt_reason": state.session_halt_reason,
        "session_lost_blocks_send": state.session_lost_blocks_send,
        "recover_lost_posts_bto": False,
    }

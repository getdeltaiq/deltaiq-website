"""Runtime hooks. tradier_exec must call these; do not bypass.

Canonical module: engine.tradier_exec.hooks
Railway copies or imports this. Do not keep a second knob-only copy.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from engine.shared.gates import (
    ENGINE_EXIT_MODE,
    HOLD_EXIT,
    LIVE_KNOB_KEYS,
    SessionState,
    apply_manage_result,
    decide_manage,
    decide_starter,
    decide_stc,
    extra_bto_qty,
    may_post_stc,
    new_session,
    note_flatten_fill,
    note_ladder_step,
    option_dte,
    rec_book,
    rec_book_mismatch,
    rec_book_skip_reason,
    source_skip_reason,
)

ET = ZoneInfo("America/New_York")
SESSION_KEEP_HALT_DATE = "2026-09-29"


def today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def enforce_keep_halt(state: SessionState) -> None:
    """9/29 stay halted even if a dashboard flag was flipped."""
    if today_et() != SESSION_KEEP_HALT_DATE:
        return
    if state.session_date != SESSION_KEEP_HALT_DATE:
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


def _live_book_from(kwargs: dict) -> dict | None:
    """Pull Railway leftover knobs out of before_bto kwargs."""
    live = dict(kwargs.pop("live_book", None) or {})
    for key in list(kwargs):
        if key in LIVE_KNOB_KEYS:
            live[key] = kwargs.pop(key)
    return live or None


def before_bto(state: SessionState, **kwargs) -> dict:
    """Call immediately before any Tradier buy_to_open. If post is False, return."""
    enforce_keep_halt(state)
    live_book = _live_book_from(kwargs)
    return decide_starter(state, live_book=live_book, **kwargs)


def on_manage(state: SessionState, **kwargs) -> dict:
    """Call every manage tick with broker_qty, not intended qty.

    If flatten is True, ignore trail and walk STC ladder to market.
    If flatten is False, do not post STC. take_exit is hold.
    Live must not walk the ladder from this hook unless may_post_stc.
    """
    enforce_keep_halt(state)
    m = apply_manage_result(state, decide_manage(state, **kwargs))
    if not m.get("flatten"):
        return m
    qty = (
        int(kwargs["qty"])
        if kwargs.get("qty") is not None
        else int(state.broker_qty or 0)
    )
    age = state.ticket_age_sec(
        float(kwargs.get("seconds_since_fill") or 0.0), now=kwargs.get("now")
    )
    if may_post_stc(
        age,
        m.get("reason"),
        fill_px=kwargs.get("fill_px"),
        mark_bid=kwargs.get("mark_bid", kwargs.get("bid")),
        dte=kwargs.get("dte"),
        qty=qty,
        spy_adverse=kwargs.get("spy_adverse"),
        ticket_phase=kwargs.get("ticket_phase"),
        peak_unrealized=state.ticket_peak_unrealized,
        orphan_adopt=state.orphan_adopt,
        et_hhmm=kwargs.get("et_hhmm"),
    ):
        return m
    state.last_action = "hold_min_hold"
    return apply_manage_result(
        state,
        {
            "action": "hold",
            "flatten": False,
            "post_stc": False,
            "working_stc": False,
            "reason": "min_hold",
            "override_trail": False,
            "ignore_trail": False,
            "engine_exit_mode": ENGINE_EXIT_MODE,
            "ticket_phase": state.ticket_phase,
        },
    )


def before_stc(state: SessionState, **kwargs) -> dict:
    """Call immediately before any Tradier sell_to_close. If post is False, do not send.

    9/30 10:39 BTO fill must not rest a bid STC. Envelope only.
    """
    enforce_keep_halt(state)
    return decide_stc(state, **kwargs)


def before_extra_bto(state: SessionState, **kwargs) -> dict:
    """Size-up only when extra_bto_ok. Never a second starter.

    Default add_qty is remaining room to 16. 9/24 winners left 3–5 unused.
    """
    enforce_keep_halt(state)
    rec_skip = rec_book_skip_reason(_live_book_from(kwargs))
    if rec_skip is not None:
        state.last_action = rec_skip
        return {"post": False, "action": rec_skip, "qty": 0}
    src = source_skip_reason(
        plot=kwargs.get("plot"),
        is_opposite=bool(kwargs.get("is_opposite")),
        overlay_queued=bool(kwargs.get("overlay_queued")),
    )
    if src is not None:
        state.last_action = src
        return {"post": False, "action": src}
    dte = kwargs.get("dte")
    if dte is None:
        dte = option_dte(state.last_option_symbol, state.session_date)
    add = int(kwargs.get("add_qty") or 0)
    if add <= 0:
        add = extra_bto_qty(state.broker_qty, dte=dte)
    ok = state.extra_bto_ok(
        add,
        float(kwargs.get("mfe_usd") or 0.0),
        float(kwargs.get("mark_bid") or 0.0),
        float(kwargs.get("avg_fill") or 0.0),
        dte=dte,
    )
    if not ok:
        state.last_action = "top_up_blocked"
        return {"post": False, "action": "top_up_blocked", "qty": 0}
    state.last_action = "extra_bto"
    return {"post": True, "action": "extra_bto", "qty": add}


def health_overlay(state: SessionState) -> dict:
    rec = rec_book()
    mismatch = rec_book_mismatch()
    rec.update(
        {
            "gates_module": "engine.shared.gates",
            "code_ship": rec["rec_book_ship"],
            "rec_book_identity": mismatch == [],
            "rec_book_mismatch": mismatch,
            "decide_starter": True,
            "before_bto": True,
            "on_manage": True,
            "before_stc": True,
            "before_extra_bto": True,
            "bto_requires_new_send": True,
            "stc_requires_envelope": True,
            "stc_on_bto_fill": False,
            "working_stc_on_fill": False,
            "skip_already_flat": True,
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
            "skip_choppy_is_refuse": True,
            "skip_misfire_n": state.skip_misfire_n,
            "session_halt": state.session_halt,
            "session_halt_reason": state.session_halt_reason,
            "session_lost_blocks_send": state.session_lost_blocks_send,
            "recover_lost_posts_bto": False,
            "recover_lost_owned": True,
            "orphan_adopt_flattens": True,
            "flatten_exits": list(state.flatten_exits[-20:]),
            "last_flatten_exit": state.last_flatten_exit,
            "on_ladder_step": True,
            "on_flatten_fill": True,
            "after_recover_lost": True,
        }
    )
    return rec


def after_recover_lost(state: SessionState, kind: str, **kwargs) -> dict:
    """Only STC path after recover_lost(). Flat already means qty=0.

    10/15 C 12:45, D 13:12, G 15:20 sold $0.02 1DTE dips in 2–7s because
    live walked the ladder on recover_lost_flat / broker_flat. Those
    names are scratches. fresh_fill HOLDs. owned goes through on_manage
    (envelope only). adopt_stc_only still reaches before_stc.
    """
    enforce_keep_halt(state)
    k = str(kind or "")
    qty = kwargs.get("qty")
    if k in ("flat", "recover_lost_flat") or (qty is not None and int(qty) <= 0):
        return decide_stc(state, **{**kwargs, "qty": 0})
    if k == "fresh_fill":
        state.last_action = "recover_lost_fresh_fill"
        return apply_manage_result(
            state,
            {
                "action": "hold",
                "flatten": False,
                "post_stc": False,
                "working_stc": False,
                "reason": "fresh_fill",
                "override_trail": False,
                "ignore_trail": False,
                "engine_exit_mode": ENGINE_EXIT_MODE,
                "ticket_phase": state.ticket_phase,
                "take_exit": HOLD_EXIT,
            },
        )
    return on_manage(state, **kwargs)


def on_ladder_step(state: SessionState, **kwargs) -> dict | None:
    """Call after each posted STC ladder step (limit or market)."""
    return note_ladder_step(state, **kwargs)


def on_flatten_fill(state: SessionState, **kwargs) -> dict | None:
    """Call when the flatten STC fills. This is the durable exit print."""
    return note_flatten_fill(state, **kwargs)

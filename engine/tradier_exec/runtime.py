"""Runnable cover-us exec. Dry-run only — never places Tradier orders.

Wire this in live tradier_exec: starter() before every BTO, manage() every tick.
"""

from __future__ import annotations

from engine.shared.gates import ENGINE_EXIT_MODE, new_session
from engine.tradier_exec.hooks import (
    SESSION_KEEP_HALT_DATE,
    enforce_keep_halt,
    health_overlay,
    today_et,
)
from engine.tradier_exec.persist import ConsumedSends
from engine.shared.gates import decide_manage, decide_starter


class CoverUsExec:
    """In-process gates + durable consume. Does not talk to Tradier."""

    def __init__(
        self,
        db_path: str = ":memory:",
        session_date: str | None = None,
        *,
        keep_halt: bool | None = None,
    ) -> None:
        self.db = ConsumedSends(db_path)
        d = session_date or today_et()
        self.state = new_session(d)
        if keep_halt is None:
            keep_halt = today_et() == SESSION_KEEP_HALT_DATE
        if keep_halt:
            enforce_keep_halt(self.state)
        self.state.load_consumed(self.db.load(d))
        self.state.engine_exit_mode = ENGINE_EXIT_MODE
        self.state.refresh_halt()
        self.log: list[dict] = []

    def starter(self, **kwargs) -> dict:
        kwargs.setdefault("persist", self.db.insert)
        d = decide_starter(self.state, **kwargs)
        self.log.append({"kind": "starter", **d})
        if d.get("post"):
            self.state.inflight = True
        return d

    def on_bto_fill(self, qty: int, fill_px: float) -> None:
        self.state.inflight = False
        self.state.pending_entry = False
        self.state.broker_qty = int(qty)
        self.state.ticket_phase = "FAIL"
        self.log.append({"kind": "bto_fill", "qty": qty, "fill_px": fill_px})

    def manage(self, **kwargs) -> dict:
        m = decide_manage(self.state, **kwargs)
        self.log.append({"kind": "manage", **{k: v for k, v in m.items() if k != "ladder"}})
        return m

    def flatten(
        self, realized_delta: float, phase: str = "FAIL", *, dte: int | None = None
    ) -> None:
        self.state.on_flatten(realized_delta, phase, dte=dte)
        self.state.apply_broker_session_cash(self.state.session_realized_usd)
        self.log.append(
            {
                "kind": "flatten",
                "realized_delta": realized_delta,
                "session_realized_usd": self.state.session_realized_usd,
                "halt": self.state.session_halt,
                "halt_reason": self.state.session_halt_reason,
            }
        )

    def health(self) -> dict:
        return health_overlay(self.state)

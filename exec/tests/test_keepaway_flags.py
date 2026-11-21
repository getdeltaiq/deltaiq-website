"""Verified keep-away: morning CHOPPY+NO-TRADE and wait-clock MAE."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from engine.overlay.publish import Candidate, decide_sub_send, with_spy_deltas
from exec.gates import (
    WAIT_MAE_SEC,
    WAIT_MAE_SPY,
    decide_starter,
    morning_choppy_no_trade_skip_reason,
    new_session,
    prior_completed_minute_et,
    trend_confirm_kwargs,
    wait_mae_against,
    wait_mae_path_closes,
    wait_mae_skip_reason,
)

ET = ZoneInfo("America/New_York")

CLEAN_Q = dict(
    choppy=False,
    on_arm_bar=False,
    pre_move_spy=0.40,
    chase_spy=0.10,
    regime="TREND",
    plot="sub_alert_send",
    overlay_queued=True,
    is_opposite=False,
    option_symbol="SPY261111P00670000",
)


def _starter(**kwargs):
    kwargs.setdefault("send_spy", 670.00)
    kwargs.setdefault("spy", kwargs["send_spy"])
    kwargs.setdefault("bar_high", kwargs["spy"] + 0.10)
    kwargs.setdefault("bar_low", kwargs["spy"] - 0.10)
    kwargs.setdefault("ask", 1.20)
    kwargs.setdefault("dte", 0)
    kwargs.setdefault("direction", "BEAR")
    kwargs.setdefault("et_hhmm", "09:38")
    kwargs.setdefault("send_ts", 11.0)
    for key, val in CLEAN_Q.items():
        kwargs.setdefault(key, val)
    if "rip_1m_spy" not in kwargs:
        kwargs.update(trend_confirm_kwargs(kwargs["direction"]))
    return decide_starter(new_session("2026-11-11"), **kwargs)


def _closes(direction: str, spy: float) -> list[float]:
    sign = 1.0 if direction == "BULL" else -1.0
    return [spy - sign * 0.60, spy - sign * 0.42, spy - sign * 0.25, spy]


class TestMorningChoppyNoTrade(unittest.TestCase):
    def test_nov11_0938_choppy_no_trade_holds(self) -> None:
        self.assertEqual(
            morning_choppy_no_trade_skip_reason(
                et_hhmm="09:38",
                prior_regime="CHOPPY",
                prior_gate_verdict="NO-TRADE",
            ),
            "skip_morning_choppy_no_trade",
        )

    def test_nov12_not_choppy_sends(self) -> None:
        self.assertIsNone(
            morning_choppy_no_trade_skip_reason(
                et_hhmm="09:38",
                prior_regime="TREND",
                prior_gate_verdict="NO-TRADE",
            )
        )

    def test_nov13_choppy_go_sends(self) -> None:
        self.assertIsNone(
            morning_choppy_no_trade_skip_reason(
                et_hhmm="09:38",
                prior_regime="CHOPPY",
                prior_gate_verdict="GO",
            )
        )

    def test_missing_fields_fail_open(self) -> None:
        self.assertIsNone(
            morning_choppy_no_trade_skip_reason(
                et_hhmm="09:38",
                prior_regime="CHOPPY",
                prior_gate_verdict=None,
            )
        )

    def test_prior_minute_is_the_completed_bar(self) -> None:
        now = datetime(2026, 11, 11, 9, 38, 46, tzinfo=ET)
        self.assertEqual(prior_completed_minute_et(now), "09:37")

    def test_overlay_sms_holds_when_stamped(self) -> None:
        spy = 670.10
        c = with_spy_deltas(
            Candidate(
                ts=1.0,
                direction="BEAR",
                spy=spy,
                pre_move_spy=0.40,
                armed=True,
                choppy=False,
                on_arm_bar=False,
                chase_spy=0.10,
                et_hhmm="09:38",
                dte=0,
                prior_regime="CHOPPY",
                prior_gate_verdict="NO-TRADE",
            ),
            _closes("BEAR", spy),
        )
        d = decide_sub_send(c)
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_morning_choppy_no_trade")

    def test_bto_holds(self) -> None:
        out = _starter(
            prior_regime="CHOPPY",
            prior_gate_verdict="NO-TRADE",
        )
        self.assertFalse(out["post"])
        self.assertEqual(out["action"], "skip_morning_choppy_no_trade")

    def test_unstamped_morning_still_sends(self) -> None:
        out = _starter()
        self.assertTrue(out["post"])


class TestWaitMae(unittest.TestCase):
    def test_constants(self) -> None:
        self.assertEqual(WAIT_MAE_SEC, 180.0)
        self.assertEqual(WAIT_MAE_SPY, 0.20)

    def test_nov17_path_peak_not_live(self) -> None:
        mae = wait_mae_against("BULL", 670.00, [669.90, 669.65, 670.05], live_spy=670.05)
        self.assertGreaterEqual(mae + 1e-9, 0.35)
        send = datetime(2026, 11, 17, 10, 12, tzinfo=ET)
        now = send + timedelta(seconds=200)
        self.assertEqual(
            wait_mae_skip_reason(
                direction="BULL",
                send_spy=670.00,
                send_ts=send,
                now=now,
                path_closes=[669.90, 669.65, 670.05],
                live_spy=670.05,
            ),
            "skip_wait_mae",
        )

    def test_small_mae_still_sends(self) -> None:
        send = datetime(2026, 11, 13, 10, 20, tzinfo=ET)
        now = send + timedelta(seconds=200)
        self.assertIsNone(
            wait_mae_skip_reason(
                direction="BULL",
                send_spy=670.00,
                send_ts=send,
                now=now,
                path_closes=[669.96, 670.04],
                live_spy=670.04,
            )
        )

    def test_age_under_three_minutes_does_not_skip(self) -> None:
        send = datetime(2026, 11, 17, 10, 12, tzinfo=ET)
        now = send + timedelta(seconds=120)
        self.assertIsNone(
            wait_mae_skip_reason(
                direction="BULL",
                send_spy=670.00,
                send_ts=send,
                now=now,
                path_closes=[669.65],
                live_spy=669.65,
            )
        )

    def test_path_closes_from_bars(self) -> None:
        send = datetime(2026, 11, 17, 10, 12, tzinfo=ET)
        bars = [
            {"now": send - timedelta(minutes=1), "close": 670.20},
            {"now": send + timedelta(seconds=60), "close": 669.80},
            {"now": send + timedelta(seconds=120), "close": 669.65},
            {"now": send + timedelta(seconds=400), "close": 668.00},
        ]
        now = send + timedelta(seconds=200)
        closes = wait_mae_path_closes(bars, send_ts=send, now_ts=now)
        self.assertEqual(closes, [669.80, 669.65])

    def test_bto_holds_after_wait(self) -> None:
        send = 1_784_000_000.0
        out = _starter(
            send_ts=send,
            direction="BULL",
            send_spy=670.00,
            spy=670.05,
            et_hhmm="10:15",
            option_symbol="SPY261117C00670000",
            path_closes=[669.90, 669.65, 670.05],
            now_ts=send + 200.0,
        )
        self.assertFalse(out["post"])
        self.assertEqual(out["action"], "skip_wait_mae")


if __name__ == "__main__":
    unittest.main()

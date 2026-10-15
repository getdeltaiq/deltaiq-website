"""Unit tests for cover-us gates. These encode 2026-09-29 failure modes without account P&L."""

from __future__ import annotations

import unittest

from exec.gates import (
    CHASE_SPY,
    CHOP_SIZE,
    ONE_MIN_CLIMAX_FRAC,
    ONE_MIN_CLIMAX_USD,
    TREND_3M_MIN_USD,
    PEAK_GIVEBACK_MIN_SEC,
    PEAK_GIVEBACK_USD,
    SESSION_LOSS_HALT_USD,
    STALL_1M_USD,
    TICKET_RISK_USD,
    SessionState,
    bounce_against,
    clock_quality_skip_reason,
    et_hhmm_from_ts,
    et_hms_from_ts,
    extra_bto_qty,
    locked_et_hhmm,
    may_post_stc,
    seal_identity,
    strike_copy,
    starter_dte_for_clock,
    decide_manage,
    decide_starter,
    decide_stc,
    envelope_hit,
    ledger_invariant,
    new_session,
    one_bar_rip_skip_reason,
    signed_spy_deltas,
    quality_skip_reason,
    rec_book,
    starter_qty,
    stc_ladder_prices,
    sub_action_skip_reason,
    trend_confirm_kwargs,
)

# 9/29 fail-closed: missing pre_move/chase is a refuse. Tests that should
# reach bounce/halt/BTO must pass a clean quality slice (0.30–0.49 pre-move).
CLEAN_Q = dict(
    choppy=False,
    on_arm_bar=False,
    pre_move_spy=0.40,
    chase_spy=0.10,
    regime="TREND",
    plot="sub_alert_send",
    overlay_queued=True,
    is_opposite=False,
    option_symbol="SPY260929P00765000",
    rip_1m_spy=-0.25,
    trend_3m_spy=-0.60,
)


class QtyTests(unittest.TestCase):
    def test_two_thousand_capped_at_16(self):
        self.assertEqual(starter_qty(1.42), 14)
        self.assertEqual(starter_qty(1.14), 16)
        self.assertEqual(starter_qty(0.50), 16)
        self.assertEqual(starter_qty(3.03), 6)

    def test_option_dte_spy(self):
        from exec.gates import option_dte

        self.assertEqual(option_dte("SPY260924P00766000", "2026-09-24"), 0)
        self.assertEqual(option_dte("SPY260929P00767000", "2026-09-28"), 1)
        self.assertEqual(option_dte("SPY260928C00771000", "2026-09-25"), 3)


class LedgerTests(unittest.TestCase):
    def test_sub_is_subset(self):
        self.assertTrue(ledger_invariant(95, 13))
        self.assertTrue(ledger_invariant(13, 13))
        self.assertFalse(ledger_invariant(5, 13))


class ConsumeTests(unittest.TestCase):
    def test_second_worker_does_not_post(self):
        s = new_session("2026-09-29")
        a = decide_starter(
            s,
            send_ts=1790694120.0,
            direction="BEAR",
            send_spy=764.78,
            spy=764.78,
            bar_high=764.80,
            bar_low=764.68,
            et_hhmm="11:02",
            ask=1.42,
            **CLEAN_Q,
        )
        self.assertTrue(a["post"])
        self.assertEqual(a["qty"], 14)
        s.pending_entry = False
        s.inflight = False
        # flatten without un-consume
        s.on_flatten(-434.0, "FAIL")
        self.assertIn(1790694120.0, s.consumed)
        b = decide_starter(
            s,
            send_ts=1790694120.0,
            direction="BEAR",
            send_spy=764.78,
            spy=764.90,
            bar_high=765.00,
            bar_low=764.70,
            et_hhmm="11:13",
            ask=1.57,
            **CLEAN_Q,
        )
        self.assertFalse(b["post"])
        self.assertEqual(b["action"], "skip_dup_send_ts")
        self.assertEqual(s.session_starters_n, 1)
        self.assertGreaterEqual(s.skipped_dup_submit_n, 1)

    def test_inflight_mutex_blocks_454ms_double(self):
        s = new_session("2026-09-29")
        a = decide_starter(
            s,
            send_ts=1.0,
            direction="BEAR",
            send_spy=764.78,
            spy=764.78,
            bar_high=764.80,
            bar_low=764.60,
            et_hhmm="11:02",
            ask=1.42,
            **CLEAN_Q,
        )
        self.assertTrue(a["post"])
        s.inflight = True
        s.pending_entry = True
        s.broker_qty = 14
        b = decide_starter(
            s,
            send_ts=1.0,
            direction="BEAR",
            send_spy=764.78,
            spy=764.78,
            bar_high=764.80,
            bar_low=764.60,
            et_hhmm="11:02",
            ask=1.39,
            **CLEAN_Q,
        )
        self.assertFalse(b["post"])

    def test_recover_lost_never_bto(self):
        s = new_session("2026-09-29")
        # Unstamped broker qty is a live fill (10/8 13:20), not an orphan.
        self.assertEqual(s.recover_lost(10), "fresh_fill")
        self.assertEqual(s.last_action, "recover_lost_fresh_fill")
        self.assertNotIn("bto", s.last_action)


class BounceTests(unittest.TestCase):
    def test_ten_am_bear_reversal_bar_skipped_and_consumed(self):
        # 0DTE 10:00 is open-fade. Bounce still applies to 1DTE TREND in
        # 10:00–10:02 (exec-only live tape after the shared skip stack).
        s = new_session("2026-09-29")
        args = dict(
            send_ts=10.0,
            direction="BEAR",
            send_spy=764.78,
            spy=765.31,
            bar_high=765.63,
            bar_low=764.68,
            et_hhmm="10:00",
            ask=1.42,
            **CLEAN_Q,
        )
        args["option_symbol"] = "SPY260930P00765000"
        args["dte"] = 1
        args["regime"] = "TREND"
        d = decide_starter(s, **args)
        self.assertEqual(d["action"], "skip_bounce_against")
        self.assertFalse(d["post"])
        self.assertEqual(s.skip_bounce_n, 1)
        self.assertEqual(s.session_starters_n, 0)
        self.assertIn(10.0, s.consumed)
        again_args = dict(args)
        again_args.update(
            spy=764.80,
            bar_high=764.85,
            bar_low=764.70,
            et_hhmm="10:13",
            ask=1.57,
        )
        again = decide_starter(s, **again_args)
        self.assertFalse(again["post"])

    def test_with_trend_bear_posts_one_starter(self):
        s = new_session("2026-09-29")
        d = decide_starter(
            s,
            send_ts=11.0,
            direction="BEAR",
            send_spy=764.58,
            spy=764.41,
            bar_high=764.57,
            bar_low=764.20,
            et_hhmm="11:02",
            ask=1.50,
            **CLEAN_Q,
        )
        self.assertTrue(d["post"])
        self.assertEqual(d["qty"], 13)


class EnvelopeTests(unittest.TestCase):
    def test_sixteen_lot_021_down_hits_before_336(self):
        # 16 * 0.21 * 100 = 336; must hit at 0.15 / 240, not hold to 0.93
        self.assertEqual(
            envelope_hit(
                fill_px=1.14,
                mark_bid=0.99,
                qty=16,
                spy_adverse=0.10,
                seconds_since_fill=9,
                ticket_phase="FAIL",
            ),
            "protective",
        )
        self.assertEqual(
            envelope_hit(
                fill_px=1.14,
                mark_bid=0.99,
                qty=16,
                spy_adverse=0.10,
                seconds_since_fill=9,
                ticket_phase="FAIL",
            )
            is not None,
            True,
        )
        u_ok = envelope_hit(
            fill_px=1.14,
            mark_bid=0.93,
            qty=16,
            spy_adverse=0.20,
            seconds_since_fill=240,
            ticket_phase="FAIL",
        )
        self.assertIn(u_ok, ("protective", "ticket_risk", "cata_opt", "fail_90"))

    def test_grace_prevents_immediate_flatten(self):
        self.assertIsNone(
            envelope_hit(
                fill_px=1.42,
                mark_bid=1.25,
                qty=28,
                spy_adverse=0.5,
                seconds_since_fill=5,
                ticket_phase="FAIL",
            )
        )

    def test_combined_28_lot_hits_ticket_risk(self):
        # 28 * 0.09 * 100 = 252 > 240
        self.assertEqual(
            envelope_hit(
                fill_px=1.405,
                mark_bid=1.315,
                qty=28,
                spy_adverse=0.20,
                seconds_since_fill=11,
                ticket_phase="FAIL",
            ),
            "ticket_risk",
        )

    def test_manage_walks_to_market(self):
        s = new_session("2026-09-29")
        r = decide_manage(
            s,
            fill_px=1.14,
            mark_bid=0.99,
            qty=16,
            spy_adverse=0.10,
            seconds_since_fill=12,
            ticket_phase="FAIL",
            bid=0.99,
        )
        self.assertTrue(r["flatten"])
        self.assertEqual(r["last_stc_ladder"], "market")
        self.assertEqual(r["engine_exit_mode"], "ladder_to_market")
        self.assertTrue(r["override_trail"])
        self.assertTrue(r["ignore_trail"])
        self.assertTrue(r["disable_trail"])
        self.assertFalse(r["use_trail"])
        self.assertEqual(r["take_exit"], "ladder_to_market")
        self.assertEqual(r["ladder"][-1][0], "market")
        self.assertEqual(s.protect_fills_n, 1)
        self.assertEqual(s.last_stc_ladder, "market")
        self.assertEqual(s.engine_exit_mode, "ladder_to_market")
        self.assertTrue(r["post_stc"])


    def test_hold_tick_does_not_advertise_ladder_exit(self):
        s = new_session("2026-09-30")
        s.on_bto_fill(10, 1.84, now=1_000.0)
        m = decide_manage(
            s,
            fill_px=1.84,
            mark_bid=1.83,
            qty=10,
            spy_adverse=0.05,
            seconds_since_fill=3,
            ticket_phase="FAIL",
            bid=1.83,
            now=1_003.0,
        )
        self.assertFalse(m["flatten"])
        self.assertFalse(m["post_stc"])
        self.assertEqual(m["take_exit"], "hold")
        self.assertTrue(m["cancel_working_stc"])


    def test_before_stc_blocks_bid_scratch_on_fill(self):
        from engine.tradier_exec.hooks import before_stc

        s = new_session("2026-09-30")
        s.on_bto_fill(10, 1.84, now=1_000.0)
        d = before_stc(
            s,
            fill_px=1.84,
            mark_bid=1.83,
            qty=10,
            spy_adverse=0.05,
            seconds_since_fill=3,
            ticket_phase="FAIL",
            bid=1.83,
            now=1_003.0,
        )
        self.assertFalse(d["post"])
        self.assertFalse(d["flatten"])
        self.assertEqual(d["reason"], "stc_requires_envelope")


    def test_before_stc_skips_when_already_flat(self):
        from engine.tradier_exec.hooks import before_stc

        s = new_session("2026-09-30")
        s.on_bto_fill(14, 1.37, now=1_000.0)
        d = before_stc(
            s,
            fill_px=1.37,
            mark_bid=1.22,
            qty=0,
            spy_adverse=0.20,
            seconds_since_fill=20,
            ticket_phase="FAIL",
            bid=1.22,
            now=1_020.0,
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["reason"], "skip_already_flat")
        self.assertTrue(d["cancel_working_stc"])


    def test_stale_prior_fail_clock_cannot_flatten_new_fill(self):
        s = new_session("2026-09-30")
        s.on_bto_fill(10, 1.84, now=10_000.0)
        m = decide_manage(
            s,
            fill_px=1.84,
            mark_bid=1.83,
            qty=10,
            spy_adverse=0.10,
            seconds_since_fill=500,
            ticket_phase="FAIL",
            bid=1.83,
            now=10_005.0,
        )
        self.assertFalse(m["flatten"])
        self.assertEqual(m["take_exit"], "hold")


    def test_recover_lost_fresh_fill_is_not_an_orphan(self):
        s = new_session("2026-09-30")
        s.on_bto_fill(10, 1.84, now=1_000.0)
        self.assertEqual(s.recover_lost(10, now=1_003.0), "fresh_fill")
        self.assertEqual(s.last_action, "recover_lost_fresh_fill")

    def test_ladder_order(self):
        steps = stc_ladder_prices(1.25)
        self.assertEqual(steps[0], ("limit", 1.25))
        self.assertEqual(steps[1], ("limit", 1.20))
        self.assertEqual(steps[2], ("limit", 1.15))
        self.assertEqual(steps[3], ("market", None))


class HaltTests(unittest.TestCase):
    def test_does_not_rehalt_on_old_loss_until_additional_750(self):
        s = new_session("2026-09-29")
        s.session_realized_usd = -1111.0
        s.halt_baseline_usd = -1111.0
        s.halt_lifted = True
        s.refresh_halt()
        self.assertFalse(s.session_halt)
        s.session_realized_usd = -1111.0 - 500.0
        s.refresh_halt()
        self.assertFalse(s.session_halt)
        s.session_realized_usd = -1111.0 - 750.0
        s.refresh_halt()
        self.assertTrue(s.session_halt)
        self.assertEqual(s.session_halt_reason, "session_loss_after_lift")
        self.assertTrue(s.session_lost_blocks_send)

    def test_flatten_does_not_clear_halt(self):
        s = new_session("2026-09-29")
        s.halt_lifted = True
        s.halt_baseline_usd = 0.0
        s.on_flatten(-750.0, "FAIL")
        self.assertTrue(s.session_halt)
        s.on_flatten(0.0, "FAIL")
        self.assertTrue(s.session_halt)
        self.assertFalse(s.may_starter_bto())

    def test_halt_stays_on_after_accounting_reset(self):
        s = new_session("2026-09-29")
        s.halt_lifted = True
        s.halt_baseline_usd = 0.0
        s.on_flatten(-750.0, "FAIL")
        self.assertTrue(s.session_halt)
        s.session_realized_usd = 0.0
        s.refresh_halt()
        self.assertTrue(s.session_halt)
        self.assertFalse(s.may_starter_bto())

    def test_halt_skip_consumes_send_so_lift_cannot_recycle(self):
        s = new_session("2026-09-29")
        s.session_halt = True
        s.session_lost_blocks_send = True
        d = decide_starter(
            s,
            send_ts=15.44,
            direction="BEAR",
            send_spy=764.05,
            spy=764.05,
            bar_high=764.10,
            bar_low=764.00,
            et_hhmm="15:44",
            ask=2.59,
            **{**CLEAN_Q, "option_symbol": "SPY260930P00765000"},
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_halt_or_inflight")
        self.assertIn(15.44, s.consumed)
        self.assertEqual(s.session_starters_n, 0)
        s.session_halt = False
        s.session_lost_blocks_send = False
        again = decide_starter(
            s,
            send_ts=15.44,
            direction="BEAR",
            send_spy=764.05,
            spy=764.05,
            bar_high=764.10,
            bar_low=764.00,
            et_hhmm="15:45",
            ask=2.59,
            **{**CLEAN_Q, "option_symbol": "SPY260930P00765000"},
        )
        self.assertFalse(again["post"])

    def test_recover_lost_does_not_clear_halt_or_bto(self):
        s = new_session("2026-09-29")
        s.session_halt = True
        s.session_lost_blocks_send = True
        s.session_halt_reason = "session_loss_after_lift"
        self.assertEqual(s.recover_lost(0), "flat")
        self.assertTrue(s.session_halt)
        self.assertFalse(s.may_starter_bto())

    def test_broker_cash_trips_halt(self):
        s = new_session("2026-09-29")
        s.halt_lifted = True
        s.halt_baseline_usd = 0.0
        s.apply_broker_session_cash(-1652.98)
        self.assertTrue(s.session_halt)
        self.assertEqual(s.session_halt_reason, "session_loss_after_lift")

    def test_first_line_session_loss_halts_without_lift(self):
        s = new_session("2026-09-29")
        s.on_flatten(-500.0, "FAIL")
        self.assertFalse(s.session_halt)
        s.on_flatten(-250.0, "FAIL")
        self.assertTrue(s.session_halt)
        self.assertEqual(s.session_halt_reason, "session_loss")
        self.assertFalse(s.may_starter_bto())

    def test_three_ticket_risk_misses_stay_under_halt(self):
        s = new_session("2026-09-30")
        s.on_flatten(-240.0, "FAIL")
        s.on_flatten(-240.0, "FAIL")
        s.on_flatten(-240.0, "FAIL")
        self.assertEqual(s.session_realized_usd, -720.0)
        self.assertFalse(s.session_halt)
        self.assertEqual(s.consecutive_fail_n, 3)

    def test_four_fails_halt_without_lift(self):
        s = new_session("2026-09-29")
        for _ in range(3):
            s.on_flatten(-40.0, "FAIL")
        self.assertFalse(s.session_halt)
        s.on_flatten(-40.0, "FAIL")
        self.assertTrue(s.session_halt)
        self.assertEqual(s.session_halt_reason, "consecutive_fail")

    def test_1dte_fails_do_not_count_toward_streak(self):
        s = new_session("2026-09-28")
        for _ in range(4):
            s.on_flatten(-40.0, "FAIL", dte=1)
        self.assertFalse(s.session_halt)
        self.assertEqual(s.consecutive_fail_n, 0)
        for _ in range(3):
            s.on_flatten(-40.0, "FAIL", dte=0)
        self.assertFalse(s.session_halt)
        s.on_flatten(-40.0, "FAIL", dte=0)
        self.assertTrue(s.session_halt)

    def test_1dte_fail_does_not_reset_0dte_streak(self):
        s = new_session("2026-09-28")
        s.on_flatten(-40.0, "FAIL", dte=0)
        s.on_flatten(-40.0, "FAIL", dte=0)
        s.on_flatten(-40.0, "FAIL", dte=0)
        s.on_flatten(-40.0, "FAIL", dte=1)
        self.assertEqual(s.consecutive_fail_n, 3)
        s.on_flatten(-40.0, "FAIL", dte=0)
        self.assertTrue(s.session_halt)


class QualityLearnTests(unittest.TestCase):
    """2026-09-29: overlay skipped these; Tradier still bought them."""

    def _base(self, s, **kw):
        args = dict(
            send_ts=50.0,
            direction="BEAR",
            send_spy=764.05,
            spy=764.05,
            bar_high=764.10,
            bar_low=764.00,
            et_hhmm="11:02",
            ask=2.59,
            **CLEAN_Q,
        )
        args.update(kw)
        if "rip_1m_spy" not in kw:
            args.update(trend_confirm_kwargs(args["direction"]))
        return decide_starter(s, **args)

    def test_choppy_regime_is_not_a_starter(self):
        s = new_session("2026-09-29")
        d = self._base(s, regime="CHOPPY")
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_choppy")
        self.assertEqual(s.skip_quality_n, 1)
        self.assertEqual(s.session_starters_n, 0)

    def test_weak_pre_move_is_not_a_starter(self):
        s = new_session("2026-09-29")
        d = self._base(s, pre_move_spy=0.22)
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_weak_pre_move")

    def test_strong_pre_move_is_not_a_starter(self):
        s = new_session("2026-09-29")
        d = self._base(s, pre_move_spy=0.55)
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_strong_pre_move")

    def test_chase_is_not_a_starter(self):
        s = new_session("2026-09-29")
        d = self._base(s, chase_spy=0.55)
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_chase")

    def test_missing_quality_fields_are_not_a_starter(self):
        s = new_session("2026-09-29")
        d = self._base(s, pre_move_spy=None, chase_spy=None)
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_quality_unknown")

    def test_clean_send_still_posts(self):
        s = new_session("2026-09-29")
        d = self._base(s, pre_move_spy=0.40, chase_spy=0.10, choppy=False)
        self.assertTrue(d["post"])
        self.assertIsNone(
            quality_skip_reason(
                choppy=False,
                pre_move_spy=0.40,
                chase_spy=0.10,
                direction="BEAR",
                rip_1m_spy=-0.25,
                trend_3m_spy=-0.60,
            )
        )

    def test_admin_ledger_is_not_a_starter(self):
        s = new_session("2026-09-29")
        d = self._base(s, plot="admin_alert_ledger")
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_not_sub")

    def test_missing_plot_is_not_a_starter(self):
        s = new_session("2026-09-29")
        d = self._base(s, plot=None)
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_not_sub")

    def test_exec_queued_opposite_is_not_a_starter(self):
        s = new_session("2026-09-29")
        d = self._base(s, plot="admin_alert_ledger", is_opposite=True, overlay_queued=False)
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_not_sub")

    def test_overlay_sub_opposite_posts_even_if_queued_flag_missing(self):
        """10/15 B 11:47 BULL after A 10:26 BEAR. SUB plot is the queue."""
        s = new_session("2026-10-15")
        d = self._base(s, is_opposite=True, overlay_queued=False)
        self.assertTrue(d["post"])

    def test_overlay_queued_opposite_still_posts(self):
        s = new_session("2026-09-29")
        d = self._base(s, is_opposite=True, overlay_queued=True)
        self.assertTrue(d["post"])


class MisfireTests(unittest.TestCase):
    """9/28 1DTE chop + rapid FAIL re-entry. 9/25 TREND 1DTE runner still posts."""

    def _base(self, s, **kw):
        args = dict(
            send_ts=60.0,
            direction="BEAR",
            send_spy=765.00,
            spy=765.00,
            bar_high=765.02,
            bar_low=764.90,
            et_hhmm="11:15",
            ask=2.54,
            **CLEAN_Q,
        )
        args.update(kw)
        if "rip_1m_spy" not in kw:
            args.update(trend_confirm_kwargs(args["direction"]))
        return decide_starter(s, **args)

    def test_1dte_without_trend_is_refused(self):
        s = new_session("2026-09-28")
        d = self._base(
            s,
            option_symbol="SPY260929P00768000",
            regime="RANGE",
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_1dte_not_trend")
        self.assertEqual(s.skip_misfire_n, 1)
        self.assertEqual(s.session_starters_n, 0)

    def test_1dte_missing_regime_is_refused(self):
        s = new_session("2026-09-28")
        d = self._base(
            s,
            option_symbol="SPY260929P00768000",
            regime=None,
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_1dte_not_trend")

    def test_1dte_trend_still_posts(self):
        s = new_session("2026-09-25")
        d = self._base(
            s,
            option_symbol="SPY260928C00771000",
            regime="TREND",
            direction="BULL",
        )
        self.assertTrue(d["post"])
        self.assertEqual(s.skip_misfire_n, 0)

    def test_after_cutover_1dte_range_posts(self):
        """9/30 14:06 BEAR: afternoon book is 1DTE. Do not require TREND."""
        s = new_session("2026-09-30")
        d = self._base(
            s,
            et_hhmm="14:06",
            option_symbol="SPY261001P00767000",
            regime="RANGE",
        )
        self.assertTrue(d["post"])
        self.assertEqual(s.skip_misfire_n, 0)

    def test_cutover_on_the_clock_is_1dte(self):
        s = new_session("2026-09-30")
        d = self._base(
            s,
            et_hhmm="12:45",
            option_symbol="SPY261001P00767000",
            regime="RANGE",
            choppy=False,
        )
        self.assertTrue(d["post"])

    def test_after_cutover_0dte_is_refused(self):
        s = new_session("2026-09-30")
        d = self._base(
            s,
            et_hhmm="14:06",
            option_symbol="SPY260930P00767000",
            regime="RANGE",
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_0dte_after_cutover")
        self.assertEqual(s.skip_misfire_n, 1)

    def test_0dte_without_regime_still_posts(self):
        s = new_session("2026-09-24")
        d = self._base(
            s,
            option_symbol="SPY260924P00766000",
            regime=None,
        )
        self.assertTrue(d["post"])

    def test_cooldown_blocks_the_next_eight_minutes(self):
        s = new_session("2026-09-29")
        s.on_flatten(-165.0, "FAIL", dte=0, et_hhmm="10:00")
        blocked = self._base(s, send_ts=10.03, et_hhmm="10:03")
        self.assertFalse(blocked["post"])
        self.assertEqual(blocked["action"], "skip_cooldown_after_fail")
        open_ok = self._base(s, send_ts=10.21, et_hhmm="10:21")
        self.assertTrue(open_ok["post"])

    def test_missing_option_symbol_is_refused(self):
        s = new_session("2026-09-28")
        d = self._base(s, option_symbol=None, dte=None)
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_dte_unknown")
        self.assertEqual(s.skip_misfire_n, 1)


class PersistConsumeTests(unittest.TestCase):
    def test_sql_conflict_is_dup_not_second_bto(self):
        s = new_session("2026-09-29")
        store = {(s.session_date, 7.0)}

        def persist(session_date, send_ts, _direction):
            key = (session_date, send_ts)
            if key in store:
                return False
            store.add(key)
            return True

        d = decide_starter(
            s,
            send_ts=7.0,
            direction="BEAR",
            send_spy=764.05,
            spy=764.05,
            bar_high=764.10,
            bar_low=764.00,
            et_hhmm="11:02",
            ask=2.59,
            persist=persist,
            **CLEAN_Q,
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_dup_send_ts")
        self.assertEqual(s.session_starters_n, 0)

    def test_boot_load_consumed_blocks_recycle(self):
        s = new_session("2026-09-29")
        s.load_consumed([(11.0, "BEAR")])
        d = decide_starter(
            s,
            send_ts=11.0,
            direction="BEAR",
            send_spy=764.58,
            spy=764.41,
            bar_high=764.57,
            bar_low=764.20,
            et_hhmm="11:02",
            ask=1.50,
            **CLEAN_Q,
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_dup_send_ts")


class AlertQualityTests(unittest.TestCase):
    """9/30 morning envelope losses >$150: 10:14 BEAR −$190, 12:44 BULL −$256."""

    def _starter(self, s, **kw):
        args = dict(
            send_ts=70.0,
            direction="BEAR",
            send_spy=768.17,
            spy=768.17,
            bar_high=768.20,
            bar_low=768.00,
            et_hhmm="10:14",
            ask=1.84,
            **CLEAN_Q,
        )
        args.update(kw)
        if "rip_1m_spy" not in kw:
            args.update(trend_confirm_kwargs(args["direction"]))
        return decide_starter(s, **args)

    def test_1014_0dte_bear_is_open_fade(self):
        s = new_session("2026-09-30")
        d = self._starter(
            s,
            option_symbol="SPY260930P00768000",
            et_hhmm="10:14",
            ask=1.84,
            direction="BEAR",
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_0dte_open_fade")
        self.assertEqual(s.skip_quality_n, 1)
        self.assertEqual(s.session_starters_n, 0)

    def test_1237_1dte_trend_bear_posts(self):
        """10/8 dump: 12:37 0DTE is dark; 1DTE TREND is the book."""
        self.assertEqual(starter_dte_for_clock("09:50"), 0)
        self.assertEqual(starter_dte_for_clock("10:14"), 1)
        self.assertEqual(starter_dte_for_clock("10:20"), 1)
        self.assertEqual(starter_dte_for_clock("10:21"), 0)
        self.assertEqual(starter_dte_for_clock("11:48"), 0)
        self.assertEqual(starter_dte_for_clock("12:29"), 0)
        self.assertEqual(starter_dte_for_clock("12:30"), 1)
        self.assertEqual(starter_dte_for_clock("12:37"), 1)
        s = new_session("2026-10-08")
        d = self._starter(
            s,
            send_ts=80.0,
            direction="BEAR",
            send_spy=775.06,
            spy=775.06,
            bar_high=775.20,
            bar_low=774.90,
            et_hhmm="12:37",
            ask=1.40,
            option_symbol="SPY261009P00775000",
            regime="TREND",
            rip_1m_spy=-0.08,
            trend_3m_spy=-0.35,
        )
        self.assertTrue(d["post"])
        self.assertEqual(d["dte"], 1)

    def test_oct8_1217_climax_is_not_a_sub_starter(self):
        s = new_session("2026-10-08")
        d = self._starter(
            s,
            send_ts=1217.0,
            direction="BULL",
            send_spy=774.77,
            spy=776.32,
            bar_high=776.40,
            bar_low=774.70,
            et_hhmm="12:17",
            ask=1.40,
            option_symbol="SPY261008C00776000",
            regime="TREND",
            rip_1m_spy=1.55,
            trend_3m_spy=1.61,
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_1min_rip")
        self.assertEqual(s.session_starters_n, 0)

    def test_1244_0dte_bull_is_near_cutover(self):
        s = new_session("2026-09-30")
        d = self._starter(
            s,
            send_ts=71.0,
            direction="BULL",
            send_spy=768.41,
            spy=768.41,
            bar_high=768.50,
            bar_low=768.30,
            et_hhmm="12:44",
            ask=1.19,
            option_symbol="SPY260930C00768000",
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_0dte_near_cutover")
        self.assertEqual(s.skip_quality_n, 1)

    def test_1148_0dte_midbook_still_posts(self):
        s = new_session("2026-09-30")
        d = self._starter(
            s,
            send_ts=72.0,
            direction="BEAR",
            send_spy=768.20,
            spy=768.20,
            et_hhmm="11:48",
            ask=1.39,
            option_symbol="SPY260930P00768000",
        )
        self.assertTrue(d["post"])
        self.assertEqual(d["qty"], 14)

    def test_open_fade_does_not_freeze_1dte_trend(self):
        s = new_session("2026-09-25")
        d = self._starter(
            s,
            send_ts=73.0,
            direction="BULL",
            et_hhmm="10:14",
            ask=2.32,
            option_symbol="SPY260928C00771000",
            regime="TREND",
        )
        self.assertTrue(d["post"])

    def test_924_0dte_extra_bto_hour_still_posts(self):
        s = new_session("2026-09-24")
        d = self._starter(
            s,
            send_ts=74.0,
            et_hhmm="11:02",
            ask=1.51,
            option_symbol="SPY260924P00766000",
        )
        self.assertTrue(d["post"])

    def test_1516_1dte_runner_still_posts(self):
        s = new_session("2026-09-30")
        d = self._starter(
            s,
            send_ts=75.0,
            direction="BEAR",
            et_hhmm="15:16",
            ask=2.38,
            option_symbol="SPY261001P00762000",
            regime="RANGE",
        )
        self.assertTrue(d["post"])
        self.assertEqual(d["qty"], 6)  # 1DTE cap while flatten_exits gather

    def test_clock_helper_matches_the_two_losses(self):
        self.assertEqual(
            clock_quality_skip_reason(dte=0, et_hhmm="10:14"),
            "skip_0dte_open_fade",
        )
        self.assertEqual(
            clock_quality_skip_reason(dte=0, et_hhmm="12:44"),
            "skip_0dte_near_cutover",
        )
        self.assertIsNone(clock_quality_skip_reason(dte=0, et_hhmm="11:48"))
        self.assertIsNone(clock_quality_skip_reason(dte=1, et_hhmm="10:14"))
        self.assertIsNone(clock_quality_skip_reason(dte=1, et_hhmm="15:16"))
        self.assertEqual(
            clock_quality_skip_reason(dte=0, et_hhmm="12:37"),
            "skip_0dte_near_cutover",
        )
        self.assertIsNone(clock_quality_skip_reason(dte=1, et_hhmm="12:37"))


class ClockIdentityTests(unittest.TestCase):
    def test_live_unix_overrides_legacy_hhmm(self):
        seal = 1791557156.23214
        self.assertEqual(et_hhmm_from_ts(seal), "10:45")
        self.assertEqual(et_hms_from_ts(seal), "10:45:56")
        self.assertEqual(locked_et_hhmm(seal, "10:32"), "10:45")
        self.assertEqual(locked_et_hhmm(1102.0, "11:02"), "11:02")

    def test_bear_seal_is_not_bull_sms_copy(self):
        ident = seal_identity(1791558455.695518, "BEAR", 776.92, "11:04")
        self.assertEqual(ident["et_hhmm"], "11:07")
        self.assertEqual(ident["et_hms"], "11:07:35")
        self.assertEqual(ident["publication_et"], "11:07:35")
        self.assertEqual(ident["strike_kind"], "put")
        self.assertEqual(strike_copy("BULL", 777.41)["kind"], "call")


class ChannelAlignTests(unittest.TestCase):
    """SUB SMS skip stack is the Tradier BTO skip stack (minus live-tape)."""

    def test_rec_book_advertises_channel_align(self):
        h = rec_book()
        self.assertEqual(h["rec_book_ship"], "2026-10-15-match-rec")
        self.assertTrue(h["rec_revenue_lock"])
        self.assertEqual(h["min_hold_sec"], 90.0)
        self.assertTrue(h["stc_requires_before_stc"])
        self.assertTrue(h["stc_recomputes_envelope"])
        self.assertFalse(h["native_close_push"])
        self.assertFalse(h["peak_starter_giveback"])
        self.assertFalse(h["morning_rip_flatten"])
        self.assertTrue(h["clock_identity_lock"])
        self.assertTrue(h["sub_clock_is_tradier_clock"])
        self.assertTrue(h["overlay_sub_forces_queued"])
        self.assertTrue(h["inflight_does_not_consume"])
        self.assertTrue(h["after_recover_lost"])
        self.assertTrue(h["sms_at_seal"])
        self.assertTrue(h["quality_at_seal"])
        self.assertFalse(h["follow_delivered_sub"])
        self.assertTrue(h["overlay_iff_sms"])
        self.assertEqual(h["clock"], "America/New_York unix send instant")
        self.assertEqual(h["clock_fallback"], "historical_audits_only")
        self.assertTrue(h["protect_from_high"])
        self.assertEqual(h["peak_giveback_usd"], 50.0)
        self.assertEqual(h["peak_giveback_min_sec"], 90.0)
        self.assertTrue(h["skip_1min_rip"])
        self.assertEqual(h["one_min_rip_usd"], 0.20)
        self.assertEqual(h["one_min_climax_frac"], 0.80)
        self.assertEqual(h["one_min_climax_usd"], 0.50)
        self.assertEqual(h["trend_3m_min_usd"], 0.20)
        self.assertTrue(h["skip_1min_stall"])
        self.assertEqual(h["stall_1m_usd"], 0.08)
        self.assertEqual(h["dte_book_et"], "12:30")
        self.assertTrue(h["trade_1dte_from_book_et"])
        self.assertTrue(h["open_fade_1dte_book"])
        self.assertEqual(h["rth_start_et"], "09:30")
        self.assertEqual(h["rth_end_et"], "15:50")
        self.assertTrue(h["hold_unstamped_fill"])
        self.assertEqual(h["fresh_fill_sec"], 15.0)
        self.assertTrue(h["fail_90_requires_reversal"])
        self.assertEqual(h["protective_stop_1dte_usd"], 0.30)
        self.assertIsNone(h["fail_sec_1dte"])
        self.assertTrue(h["recover_lost_owned"])
        self.assertTrue(h["orphan_adopt_flattens"])
        self.assertTrue(h["channels_aligned"])
        self.assertTrue(h["sms_iff_sub_send"])
        self.assertEqual(h["sms_from"], "sub_alert_send")
        self.assertTrue(h["extra_bto"])
        self.assertTrue(h["extra_bto_on_run"])
        self.assertEqual(h["extra_bto_fill_to"], 16)
        self.assertEqual(h["starter_notional_usd"], 2000.0)
        self.assertEqual(h["ticket_risk_usd"], 240.0)
        self.assertEqual(h["session_loss_halt_usd"], 750.0)
        self.assertEqual(h["risk_qty_cap"], 16)
        self.assertEqual(h["peak_giveback_usd"], 50.0)
        self.assertEqual(h["one_dte_qty_cap"], 6)
        self.assertFalse(h["extra_bto_1dte"])

    def test_1001_0dte_is_open_fade_on_shared_stack(self):
        self.assertEqual(
            sub_action_skip_reason(
                choppy=False,
                on_arm_bar=False,
                pre_move_spy=0.40,
                chase_spy=0.10,
                regime="TREND",
                dte=0,
                et_hhmm="10:01",
                direction="BEAR",
                **trend_confirm_kwargs("BEAR"),
            ),
            "skip_0dte_open_fade",
        )
        s = new_session("2026-10-01")
        args = dict(
            send_ts=1001.0,
            direction="BEAR",
            send_spy=760.20,
            spy=760.20,
            bar_high=760.30,
            bar_low=760.10,
            et_hhmm="10:01",
            ask=1.50,
            **CLEAN_Q,
        )
        args["option_symbol"] = "SPY261001P00760000"
        args["dte"] = 0
        d = decide_starter(s, **args)
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_0dte_open_fade")
        self.assertEqual(s.session_starters_n, 0)

    def test_starter_skip_matches_sub_action(self):
        cases = [
            dict(et_hhmm="10:01", dte=0, option_symbol="SPY260930P00760000"),
            dict(et_hhmm="10:14", dte=0, option_symbol="SPY260930P00768000"),
            dict(et_hhmm="12:44", dte=0, option_symbol="SPY260930C00768000"),
            dict(et_hhmm="11:48", dte=0, option_symbol="SPY260930P00768000"),
            dict(
                et_hhmm="11:02",
                dte=1,
                regime="RANGE",
                option_symbol="SPY261001P00765000",
            ),
        ]
        for i, kw in enumerate(cases):
            act = sub_action_skip_reason(
                choppy=False,
                on_arm_bar=False,
                pre_move_spy=0.40,
                chase_spy=0.10,
                regime=kw.get("regime", "TREND"),
                dte=kw["dte"],
                et_hhmm=kw["et_hhmm"],
                direction="BEAR",
                **trend_confirm_kwargs("BEAR"),
            )
            s = new_session("2026-09-30")
            args = dict(
                send_ts=float(80 + i),
                direction="BEAR",
                send_spy=768.17,
                spy=768.17,
                bar_high=768.20,
                bar_low=768.00,
                ask=1.50,
                **CLEAN_Q,
            )
            args.update(kw)
            d = decide_starter(s, **args)
            if act is None:
                self.assertTrue(d["post"], msg=kw)
                self.assertEqual(d["action"], "bto")
            else:
                self.assertFalse(d["post"], msg=kw)
                self.assertEqual(d["action"], act)


class AdaptiveProtectTests(unittest.TestCase):
    """10/1 1DTE $0.15 clip: widen 1DTE stop, keep 0DTE $0.15 + fail_90."""

    def test_0dte_015_still_protective(self):
        self.assertEqual(
            envelope_hit(
                fill_px=1.90,
                mark_bid=1.75,
                qty=10,
                spy_adverse=0.20,
                seconds_since_fill=12,
                ticket_phase="FAIL",
                dte=0,
            ),
            "protective",
        )

    def test_1dte_021_is_not_protective(self):
        # 10/1 14:04: −$0.21 option, then the close rally.
        self.assertIsNone(
            envelope_hit(
                fill_px=2.95,
                mark_bid=2.74,
                qty=6,
                spy_adverse=0.42,
                seconds_since_fill=60,
                ticket_phase="FAIL",
                dte=1,
            )
        )

    def test_1dte_030_is_protective(self):
        self.assertEqual(
            envelope_hit(
                fill_px=2.95,
                mark_bid=2.65,
                qty=6,
                spy_adverse=0.60,
                seconds_since_fill=60,
                ticket_phase="FAIL",
                dte=1,
            ),
            "protective",
        )

    def test_1dte_does_not_fail_90(self):
        self.assertIsNone(
            envelope_hit(
                fill_px=2.95,
                mark_bid=2.90,
                qty=6,
                spy_adverse=0.10,
                seconds_since_fill=95,
                ticket_phase="FAIL",
                dte=1,
            )
        )

    def test_0dte_fail_90_holds_without_reversal(self):
        # 10/8 10:23 / 11:11: 91s STC with SPY still with the send.
        self.assertIsNone(
            envelope_hit(
                fill_px=1.90,
                mark_bid=1.85,
                qty=10,
                spy_adverse=0.10,
                seconds_since_fill=95,
                ticket_phase="FAIL",
                dte=0,
            )
        )

    def test_0dte_fail_90_on_major_reversal(self):
        self.assertEqual(
            envelope_hit(
                fill_px=1.90,
                mark_bid=1.85,
                qty=10,
                spy_adverse=0.30,
                seconds_since_fill=95,
                ticket_phase="FAIL",
                dte=0,
            ),
            "fail_90",
        )

    def test_manage_holds_1dte_021_dip(self):
        s = new_session("2026-10-01")
        s.last_option_symbol = "SPY261002C00763000"
        m = decide_manage(
            s,
            fill_px=2.95,
            mark_bid=2.74,
            qty=6,
            spy_adverse=0.42,
            seconds_since_fill=60,
            ticket_phase="FAIL",
            bid=2.74,
            dte=1,
        )
        self.assertFalse(m["flatten"])
        self.assertEqual(m["take_exit"], "hold")
        self.assertEqual(s.protect_fills_n, 0)

    def test_102_1dte_769_call_055_flattens(self):
        # 10/2 12:52 7-lot SPY261005C00769000 2.71→2.16 sat 90m.
        # Rec $0.30 must flatten; fail_90 must not be why.
        self.assertEqual(
            envelope_hit(
                fill_px=2.71,
                mark_bid=2.16,
                qty=7,
                spy_adverse=0.50,
                seconds_since_fill=5400,
                ticket_phase="FAIL",
                dte=1,
            ),
            "protective",
        )
        s = new_session("2026-10-02")
        s.last_option_symbol = "SPY261005C00769000"
        m = decide_manage(
            s,
            fill_px=2.71,
            mark_bid=2.16,
            qty=7,
            spy_adverse=0.50,
            seconds_since_fill=5400,
            ticket_phase="FAIL",
            bid=2.16,
            dte=1,
        )
        self.assertTrue(m["flatten"])
        self.assertEqual(m["reason"], "protective")
        self.assertEqual(m["take_exit"], "ladder_to_market")


class OrphanAdoptTests(unittest.TestCase):
    """10/2 12:52 1DTE sat 90m. Fresh fills stay owned; true orphans flatten."""

    def test_stamped_ticket_after_grace_is_owned(self):
        s = new_session("2026-10-02")
        s.on_bto_fill(7, 2.71, now=1_000.0)
        self.assertEqual(s.recover_lost(7, now=1_020.0), "owned")
        self.assertEqual(s.last_action, "recover_lost_owned")
        self.assertFalse(s.orphan_adopt)
        d = decide_stc(
            s,
            fill_px=2.71,
            mark_bid=2.60,
            qty=7,
            spy_adverse=0.10,
            seconds_since_fill=20,
            ticket_phase="FAIL",
            bid=2.60,
            now=1_020.0,
            dte=1,
        )
        self.assertFalse(d["post"])
        self.assertEqual(d.get("reason"), "stc_requires_envelope")

    def test_unstamped_qty_is_fresh_fill_not_orphan(self):
        # 10/8 13:20: filled then recover_lost flattened in 9s (no stamp).
        from engine.tradier_exec.hooks import before_stc

        s = new_session("2026-10-08")
        self.assertEqual(s.recover_lost(2, now=1_000.0), "fresh_fill")
        self.assertFalse(s.orphan_adopt)
        self.assertEqual(s.last_action, "recover_lost_fresh_fill")
        self.assertIsNotNone(s.ticket_fill_ts)
        self.assertEqual(s.recover_lost(2, now=1_009.0), "fresh_fill")
        d = before_stc(
            s,
            fill_px=1.40,
            mark_bid=1.38,
            qty=2,
            spy_adverse=0.05,
            seconds_since_fill=9,
            ticket_phase="FAIL",
            bid=1.38,
            now=1_009.0,
            dte=1,
        )
        self.assertFalse(d["post"])
        self.assertFalse(d["flatten"])

    def test_unstamped_then_owned_still_protective_after_15s(self):
        # 10/2 leftover: first sight stamps; after 15s owned; $0.30 dumps.
        s = new_session("2026-10-02")
        self.assertEqual(s.recover_lost(7, now=1_000.0), "fresh_fill")
        self.assertEqual(s.recover_lost(7, now=1_016.0), "owned")
        self.assertFalse(s.orphan_adopt)
        m = decide_manage(
            s,
            fill_px=2.71,
            mark_bid=2.16,
            qty=7,
            spy_adverse=0.50,
            seconds_since_fill=16,
            ticket_phase="FAIL",
            bid=2.16,
            now=1_016.0,
            dte=1,
        )
        self.assertTrue(m["flatten"])
        self.assertEqual(m["reason"], "protective")

    def test_true_orphan_when_hold_unstamped_off(self):
        from unittest.mock import patch

        from engine.tradier_exec.hooks import before_stc

        s = new_session("2026-10-02")
        with patch("engine.shared.gates.HOLD_UNSTAMPED_FILL", False):
            self.assertEqual(s.recover_lost(7), "adopt_stc_only")
            self.assertTrue(s.orphan_adopt)
            d = before_stc(
                s,
                fill_px=2.71,
                mark_bid=2.60,
                qty=7,
                spy_adverse=0.10,
                seconds_since_fill=90,
                ticket_phase="FAIL",
                bid=2.60,
                dte=1,
            )
            self.assertTrue(d["post"])
            self.assertTrue(d["flatten"])
            self.assertEqual(d["reason"], "orphan_adopt")
            self.assertEqual(d["take_exit"], "ladder_to_market")
            self.assertEqual(s.last_action, "flatten_orphan_adopt")

    def test_fresh_fill_still_not_an_orphan(self):
        s = new_session("2026-10-02")
        s.on_bto_fill(7, 2.71, now=1_000.0)
        self.assertEqual(s.recover_lost(7, now=1_003.0), "fresh_fill")
        self.assertFalse(s.orphan_adopt)


class OneMinRipTests(unittest.TestCase):
    """10/2 SUB losers were 1-minute prints. 10:54 3-minute dump still sends."""

    def test_helper_skips_todays_fade_rips(self):
        # 1-min vs 3-min from the SPY tape at each fill.
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=0.59, trend_3m_spy=0.39
            ),
            "skip_1min_rip",
        )
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BEAR", rip_1m_spy=0.16, trend_3m_spy=0.08
            ),
            "skip_1min_rip",
        )
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BEAR", rip_1m_spy=0.24, trend_3m_spy=0.02
            ),
            "skip_1min_rip",
        )
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=0.44, trend_3m_spy=0.12
            ),
            "skip_1min_rip",
        )
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BEAR", rip_1m_spy=-0.15, trend_3m_spy=0.07
            ),
            "skip_1min_rip",
        )
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=0.36, trend_3m_spy=-0.08
            ),
            "skip_1min_rip",
        )
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=0.20, trend_3m_spy=0.04
            ),
            "skip_1min_rip",
        )

    def test_1054_bear_3min_dump_still_sends(self):
        self.assertIsNone(
            one_bar_rip_skip_reason(
                direction="BEAR", rip_1m_spy=-0.28, trend_3m_spy=-0.87
            )
        )

    def test_oct8_1217_spike_is_climax_skip(self):
        # 10/8 12:17 BULL +$1.55 / +$1.61 — 96% of the 3-minute in one bar.
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=1.55, trend_3m_spy=1.61
            ),
            "skip_1min_rip",
        )

    def test_oct8_dump_minutes_still_send(self):
        # 12:38 ET: 3-minute dump still printing. 1m is 33% of 3m, not a climax.
        self.assertIsNone(
            one_bar_rip_skip_reason(
                direction="BEAR", rip_1m_spy=-0.22, trend_3m_spy=-0.67
            )
        )

    def test_1252_1dte_3min_confirm_still_sends(self):
        self.assertIsNone(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=0.14, trend_3m_spy=0.27
            )
        )

    def test_oct5_stalled_last_minute_is_skip(self):
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=0.03, trend_3m_spy=0.505
            ),
            "skip_1min_stall",
        )
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=0.02, trend_3m_spy=0.25
            ),
            "skip_1min_stall",
        )
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=0.015, trend_3m_spy=0.22
            ),
            "skip_1min_stall",
        )
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=-0.06, trend_3m_spy=0.64
            ),
            "skip_1min_stall",
        )

    def test_oct5_and_recent_winners_still_send(self):
        self.assertIsNone(
            one_bar_rip_skip_reason(
                direction="BEAR", rip_1m_spy=-0.18, trend_3m_spy=-0.50
            )
        )
        self.assertIsNone(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=0.175, trend_3m_spy=0.235
            )
        )
        self.assertIsNone(
            one_bar_rip_skip_reason(
                direction="BEAR", rip_1m_spy=-0.19, trend_3m_spy=-0.43
            )
        )
        self.assertIsNone(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=0.25, trend_3m_spy=0.94
            )
        )
        self.assertIsNone(
            one_bar_rip_skip_reason(
                direction="BEAR", rip_1m_spy=-0.24, trend_3m_spy=-0.2577
            )
        )

    def test_missing_1m_3m_is_fail_closed(self):
        self.assertEqual(
            one_bar_rip_skip_reason(direction="BEAR"),
            "skip_1min_unconfirmed",
        )
        self.assertEqual(
            signed_spy_deltas([770.0, 770.1]),
            {"rip_1m_spy": None, "trend_3m_spy": None},
        )
        self.assertEqual(
            signed_spy_deltas(None),
            {"rip_1m_spy": None, "trend_3m_spy": None},
        )
        climax = signed_spy_deltas([769.29, 769.21, 768.97, 769.41])
        self.assertAlmostEqual(climax["rip_1m_spy"], 0.44, places=2)
        self.assertAlmostEqual(climax["trend_3m_spy"], 0.12, places=2)
        dump = signed_spy_deltas([771.81, 771.22, 771.22, 770.94])
        self.assertAlmostEqual(dump["rip_1m_spy"], -0.28, places=2)
        self.assertAlmostEqual(dump["trend_3m_spy"], -0.87, places=2)
        s = new_session("2026-10-02")
        d = decide_starter(
            s,
            send_ts=1142.0,
            direction="BULL",
            send_spy=768.97,
            spy=769.41,
            bar_high=769.41,
            bar_low=768.91,
            et_hhmm="11:42",
            ask=1.28,
            **{
                **CLEAN_Q,
                "option_symbol": "SPY261002C00769000",
                "rip_1m_spy": None,
                "trend_3m_spy": None,
            },
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_1min_unconfirmed")

    def test_1142_one_bar_rip_is_not_a_sub_starter(self):
        s = new_session("2026-10-02")
        d = decide_starter(
            s,
            send_ts=1142.0,
            direction="BULL",
            send_spy=768.97,
            spy=769.41,
            bar_high=769.41,
            bar_low=768.91,
            et_hhmm="11:42",
            ask=1.28,
            **{
                **CLEAN_Q,
                **trend_confirm_kwargs("BULL"),
                "option_symbol": "SPY261002C00769000",
                "rip_1m_spy": 0.44,
                "trend_3m_spy": 0.12,
            },
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_1min_rip")
        self.assertEqual(s.session_starters_n, 0)

    def test_1054_confirmed_trend_still_posts(self):
        s = new_session("2026-10-02")
        d = decide_starter(
            s,
            send_ts=1054.0,
            direction="BEAR",
            send_spy=771.22,
            spy=770.94,
            bar_high=771.32,
            bar_low=770.82,
            et_hhmm="10:54",
            ask=1.91,
            **{
                **CLEAN_Q,
                "option_symbol": "SPY261002P00771000",
                "rip_1m_spy": -0.28,
                "trend_3m_spy": -0.87,
            },
        )
        self.assertTrue(d["post"])
        self.assertEqual(d["action"], "bto")


class PeakGivebackTests(unittest.TestCase):
    """10/5 773C 16-lot: lock the bid high, flatten $50 off it."""

    def test_helper_locks_high_not_fill_stop(self):
        # 16 @ 0.79, bid 1.67 → +$1,408. Fill $0.15 stop is 0.64.
        self.assertIsNone(
            envelope_hit(
                fill_px=0.79,
                mark_bid=1.67,
                qty=16,
                spy_adverse=0.0,
                seconds_since_fill=120,
                ticket_phase="RUN",
                dte=0,
                peak_unrealized=1408.0,
            )
        )
        self.assertIsNone(
            envelope_hit(
                fill_px=0.79,
                mark_bid=1.64,
                qty=16,
                spy_adverse=0.0,
                seconds_since_fill=180,
                ticket_phase="RUN",
                dte=0,
                peak_unrealized=1408.0,
            )
        )
        self.assertEqual(
            envelope_hit(
                fill_px=0.79,
                mark_bid=1.63,
                qty=16,
                spy_adverse=0.0,
                seconds_since_fill=180,
                ticket_phase="RUN",
                dte=0,
                peak_unrealized=1408.0,
            ),
            "peak_giveback",
        )

    def test_manage_ratchets_high_then_flattens_50_off(self):
        s = new_session("2026-10-05")
        s.on_bto_fill(16, 0.79, now=1_000.0)
        s.last_option_symbol = "SPY261005C00773000"
        hold = decide_manage(
            s,
            fill_px=0.79,
            mark_bid=1.67,
            qty=16,
            spy_adverse=0.0,
            seconds_since_fill=120,
            ticket_phase="RUN",
            bid=1.67,
            now=1_120.0,
            dte=0,
        )
        self.assertFalse(hold["flatten"])
        self.assertEqual(hold["take_exit"], "hold")
        self.assertAlmostEqual(s.ticket_peak_unrealized, 1408.0, places=0)
        dip = decide_manage(
            s,
            fill_px=0.79,
            mark_bid=1.64,
            qty=16,
            spy_adverse=0.0,
            seconds_since_fill=180,
            ticket_phase="RUN",
            bid=1.64,
            now=1_180.0,
            dte=0,
        )
        self.assertFalse(dip["flatten"])
        out = decide_manage(
            s,
            fill_px=0.79,
            mark_bid=1.63,
            qty=16,
            spy_adverse=0.0,
            seconds_since_fill=200,
            ticket_phase="RUN",
            bid=1.63,
            now=1_200.0,
            dte=0,
        )
        self.assertTrue(out["flatten"])
        self.assertEqual(out["reason"], "peak_giveback")
        self.assertEqual(out["take_exit"], "ladder_to_market")
        stc = decide_stc(
            s,
            fill_px=0.79,
            mark_bid=1.63,
            qty=16,
            spy_adverse=0.0,
            seconds_since_fill=200,
            ticket_phase="RUN",
            bid=1.63,
            now=1_200.0,
            dte=0,
        )
        self.assertTrue(stc["post"])
        self.assertEqual(stc["reason"], "peak_giveback")

    def test_never_made_a_high_still_uses_fill_stop(self):
        self.assertEqual(
            envelope_hit(
                fill_px=1.14,
                mark_bid=0.99,
                qty=16,
                spy_adverse=0.10,
                seconds_since_fill=12,
                ticket_phase="FAIL",
                peak_unrealized=0.0,
            ),
            "protective",
        )

    def test_109_62s_flicker_holds_peak_until_90s(self):
        """10/9 10:45 BULL 13-lot 1.17→1.23 ($78) then STC 62s at 1.10.

        First 90s: HOLD. Fill stop is 1.02, not the print high. After 90s
        the same giveback is peak_giveback. Skip stack is unchanged.
        """
        self.assertIsNone(
            envelope_hit(
                fill_px=1.17,
                mark_bid=1.10,
                qty=13,
                spy_adverse=0.05,
                seconds_since_fill=62,
                ticket_phase="FAIL",
                dte=0,
                peak_unrealized=78.0,
            )
        )
        self.assertEqual(
            envelope_hit(
                fill_px=1.17,
                mark_bid=1.10,
                qty=13,
                spy_adverse=0.05,
                seconds_since_fill=90,
                ticket_phase="FAIL",
                dte=0,
                peak_unrealized=78.0,
            ),
            "peak_giveback",
        )
        # Fill $0.15 still clips a real dump in the first 90s.
        self.assertEqual(
            envelope_hit(
                fill_px=1.17,
                mark_bid=1.02,
                qty=13,
                spy_adverse=0.05,
                seconds_since_fill=20,
                ticket_phase="FAIL",
                dte=0,
                peak_unrealized=78.0,
            ),
            "protective",
        )

    def test_109_5s_to_12s_fills_hold_even_if_mislabeled_orphan(self):
        """10/9 C 5s, D 7s, E 12s: live sold before SPY could move.

        Same 90s min hold as fail_90 / peak. Not a one-day patch.
        A real dump still clips (fill $0.15 / 1DTE $0.30).
        """
        from unittest.mock import patch

        from engine.tradier_exec.hooks import before_stc
        from exec.gates import flatten_allowed

        self.assertFalse(flatten_allowed(5, "orphan_adopt"))
        self.assertFalse(flatten_allowed(12, "fail_90"))
        self.assertFalse(flatten_allowed(62, "peak_giveback"))
        self.assertTrue(flatten_allowed(12, "protective"))
        self.assertTrue(flatten_allowed(90, "orphan_adopt"))

        cases = [
            ("C", 24, 1.235, 1.21, 5, 0),
            ("D", 14, 1.02, 0.99, 7, 0),
            ("E", 7, 2.00, 2.02, 12, 1),
        ]
        for name, qty, fill, bid, age, dte in cases:
            s = new_session("2026-10-09")
            s.on_bto_fill(qty, fill, now=1_000.0)
            d = decide_stc(
                s,
                fill_px=fill,
                mark_bid=bid,
                qty=qty,
                spy_adverse=0.05,
                seconds_since_fill=age,
                ticket_phase="FAIL",
                bid=bid,
                now=1_000.0 + age,
                dte=dte,
            )
            self.assertFalse(d["post"], msg=name)
            self.assertFalse(d["flatten"], msg=name)

        s = new_session("2026-10-09")
        with patch("engine.shared.gates.HOLD_UNSTAMPED_FILL", False):
            self.assertEqual(s.recover_lost(24, now=1_000.0), "adopt_stc_only")
            d = before_stc(
                s,
                fill_px=1.235,
                mark_bid=1.21,
                qty=24,
                spy_adverse=0.05,
                seconds_since_fill=5,
                ticket_phase="FAIL",
                bid=1.21,
                now=1_005.0,
                dte=0,
            )
            self.assertFalse(d["post"])
            self.assertFalse(d["flatten"])

    def test_1013_4s_and_122s_scratches_hold(self):
        """10/13: 4s 1.07→1.05 and 122s 1.19→1.15 (−$48) must HOLD.

        Live advertised min_hold_sec=90 then sold via recover_lost_flat /
        broker_flat / peak_starter_giveback $37.50 from print high.
        Unknown scratch names never flatten. $0.04 down is not $0.15.
        A real fill $0.15 dump still clips.
        """
        from engine.shared.gates import flatten_allowed, may_post_stc
        from engine.tradier_exec.hooks import before_stc

        for reason in (
            "peak_starter_giveback",
            "broker_flat",
            "recover_lost_flat",
            "spike_fail",
            "print_high",
        ):
            self.assertFalse(flatten_allowed(4, reason), msg=reason)
            self.assertFalse(flatten_allowed(122, reason), msg=reason)
            self.assertFalse(may_post_stc(122, reason, qty=12), msg=reason)

        s = new_session("2026-10-13")
        s.on_bto_fill(14, 1.07, now=1_000.0)
        four = before_stc(
            s,
            fill_px=1.07,
            mark_bid=1.05,
            qty=14,
            spy_adverse=0.02,
            seconds_since_fill=4,
            ticket_phase="FAIL",
            bid=1.05,
            now=1_004.0,
            dte=0,
        )
        self.assertFalse(four["post"])
        self.assertFalse(four["flatten"])

        s = new_session("2026-10-13")
        s.on_bto_fill(12, 1.19, now=1_000.0)
        dip = before_stc(
            s,
            fill_px=1.19,
            mark_bid=1.15,
            qty=12,
            spy_adverse=0.05,
            seconds_since_fill=122,
            ticket_phase="FAIL",
            bid=1.15,
            now=1_122.0,
            dte=0,
        )
        self.assertFalse(dip["post"])
        self.assertFalse(dip["flatten"])
        self.assertFalse(
            may_post_stc(
                122,
                "protective",
                fill_px=1.19,
                mark_bid=1.15,
                dte=0,
                qty=12,
            )
        )

        s = new_session("2026-10-13")
        s.on_bto_fill(12, 1.19, now=1_000.0)
        dump = before_stc(
            s,
            fill_px=1.19,
            mark_bid=1.04,
            qty=12,
            spy_adverse=0.05,
            seconds_since_fill=20,
            ticket_phase="FAIL",
            bid=1.04,
            now=1_020.0,
            dte=0,
        )
        self.assertTrue(dump["post"])
        self.assertEqual(dump["reason"], "protective")

    def test_109_bear_185s_holds_without_spy_reversal(self):
        s = new_session("2026-10-09")
        s.on_bto_fill(15, 0.94, now=1_000.0)
        d = decide_stc(
            s,
            fill_px=0.94,
            mark_bid=0.90,
            qty=15,
            spy_adverse=0.10,
            seconds_since_fill=185,
            ticket_phase="FAIL",
            bid=0.90,
            now=1_185.0,
            dte=0,
        )
        self.assertFalse(d["post"])
        self.assertIsNone(
            envelope_hit(
                fill_px=0.94,
                mark_bid=0.90,
                qty=15,
                spy_adverse=0.10,
                seconds_since_fill=185,
                ticket_phase="FAIL",
                dte=0,
            )
        )

    def test_grace_does_not_sell_the_open(self):
        s = new_session("2026-10-05")
        s.on_bto_fill(16, 0.79, now=1_000.0)
        m = decide_manage(
            s,
            fill_px=0.79,
            mark_bid=1.67,
            qty=16,
            spy_adverse=0.0,
            seconds_since_fill=3,
            ticket_phase="RUN",
            bid=1.67,
            now=1_003.0,
            dte=0,
        )
        self.assertFalse(m["flatten"])
        self.assertAlmostEqual(s.ticket_peak_unrealized, 1408.0, places=0)


class ScratchLockTests(unittest.TestCase):
    """Every historical scratch, plus live leftover names, plus lying envelope names.

    may_post_stc recomputes envelope_hit. A $0.04 dip cannot sell by calling
    itself protective / peak_giveback / fail_90 / ticket_risk / cata.
    on_manage and before_stc share that gate. 15:50 leftover still flattens.
    """

    SCRATCH_NAMES = (
        "peak_starter_giveback",
        "peak_starter",
        "print_high",
        "hold_high",
        "broker_flat",
        "recover_lost_flat",
        "recover_lost_fresh_fill",
        "spike_fail",
        "max_in",
        "never_green",
        "native_close",
        "close_push",
        "morning_rip",
        "morning_rip_flatten",
        "rip_flatten",
        "time_stop",
        "starter_giveback",
        "thru_05",
        "working_stc",
        "stc_on_fill",
        "bid_exit",
        "trail",
        "unknown_live_knob",
    )

    def test_scratch_names_never_flatten_at_any_age(self):
        from engine.shared.gates import flatten_allowed, may_post_stc

        for reason in self.SCRATCH_NAMES:
            for age in (0, 4, 8, 12, 20, 62, 90, 122, 185, 5400):
                self.assertFalse(flatten_allowed(age, reason), msg=f"{reason}@{age}")
                self.assertFalse(
                    may_post_stc(age, reason, qty=12), msg=f"{reason}@{age}"
                )
                self.assertFalse(
                    may_post_stc(
                        age,
                        reason,
                        fill_px=1.19,
                        mark_bid=1.15,
                        dte=0,
                        qty=12,
                        spy_adverse=0.05,
                        ticket_phase="FAIL",
                        peak_unrealized=48.0,
                    ),
                    msg=f"{reason} marks {age}",
                )

    def test_lying_envelope_names_cannot_sell_a_004_dip(self):
        from engine.shared.gates import may_post_stc

        for claimed in (
            "protective",
            "peak_giveback",
            "fail_90",
            "ticket_risk",
            "cata_opt",
            "cata_spy",
            "orphan_adopt",
        ):
            self.assertFalse(
                may_post_stc(
                    122,
                    claimed,
                    fill_px=1.19,
                    mark_bid=1.15,
                    dte=0,
                    qty=12,
                    spy_adverse=0.05,
                    ticket_phase="FAIL",
                    peak_unrealized=48.0,
                    orphan_adopt=False,
                ),
                msg=claimed,
            )

    def test_historical_scratches_hold_on_manage_and_before_stc(self):
        from engine.tradier_exec.hooks import before_stc, on_manage

        # (name, qty, fill, bid, age, dte, spy, peak)
        cases = [
            ("930_3s_fill", 10, 1.84, 1.83, 3, 0, 0.02, 0.0),
            ("101_1dte_021", 8, 1.40, 1.19, 60, 1, 0.20, 0.0),
            ("108_9s_unstamped", 2, 1.40, 1.38, 9, 1, 0.05, 0.0),
            ("109_C_5s", 24, 1.235, 1.21, 5, 0, 0.05, 0.0),
            ("109_D_7s", 14, 1.02, 0.99, 7, 0, 0.05, 0.0),
            ("109_E_12s_winner", 7, 2.00, 2.02, 12, 1, 0.02, 14.0),
            ("109_A_62s_flicker", 13, 1.17, 1.10, 62, 0, 0.05, 78.0),
            ("109_B_185s_no_rev", 15, 0.94, 0.90, 185, 0, 0.10, 0.0),
            ("1012_14lot_002", 14, 1.06, 1.04, 45, 0, 0.05, 0.0),
            ("1012_18lot_scratch", 18, 0.83, 0.83, 40, 0, 0.02, 0.0),
            ("1013_4s", 14, 1.07, 1.05, 4, 0, 0.02, 0.0),
            ("1013_122s_3750", 12, 1.19, 1.15, 122, 0, 0.05, 48.0),
            ("1013_morning_rip", 12, 1.19, 1.15, 122, 0, 0.05, 48.0),
        ]
        for name, qty, fill, bid, age, dte, spy, peak in cases:
            kwargs = dict(
                fill_px=fill,
                mark_bid=bid,
                qty=qty,
                spy_adverse=spy,
                seconds_since_fill=age,
                ticket_phase="FAIL",
                bid=bid,
                now=1_000.0 + age,
                dte=dte,
            )
            if name == "1013_morning_rip":
                kwargs["et_hhmm"] = "09:45"
            for hook, fn in (("stc", before_stc), ("manage", on_manage)):
                s2 = new_session("2026-10-13")
                s2.on_bto_fill(qty, fill, now=1_000.0)
                s2.ticket_peak_unrealized = peak
                d = fn(s2, **kwargs)
                self.assertFalse(d.get("post") or d.get("flatten"), msg=f"{name}/{hook}")
                self.assertFalse(d.get("post_stc"), msg=f"{name}/{hook} post_stc")

    def test_real_dumps_and_locks_still_flatten(self):
        from engine.tradier_exec.hooks import before_stc, on_manage

        dumps = [
            ("1013_015", 12, 1.19, 1.04, 20, 0, 0.05, 0.0, "protective"),
            ("102_1dte_055", 7, 2.71, 2.16, 16, 1, 0.50, 0.0, "protective"),
            ("105_50_off_high", 16, 0.79, 1.63, 180, 0, 0.0, 1408.0, "peak_giveback"),
            ("109_015_20s", 13, 1.17, 1.02, 20, 0, 0.05, 78.0, "protective"),
            ("fail90_rev", 10, 1.00, 0.92, 91, 0, 0.30, 0.0, "fail_90"),
        ]
        for name, qty, fill, bid, age, dte, spy, peak, want in dumps:
            for hook, fn in (("stc", before_stc), ("manage", on_manage)):
                s = new_session("2026-10-13")
                s.on_bto_fill(qty, fill, now=1_000.0)
                s.ticket_peak_unrealized = peak
                d = fn(
                    s,
                    fill_px=fill,
                    mark_bid=bid,
                    qty=qty,
                    spy_adverse=spy,
                    seconds_since_fill=age,
                    ticket_phase="FAIL",
                    bid=bid,
                    now=1_000.0 + age,
                    dte=dte,
                )
                self.assertTrue(d["flatten"], msg=f"{name}/{hook}")
                self.assertEqual(d["reason"], want, msg=f"{name}/{hook}")
        from engine.shared.gates import may_post_stc

        self.assertTrue(
            may_post_stc(
                20,
                "broker_flat",
                fill_px=1.19,
                mark_bid=1.04,
                dte=0,
                qty=12,
                spy_adverse=0.05,
                ticket_phase="FAIL",
            )
        )

    def test_qty_zero_never_sprays_stc(self):
        from engine.tradier_exec.hooks import before_stc, on_manage

        s = new_session("2026-10-13")
        d = before_stc(
            s,
            fill_px=1.07,
            mark_bid=1.05,
            qty=0,
            spy_adverse=0.02,
            seconds_since_fill=4,
            ticket_phase="FAIL",
            bid=1.05,
            dte=0,
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["reason"], "skip_already_flat")
        m = on_manage(
            s,
            fill_px=1.07,
            mark_bid=1.05,
            qty=0,
            spy_adverse=0.02,
            seconds_since_fill=4,
            ticket_phase="FAIL",
            bid=1.05,
            dte=0,
        )
        self.assertFalse(m["flatten"])
        self.assertFalse(m["post_stc"])

    def test_rth_end_flattens_leftover_morning_rip_does_not(self):
        from engine.tradier_exec.hooks import before_stc

        s = new_session("2026-10-13")
        s.on_bto_fill(12, 1.19, now=1_000.0)
        morning = before_stc(
            s,
            fill_px=1.19,
            mark_bid=1.18,
            qty=12,
            spy_adverse=0.02,
            seconds_since_fill=122,
            ticket_phase="FAIL",
            bid=1.18,
            now=1_122.0,
            dte=0,
            et_hhmm="09:45",
        )
        self.assertFalse(morning["post"])
        self.assertFalse(morning["flatten"])

        s = new_session("2026-10-13")
        s.on_bto_fill(12, 1.19, now=1_000.0)
        close = before_stc(
            s,
            fill_px=1.19,
            mark_bid=1.18,
            qty=12,
            spy_adverse=0.02,
            seconds_since_fill=122,
            ticket_phase="FAIL",
            bid=1.18,
            now=1_122.0,
            dte=0,
            et_hhmm="15:50",
        )
        self.assertTrue(close["post"])
        self.assertEqual(close["reason"], "rth_end")

    def test_missing_marks_fail_closed_even_for_protective(self):
        from engine.shared.gates import may_post_stc

        self.assertFalse(may_post_stc(20, "protective", qty=12))
        self.assertFalse(may_post_stc(90, "peak_giveback", qty=16))
        self.assertFalse(may_post_stc(91, "fail_90", qty=10))


class OneDteExitLogTests(unittest.TestCase):
    """10/13–10/14: log the path, shorten 1DTE protective, cap 1DTE size."""

    def test_0dte_qty_unchanged(self):
        self.assertEqual(starter_qty(1.42), 14)
        self.assertEqual(starter_qty(1.42, dte=0), 14)

    def test_1dte_starter_caps_at_six(self):
        # 10/14 dump was 10-lot at 2.63; $2000 notional is 7; cap is 6.
        self.assertEqual(starter_qty(2.63, dte=1), 6)
        self.assertEqual(starter_qty(1.42, dte=1), 6)
        self.assertEqual(starter_qty(3.03, dte=1), 6)

    def test_1dte_extra_bto_is_off(self):
        from engine.shared.gates import extra_bto_qty

        s = new_session("2026-10-14")
        s.broker_qty = 5
        s.ticket_phase = "RUN"
        s.last_option_symbol = "SPY261015P00770000"
        self.assertEqual(extra_bto_qty(5, dte=1), 0)
        self.assertFalse(s.extra_bto_ok(1, 0.25, 2.95, 2.89, dte=1))
        m = decide_manage(
            s,
            fill_px=2.89,
            mark_bid=2.95,
            qty=5,
            spy_adverse=0.0,
            seconds_since_fill=12,
            ticket_phase="FAIL",
            bid=2.95,
            dte=1,
        )
        self.assertFalse(m["flatten"])
        self.assertFalse(m.get("extra_bto"))
        self.assertEqual(m.get("extra_bto_qty"), 0)

    def test_0dte_ladder_is_still_four_steps(self):
        steps = stc_ladder_prices(1.25, dte=0, reason="protective")
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[-1], ("market", None))

    def test_1dte_protective_is_bid_then_market(self):
        from engine.shared.gates import stc_ladder_steps

        steps = stc_ladder_prices(2.018, dte=1, reason="protective")
        self.assertEqual(steps, [("limit", 2.02), ("market", None)])
        plan = stc_ladder_steps(2.018, dte=1, reason="protective")
        self.assertEqual(plan[1]["wait_sec"], 2.0)
        peak = stc_ladder_prices(3.09, dte=1, reason="peak_giveback")
        self.assertEqual(len(peak), 4)

    def test_flatten_logs_trigger_bid_and_survives_reload(self):
        import tempfile
        from pathlib import Path

        from engine.tradier_exec.persist import ConsumedSends

        s = new_session("2026-10-13")
        with tempfile.TemporaryDirectory() as td:
            db = ConsumedSends(Path(td) / "x.db")
            s.flatten_persist = db
            s.last_option_symbol = "SPY261014P00777000"
            m = decide_manage(
                s,
                fill_px=2.318333,
                mark_bid=2.018,
                qty=6,
                spy_adverse=0.20,
                seconds_since_fill=120,
                ticket_phase="FAIL",
                bid=2.018,
                now=1_000.0,
                dte=1,
            )
            self.assertTrue(m["flatten"])
            self.assertEqual(m["reason"], "protective")
            self.assertEqual(len(m["ladder"]), 2)
            row = s.last_flatten_exit
            self.assertEqual(row["trigger_bid"], 2.018)
            self.assertEqual(row["qty"], 6)
            self.assertEqual(row["reason"], "protective")
            self.assertEqual(row["fill_px"], 2.318333)
            from engine.shared.gates import note_ladder_step, note_flatten_fill

            note_ladder_step(s, kind="limit", px=2.02, ts=1_001.0)
            note_ladder_step(s, kind="market", px=None, ts=1_003.0)
            note_flatten_fill(s, fill_px=1.77, ts=1_004.0)
            self.assertEqual(row["final_fill_px"], 1.77)
            self.assertEqual(len(row["steps"]), 2)
            db.close()
            db2 = ConsumedSends(Path(td) / "x.db")
            loaded = db2.load_flattens()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["trigger_bid"], 2.018)
            self.assertEqual(loaded[0]["final_fill_px"], 1.77)
            self.assertEqual(loaded[0]["session_date"], "2026-10-13")
            db2.close()

    def test_1013_short_ladder_replay_vs_logged_cash(self):
        from engine.shared.gates import replay_1dte_protective_cash

        # Trigger bid is assumed at the $0.30 stop. 10/13 did not log it.
        r = replay_1dte_protective_cash(
            qty=6,
            fill_px=2.318333,
            trigger_bid=2.018,
            market_fill=1.77,
            commission=4.20,
        )
        self.assertAlmostEqual(r["actual_cash"], -333.20, places=1)
        self.assertAlmostEqual(r["if_trigger_bid_fills"], -184.40, places=1)
        self.assertAlmostEqual(r["ladder_slippage_vs_bid"], -148.80, places=1)
        self.assertEqual(r["short_ladder_if_still_market"], r["actual_cash"])

    def test_scaled_giveback_replay_is_off_and_0dte_stays_50(self):
        from unittest.mock import patch

        from engine.shared.gates import peak_giveback_usd_for

        self.assertEqual(peak_giveback_usd_for(0, 16, 1408.0), 50.0)
        self.assertEqual(peak_giveback_usd_for(1, 5, 90.0), 50.0)
        with patch("engine.shared.gates.ONE_DTE_SCALED_GIVEBACK", True):
            # 5-lot floor $0.08 → $40; 25% of $90 = $22.50 → floor binds.
            self.assertEqual(peak_giveback_usd_for(1, 5, 90.0), 40.0)
            # Big 1DTE runner: 25% of $400 = $100 (looser than $50).
            self.assertEqual(peak_giveback_usd_for(1, 5, 400.0), 100.0)
            self.assertEqual(peak_giveback_usd_for(0, 16, 1408.0), 50.0)

    def test_1014_plus_30_and_plus_90_had_no_logged_high(self):
        # Cash only: 5× 2.89→2.95 +$25.42 and 5× 2.90→3.09 +$90.42.
        # Peak giveback $50 never arms below a $50 high. Without flatten_exits
        # we cannot show leftover MFE. Daily 770P H 4.55 is not this ticket.
        plus_30_peak_known = 25.42
        plus_90_peak_known = 90.42
        self.assertLess(plus_30_peak_known, 50.0)
        self.assertGreater(plus_90_peak_known, 50.0)


class ExtraBtoTests(unittest.TestCase):
    def test_extra_only_on_run(self):
        s = new_session("2026-09-29")
        s.broker_qty = 8
        s.ticket_phase = "FAIL"
        self.assertFalse(s.extra_bto_ok(4, 0.25, 1.60, 1.40))
        s.ticket_phase = "RUN"
        self.assertTrue(s.extra_bto_ok(4, 0.25, 1.60, 1.40))
        self.assertFalse(s.extra_bto_ok(9, 0.25, 1.60, 1.40))  # 8+9 > 16

    def test_extra_qty_fills_headroom_to_cap(self):
        from engine.shared.gates import extra_bto_qty

        self.assertEqual(extra_bto_qty(13), 3)
        self.assertEqual(extra_bto_qty(11), 5)
        self.assertEqual(extra_bto_qty(16), 0)

    def test_manage_arms_run_so_extra_can_fire(self):
        s = new_session("2026-09-24")
        s.broker_qty = 13
        s.ticket_phase = "FAIL"
        m = decide_manage(
            s,
            fill_px=1.51,
            mark_bid=1.71,
            qty=13,
            spy_adverse=0.0,
            seconds_since_fill=12,
            ticket_phase="FAIL",
            bid=1.71,
        )
        self.assertFalse(m["flatten"])
        self.assertEqual(s.ticket_phase, "RUN")
        self.assertTrue(m["extra_bto"])
        self.assertEqual(m["extra_bto_qty"], 3)
        self.assertEqual(m["action"], "extra_bto")
        self.assertEqual(m["take_exit"], "hold")
        self.assertFalse(m["post_stc"])
        from engine.tradier_exec.hooks import before_extra_bto

        d = before_extra_bto(
            s,
            mfe_usd=0.20,
            mark_bid=1.71,
            avg_fill=1.51,
            plot="sub_alert_send",
            overlay_queued=True,
        )
        self.assertTrue(d["post"])
        self.assertEqual(d["qty"], 3)

    def test_extra_exec_queued_opposite_blocked(self):
        from engine.tradier_exec.hooks import before_extra_bto

        s = new_session("2026-09-30")
        s.broker_qty = 8
        s.ticket_phase = "RUN"
        d = before_extra_bto(
            s,
            add_qty=4,
            mfe_usd=0.25,
            mark_bid=1.60,
            avg_fill=1.40,
            plot="sub_alert_send",
            is_opposite=True,
            overlay_queued=False,
        )
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_queue_opposite")


class RecRevenueGuardrailTests(unittest.TestCase):
    """Rec beats live on priced tape. These rails stay frozen."""

    def test_rec_beats_live_on_30_day_tape(self):
        from engine.tradier_exec.week_replay import weekly_impact

        w = weekly_impact()
        self.assertGreater(w["impact"], 7000)
        self.assertEqual(w["result"], "win")
        self.assertGreater(w["rec"], w["live"])

    def test_skip_bar_is_frozen(self):
        h = rec_book()
        self.assertTrue(h["rec_revenue_lock"])
        self.assertEqual(STALL_1M_USD, 0.08)
        self.assertEqual(TREND_3M_MIN_USD, 0.20)
        self.assertEqual(CHASE_SPY, 0.50)
        self.assertEqual(ONE_MIN_CLIMAX_USD, 0.50)
        self.assertEqual(ONE_MIN_CLIMAX_FRAC, 0.80)
        self.assertFalse(CHOP_SIZE)
        self.assertEqual(h["stall_1m_usd"], 0.08)
        self.assertEqual(h["trend_3m_min_usd"], 0.20)

    def test_105_1222_would_die_if_3m_tightened(self):
        # 10/5 12:22 +$1,344. Rec 3m $0.20 keeps it. $0.30 would skip.
        self.assertGreaterEqual(0.235, TREND_3M_MIN_USD)
        self.assertLess(0.235, 0.30)
        self.assertGreaterEqual(0.175, STALL_1M_USD)
        self.assertIsNone(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=0.175, trend_3m_spy=0.235
            ),
        )

    def test_108_1217_climax_stays_skipped(self):
        self.assertEqual(
            one_bar_rip_skip_reason(
                direction="BULL", rip_1m_spy=1.55, trend_3m_spy=1.61
            ),
            "skip_1min_rip",
        )

    def test_choppy_is_refuse_not_size(self):
        self.assertEqual(
            quality_skip_reason(
                choppy=True,
                on_arm_bar=False,
                pre_move_spy=0.40,
                chase_spy=0.10,
            ),
            "skip_choppy",
        )

    def test_extra_bto_and_halt_stay_rec(self):
        h = rec_book()
        self.assertTrue(h["extra_bto"])
        self.assertTrue(h["extra_bto_on_run"])
        self.assertEqual(h["extra_bto_fill_to"], 16)
        self.assertEqual(extra_bto_qty(13), 3)
        self.assertEqual(SESSION_LOSS_HALT_USD, 750.0)
        self.assertEqual(TICKET_RISK_USD, 240.0)
        self.assertEqual(PEAK_GIVEBACK_USD, 50.0)
        self.assertEqual(PEAK_GIVEBACK_MIN_SEC, 90.0)

    def test_envelope_still_clips_a_real_dump(self):
        # Guardrail is hold scratches, not hold dead premium.
        self.assertEqual(
            envelope_hit(
                fill_px=1.19,
                mark_bid=1.04,
                qty=12,
                spy_adverse=0.0,
                seconds_since_fill=20,
                ticket_phase="FAIL",
                dte=0,
            ),
            "protective",
        )
        self.assertTrue(
            may_post_stc(20, "protective", fill_px=1.19, mark_bid=1.04, qty=12, dte=0)
        )
        self.assertFalse(
            may_post_stc(4, "recover_lost_flat", fill_px=1.07, mark_bid=1.05, qty=14, dte=0)
        )


class SubClockHoldTests(unittest.TestCase):
    """10/15: SUB send_ts is Tradier clock; 2–7s 1DTE $0.02 dips HOLD."""

    def _start(self, s, send_ts, direction, et_hhmm, **kw):
        sign = 1.0 if direction == "BULL" else -1.0
        args = dict(
            send_ts=send_ts,
            direction=direction,
            send_spy=770.0,
            spy=770.0,
            bar_high=770.2,
            bar_low=769.8,
            et_hhmm=et_hhmm,
            ask=2.80,
            **CLEAN_Q,
        )
        args.update(
            rip_1m_spy=sign * 0.25,
            trend_3m_spy=sign * 0.60,
            option_symbol=(
                "SPY261016C00770000" if direction == "BULL" else "SPY261015P00768000"
            ),
            dte=0 if et_hhmm < "12:30" else 1,
        )
        args.update(kw)
        return decide_starter(s, **args)

    def test_inflight_does_not_consume_next_sub(self):
        s = new_session("2026-10-15")
        first = self._start(s, 10.26, "BEAR", "10:26")
        self.assertTrue(first["post"])
        s.inflight = True
        s.pending_entry = True
        s.broker_qty = 9
        nxt = self._start(
            s, 11.47, "BULL", "11:47", is_opposite=True, overlay_queued=False
        )
        self.assertEqual(nxt["action"], "skip_inflight")
        self.assertFalse(nxt["post"])
        self.assertNotIn(11.47, s.consumed)
        s.inflight = False
        s.pending_entry = False
        s.broker_qty = 0
        s.on_flatten(-189.0, "FAIL", dte=0, et_hhmm="10:27")
        again = self._start(
            s, 11.47, "BULL", "11:47", is_opposite=True, overlay_queued=False
        )
        self.assertTrue(again["post"])

    def test_publication_clock_must_match_send_ts(self):
        from engine.shared.gates import et_hms_from_ts

        live_ts = 1792082717.742985
        s = new_session("2026-10-15")
        d = self._start(
            s,
            live_ts,
            "BULL",
            "12:45",
            publication_et="12:42:00",
            dte=1,
        )
        self.assertEqual(d["action"], "skip_clock_identity")
        self.assertFalse(d["post"])
        self.assertNotIn(live_ts, s.consumed)
        self.assertEqual(d["et_hms"], et_hms_from_ts(live_ts))

    def test_1015_cdg_micro_dips_hold(self):
        from engine.tradier_exec.hooks import after_recover_lost, on_manage

        cases = (
            ("C_1245", 6, 2.73, 2.71, 4, 1),
            ("D_1312", 6, 2.83, 2.81, 2, 1),
            ("G_1520", 6, 2.71, 2.70, 7, 1),
        )
        for name, qty, fill, bid, age, dte in cases:
            s = new_session("2026-10-15")
            s.on_bto_fill(qty, fill, now=1_000.0)
            kwargs = dict(
                fill_px=fill,
                mark_bid=bid,
                qty=qty,
                spy_adverse=0.02,
                seconds_since_fill=age,
                ticket_phase="FAIL",
                bid=bid,
                now=1_000.0 + age,
                dte=dte,
            )
            m = on_manage(s, **kwargs)
            self.assertFalse(m.get("flatten"), msg=name)
            self.assertFalse(m.get("post_stc"), msg=name)
            flat = after_recover_lost(s, "flat", **{**kwargs, "qty": 0})
            self.assertEqual(flat["reason"], "skip_already_flat", msg=name)
            owned = after_recover_lost(
                new_session("2026-10-15"), "owned", **kwargs
            )
            self.assertFalse(owned.get("flatten") or owned.get("post_stc"), msg=name)

    def test_1015_a_protective_still_flattens(self):
        from engine.tradier_exec.hooks import on_manage

        s = new_session("2026-10-15")
        s.on_bto_fill(9, 2.05, now=1_000.0)
        d = on_manage(
            s,
            fill_px=2.05,
            mark_bid=1.83,
            qty=9,
            spy_adverse=0.10,
            seconds_since_fill=22,
            ticket_phase="FAIL",
            bid=1.83,
            now=1_022.0,
            dte=0,
        )
        self.assertTrue(d["flatten"])
        self.assertEqual(d["reason"], "protective")

    def test_health_advertises_sub_clock_hold(self):
        from engine.shared.gates import rec_book
        from engine.tradier_exec.hooks import health_overlay

        h = health_overlay(new_session("2026-10-15"))
        self.assertTrue(h["sub_clock_is_tradier_clock"])
        self.assertTrue(h["overlay_sub_forces_queued"])
        self.assertTrue(h["inflight_does_not_consume"])
        self.assertTrue(h["after_recover_lost"])
        self.assertTrue(h["quality_at_seal"])
        self.assertEqual(rec_book()["rec_book_ship"], "2026-10-15-match-rec")


class BounceHelperTests(unittest.TestCase):
    def test_bounce_helper_bear_bar(self):
        self.assertTrue(
            bounce_against("BEAR", 764.78, 765.31, 765.63, 764.68, "10:00")
        )
        self.assertFalse(
            bounce_against("BEAR", 764.58, 764.41, 764.57, 764.20, "11:02")
        )


if __name__ == "__main__":
    unittest.main()

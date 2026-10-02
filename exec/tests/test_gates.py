"""Unit tests for cover-us gates. These encode 2026-09-29 failure modes without account P&L."""

from __future__ import annotations

import unittest

from exec.gates import (
    SessionState,
    bounce_against,
    clock_quality_skip_reason,
    decide_manage,
    decide_starter,
    decide_stc,
    envelope_hit,
    ledger_invariant,
    new_session,
    quality_skip_reason,
    rec_book,
    starter_qty,
    stc_ladder_prices,
    sub_action_skip_reason,
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
            send_ts=1790690414.23,
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
        self.assertIn(1790690414.23, s.consumed)
        b = decide_starter(
            s,
            send_ts=1790690414.23,
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
        self.assertEqual(s.recover_lost(10), "adopt_stc_only")
        self.assertIn("stc_only", s.last_action)


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
            quality_skip_reason(choppy=False, pre_move_spy=0.40, chase_spy=0.10)
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
        d = self._base(s, is_opposite=True, overlay_queued=False)
        self.assertFalse(d["post"])
        self.assertEqual(d["action"], "skip_queue_opposite")

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
        self.assertEqual(d["qty"], 8)

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


class ChannelAlignTests(unittest.TestCase):
    """SUB SMS skip stack is the Tradier BTO skip stack (minus live-tape)."""

    def test_rec_book_advertises_channel_align(self):
        h = rec_book()
        self.assertEqual(h["rec_book_ship"], "2026-10-02-orphan-adopt")
        self.assertEqual(h["protective_stop_1dte_usd"], 0.30)
        self.assertIsNone(h["fail_sec_1dte"])
        self.assertTrue(h["recover_lost_owned"])
        self.assertTrue(h["orphan_adopt_flattens"])
        self.assertTrue(h["channels_aligned"])
        self.assertTrue(h["sms_iff_sub_send"])
        self.assertEqual(h["sms_from"], "sub_alert_send")

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

    def test_0dte_still_fail_90(self):
        self.assertEqual(
            envelope_hit(
                fill_px=1.90,
                mark_bid=1.85,
                qty=10,
                spy_adverse=0.10,
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

    def test_true_orphan_before_stc_flattens(self):
        from engine.tradier_exec.hooks import before_stc

        s = new_session("2026-10-02")
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

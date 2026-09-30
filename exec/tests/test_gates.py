"""Unit tests for cover-us gates. These encode 2026-09-29 failure modes without account P&L."""

from __future__ import annotations

import unittest

from exec.gates import (
    SessionState,
    bounce_against,
    decide_manage,
    decide_starter,
    envelope_hit,
    ledger_invariant,
    new_session,
    quality_skip_reason,
    starter_qty,
    stc_ladder_prices,
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
            et_hhmm="10:03",
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
            et_hhmm="10:13",
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
            et_hhmm="10:03",
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
            et_hhmm="10:03",
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
        s = new_session("2026-09-29")
        d = decide_starter(
            s,
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
        self.assertEqual(d["action"], "skip_bounce_against")
        self.assertFalse(d["post"])
        self.assertEqual(s.skip_bounce_n, 1)
        self.assertEqual(s.session_starters_n, 0)
        self.assertIn(10.0, s.consumed)
        again = decide_starter(
            s,
            send_ts=10.0,
            direction="BEAR",
            send_spy=764.78,
            spy=764.80,
            bar_high=764.85,
            bar_low=764.70,
            et_hhmm="10:13",
            ask=1.57,
            **CLEAN_Q,
        )
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
            **CLEAN_Q,
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
            **CLEAN_Q,
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
        open_ok = self._base(s, send_ts=10.09, et_hhmm="10:09")
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

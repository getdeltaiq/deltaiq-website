"""Admin is the full set. Sub is the actionable subset."""

from __future__ import annotations

import unittest

from engine.overlay.publish import (
    Candidate,
    PublishLedgers,
    decide_sub_send,
    with_spy_deltas,
)


def _c(**kwargs) -> Candidate:
    base = dict(
        ts=1.0,
        direction="BEAR",
        spy=764.05,
        pre_move_spy=0.40,
        armed=True,
        choppy=False,
        on_arm_bar=False,
        chase_spy=0.10,
        et_hhmm="11:02",
        copy="armed_rip",
        same_dir_age_sec=None,
        dte=0,
    )
    base.update(kwargs)
    if "rip_1m_spy" not in kwargs:
        sign = 1.0 if base["direction"] == "BULL" else -1.0
        base["rip_1m_spy"] = sign * 0.25
    if "trend_3m_spy" not in kwargs:
        sign = 1.0 if base["direction"] == "BULL" else -1.0
        base["trend_3m_spy"] = sign * 0.60
    return Candidate(**base)


class LedgerHierarchyTests(unittest.TestCase):
    def test_admin_gets_every_candidate_sub_is_subset(self):
        p = PublishLedgers("2026-09-29")
        p.ingest(_c(ts=1, pre_move_spy=0.22, armed=True))  # weak → admin only
        p.ingest(_c(ts=2, pre_move_spy=0.40, armed=True))  # send
        p.ingest(_c(ts=3, choppy=True))  # admin only
        self.assertEqual(p.admin_n, 3)
        self.assertEqual(p.sub_n, 1)
        self.assertTrue(p.admin_n >= p.sub_n)
        h = p.health()
        self.assertEqual(h["admin_rows"], "admin_alert_ledger")
        self.assertEqual(h["plot"], "sub_alert_send")
        self.assertEqual(h["sms_from"], "sub_alert_send")
        self.assertFalse(h["aligned_copy"])
        self.assertTrue(h["invariant_ok"])
        self.assertTrue(h["skip_strong_pre_move"])
        self.assertEqual(h["pre_move_strong"], 0.50)
        self.assertTrue(h["channels_aligned"])
        self.assertTrue(h["sms_iff_sub_send"])

    def test_unarmed_is_admin_only(self):
        d = decide_sub_send(_c(armed=False))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_not_armed")

    def test_weak_pre_move_admin_only(self):
        d = decide_sub_send(_c(pre_move_spy=0.22))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_weak_pre_move")

    def test_chase_admin_only(self):
        d = decide_sub_send(_c(chase_spy=0.55))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_chase")

    def test_strong_pre_move_admin_only(self):
        d = decide_sub_send(_c(pre_move_spy=0.55))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_strong_pre_move")

    def test_arm_bar_admin_only(self):
        d = decide_sub_send(_c(on_arm_bar=True))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_arm_bar")

    def test_choppy_admin_only(self):
        d = decide_sub_send(_c(choppy=True))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_choppy")

    def test_same_dir_lock_admin_only(self):
        d = decide_sub_send(_c(same_dir_age_sec=300))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_same_dir_lock")

    def test_1min_climax_is_admin_only(self):
        d = decide_sub_send(
            _c(direction="BULL", rip_1m_spy=0.44, trend_3m_spy=0.12)
        )
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_1min_rip")

    def test_1054_3min_dump_is_sub_send(self):
        d = decide_sub_send(
            _c(direction="BEAR", rip_1m_spy=-0.28, trend_3m_spy=-0.87)
        )
        self.assertTrue(d["send"])

    def test_1058_stalled_last_minute_is_admin_only(self):
        d = decide_sub_send(
            _c(direction="BULL", et_hhmm="10:58", rip_1m_spy=0.03, trend_3m_spy=0.505)
        )
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_1min_stall")

    def test_1142_oct5_stall_is_admin_only(self):
        d = decide_sub_send(
            _c(direction="BULL", et_hhmm="11:42", rip_1m_spy=0.02, trend_3m_spy=0.25)
        )
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_1min_stall")

    def test_1222_continuing_rip_still_sends(self):
        d = decide_sub_send(
            _c(direction="BULL", et_hhmm="12:22", rip_1m_spy=0.175, trend_3m_spy=0.235)
        )
        self.assertTrue(d["send"])

    def test_missing_1m_3m_is_admin_only(self):
        d = decide_sub_send(_c(rip_1m_spy=None, trend_3m_spy=None))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_1min_unconfirmed")
        d = decide_sub_send(_c())
        self.assertTrue(d["send"])
        self.assertEqual(d["reason"], "sub_alert_send")

    def test_prod_tape_stamps_1m_3m(self):
        # 10/2 11:42 +0.44 / +0.12 is admin-only. 10:54 −0.28 / −0.87 is SUB.
        climax = with_spy_deltas(
            _c(direction="BULL", rip_1m_spy=None, trend_3m_spy=None),
            [769.29, 769.21, 768.97, 769.41],
        )
        self.assertAlmostEqual(climax.rip_1m_spy, 0.44, places=2)
        self.assertAlmostEqual(climax.trend_3m_spy, 0.12, places=2)
        d = decide_sub_send(climax)
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_1min_rip")
        dump = with_spy_deltas(
            _c(direction="BEAR", et_hhmm="10:54", rip_1m_spy=None, trend_3m_spy=None),
            [771.81, 771.22, 771.22, 770.94],
        )
        self.assertAlmostEqual(dump.rip_1m_spy, -0.28, places=2)
        self.assertAlmostEqual(dump.trend_3m_spy, -0.87, places=2)
        d = decide_sub_send(dump)
        self.assertTrue(d["send"])
        short = with_spy_deltas(_c(rip_1m_spy=0.25, trend_3m_spy=-0.60), [1.0, 2.0])
        self.assertIsNone(short.rip_1m_spy)
        self.assertEqual(decide_sub_send(short)["reason"], "skip_1min_unconfirmed")

    def test_1014_0dte_is_admin_only(self):
        d = decide_sub_send(_c(et_hhmm="10:14", dte=0, direction="BEAR", spy=768.17))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_0dte_open_fade")

    def test_1243_0dte_is_admin_only(self):
        d = decide_sub_send(_c(et_hhmm="12:43", dte=0, direction="BULL", spy=768.41))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_0dte_near_cutover")

    def test_1237_0dte_is_admin_only_1dte_trend_sends(self):
        d0 = decide_sub_send(
            _c(
                et_hhmm="12:37",
                dte=0,
                direction="BEAR",
                rip_1m_spy=-0.08,
                trend_3m_spy=-0.35,
            )
        )
        self.assertFalse(d0["send"])
        self.assertEqual(d0["reason"], "skip_0dte_near_cutover")
        d1 = decide_sub_send(
            _c(
                et_hhmm="12:37",
                dte=1,
                regime="TREND",
                direction="BEAR",
                rip_1m_spy=-0.22,
                trend_3m_spy=-0.67,
            )
        )
        self.assertTrue(d1["send"])

    def test_oct8_1217_spike_is_admin_only(self):
        d = decide_sub_send(
            _c(direction="BULL", et_hhmm="12:17", rip_1m_spy=1.55, trend_3m_spy=1.61)
        )
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_1min_rip")

    def test_morning_rip_still_sends(self):
        d = decide_sub_send(_c(et_hhmm="09:38"))
        self.assertTrue(d["send"])

    def test_open_fade_1dte_trend_still_sends(self):
        d = decide_sub_send(_c(et_hhmm="10:14", dte=1, regime="TREND"))
        self.assertTrue(d["send"])

    def test_1148_0dte_still_sends(self):
        d = decide_sub_send(_c(et_hhmm="11:48", dte=0))
        self.assertTrue(d["send"])

    def test_1516_after_cutover_still_sends(self):
        d = decide_sub_send(_c(et_hhmm="15:16", dte=1))
        self.assertTrue(d["send"])

    def test_outside_window_admin_only(self):
        d = decide_sub_send(_c(et_hhmm="09:20"))
        self.assertFalse(d["send"])

    def test_1001_0dte_is_admin_only(self):
        d = decide_sub_send(_c(et_hhmm="10:01", dte=0))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_0dte_open_fade")

    def test_missing_dte_is_admin_only(self):
        d = decide_sub_send(_c(dte=None))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_dte_unknown")


class ChannelAlignTests(unittest.TestCase):
    """SUB SMS and Tradier BTO share sub_action_skip_reason.

    aligned_copy=false is SMS body ('sign in to view'), not 'not a trade'.
    If overlay send=True, starter must post (no bounce/halt). If starter
    skips on the shared stack, overlay must not SMS.
    """

    def _starter(self, c, **kw):
        from engine.shared.gates import decide_starter, new_session

        s = new_session("2026-09-30")
        dte = c.dte
        if dte is None or dte <= 0:
            option_symbol = "SPY260930P00765000"
        else:
            option_symbol = "SPY261001P00765000"
        args = dict(
            send_ts=c.ts,
            direction=c.direction,
            send_spy=c.spy,
            spy=c.spy,
            bar_high=c.spy + 0.05,
            bar_low=c.spy - 0.05,
            et_hhmm=c.et_hhmm,
            ask=1.50,
            choppy=c.choppy,
            on_arm_bar=c.on_arm_bar,
            pre_move_spy=c.pre_move_spy,
            chase_spy=c.chase_spy,
            regime=c.regime,
            same_dir_age_sec=c.same_dir_age_sec,
            plot="sub_alert_send",
            overlay_queued=True,
            is_opposite=False,
            option_symbol=option_symbol,
            dte=dte,
            rip_1m_spy=c.rip_1m_spy,
            trend_3m_spy=c.trend_3m_spy,
        )
        args.update(kw)
        return s, decide_starter(s, **args)

    def test_1001_0dte_sms_and_bto_same_skip(self):
        c = _c(et_hhmm="10:01", dte=0, ts=1001.0)
        sms = decide_sub_send(c)
        _, bto = self._starter(c)
        self.assertFalse(sms["send"])
        self.assertFalse(bto["post"])
        self.assertEqual(sms["reason"], "skip_0dte_open_fade")
        self.assertEqual(bto["action"], sms["reason"])

    def test_clean_midbook_sms_and_bto_both_fire(self):
        c = _c(et_hhmm="11:02", dte=0, ts=1102.0)
        sms = decide_sub_send(c)
        _, bto = self._starter(c)
        self.assertTrue(sms["send"])
        self.assertTrue(bto["post"])
        self.assertEqual(sms["reason"], "sub_alert_send")
        self.assertEqual(bto["action"], "bto")

    def test_1min_rip_sms_and_bto_same_skip(self):
        c = _c(
            direction="BULL",
            rip_1m_spy=0.44,
            trend_3m_spy=0.12,
            ts=1142.0,
            et_hhmm="11:42",
        )
        sms = decide_sub_send(c)
        _, bto = self._starter(c)
        self.assertFalse(sms["send"])
        self.assertFalse(bto["post"])
        self.assertEqual(sms["reason"], "skip_1min_rip")
        self.assertEqual(bto["action"], sms["reason"])

    def test_choppy_sms_and_bto_same_skip(self):
        c = _c(choppy=True, ts=1200.0)
        sms = decide_sub_send(c)
        _, bto = self._starter(c)
        self.assertFalse(sms["send"])
        self.assertFalse(bto["post"])
        self.assertEqual(sms["reason"], "skip_choppy")
        self.assertEqual(bto["action"], sms["reason"])

    def test_1dte_not_trend_before_cutover_sms_and_bto_same_skip(self):
        c = _c(et_hhmm="11:02", dte=1, regime="RANGE", ts=1300.0)
        sms = decide_sub_send(c)
        _, bto = self._starter(c, option_symbol="SPY261001P00765000")
        self.assertFalse(sms["send"])
        self.assertFalse(bto["post"])
        self.assertEqual(sms["reason"], "skip_1dte_not_trend")
        self.assertEqual(bto["action"], sms["reason"])

    def test_overlay_send_iff_starter_would_post(self):
        cases = [
            _c(et_hhmm="10:01", dte=0, ts=1.0),
            _c(et_hhmm="10:14", dte=0, ts=2.0),
            _c(et_hhmm="11:02", dte=0, ts=3.0),
            _c(et_hhmm="11:48", dte=0, ts=4.0),
            _c(et_hhmm="12:43", dte=0, ts=5.0),
            _c(et_hhmm="15:16", dte=1, ts=6.0),
            _c(
                et_hhmm="12:17",
                dte=0,
                direction="BULL",
                rip_1m_spy=1.55,
                trend_3m_spy=1.61,
                ts=11.0,
            ),
            _c(
                et_hhmm="12:37",
                dte=1,
                regime="TREND",
                direction="BEAR",
                rip_1m_spy=-0.22,
                trend_3m_spy=-0.67,
                ts=12.0,
            ),
            _c(choppy=True, ts=7.0),
            _c(chase_spy=0.55, ts=8.0),
            _c(et_hhmm="11:02", dte=1, regime="RANGE", ts=9.0),
            _c(et_hhmm="09:38", dte=0, ts=10.0),
        ]
        for c in cases:
            sms = decide_sub_send(c)
            _, bto = self._starter(c)
            self.assertEqual(
                sms["send"],
                bto["post"],
                msg=f"{c.et_hhmm} dte={c.dte} sms={sms} bto={bto}",
            )
            if not sms["send"] and sms["reason"] not in (
                "skip_outside_window",
                "skip_not_armed",
                "skip_copy",
            ):
                self.assertEqual(bto["action"], sms["reason"])


class HookHaltTests(unittest.TestCase):
    def test_health_names_github_module(self):
        from engine.shared.gates import new_session
        from engine.tradier_exec.hooks import health_overlay

        st = new_session("2026-09-29")
        h = health_overlay(st)
        self.assertEqual(h["gates_module"], "engine.shared.gates")
        self.assertTrue(h["decide_starter"])
        self.assertEqual(h["skip_bounce_n"], 0)

    def test_health_advertises_ladder_not_trail(self):
        from engine.shared.gates import ENGINE_EXIT_MODE, new_session
        from engine.tradier_exec.hooks import health_overlay

        st = new_session("2026-09-29")
        h = health_overlay(st)
        self.assertEqual(h["engine_exit_mode"], ENGINE_EXIT_MODE)
        self.assertEqual(h["engine_exit_mode"], "ladder_to_market")
        self.assertTrue(h["override_trail"])
        self.assertTrue(h["before_bto"])
        self.assertTrue(h["on_manage"])
        self.assertTrue(h["bto_requires_new_send"])
        self.assertFalse(h["chop_size"])
        self.assertTrue(h["skip_choppy_is_refuse"])
        self.assertFalse(h["use_trail"])
        self.assertEqual(h["take_exit"], "ladder_to_market")
        self.assertEqual(h["bto_source"], "sub_alert_send")
        self.assertFalse(h["queue_opposite"])
        self.assertTrue(h["rec_book"])
        self.assertEqual(h["rec_book_ship"], "2026-10-08-climax-1dte-book")
        self.assertTrue(h["protect_from_high"])
        self.assertEqual(h["peak_giveback_usd"], 50.0)
        self.assertTrue(h["skip_1min_rip"])
        self.assertEqual(h["one_min_rip_usd"], 0.20)
        self.assertEqual(h["one_min_climax_frac"], 0.80)
        self.assertEqual(h["one_min_climax_usd"], 0.50)
        self.assertTrue(h["skip_1min_stall"])
        self.assertEqual(h["stall_1m_usd"], 0.08)
        self.assertTrue(h["recover_lost_owned"])
        self.assertTrue(h["orphan_adopt_flattens"])
        self.assertEqual(h["protective_stop_usd"], 0.15)
        self.assertEqual(h["protective_stop_1dte_usd"], 0.30)
        self.assertIsNone(h["fail_sec_1dte"])
        self.assertTrue(h["channels_aligned"])
        self.assertTrue(h["sms_iff_sub_send"])
        self.assertEqual(h["sms_from"], "sub_alert_send")
        self.assertTrue(h["fail_streak_0dte_only"])
        self.assertTrue(h["extra_bto"])
        self.assertEqual(h["extra_bto_fill_to"], 16)
        self.assertTrue(h["skip_1dte_not_trend"])
        self.assertEqual(h["dte_cutover_et"], "12:45")
        self.assertEqual(h["dte_book_et"], "12:30")
        self.assertTrue(h["trade_1dte_from_book_et"])
        self.assertTrue(h["skip_0dte_after_cutover"])
        self.assertTrue(h["trade_1dte_after_cutover"])
        self.assertTrue(h["skip_0dte_open_fade"])
        self.assertEqual(h["open_fade_window"], ["10:00", "10:20"])
        self.assertTrue(h["skip_0dte_near_cutover"])
        self.assertEqual(h["near_cutover_0dte_et"], "12:30")
        self.assertEqual(h["cooldown_after_fail_sec"], 480.0)
        self.assertEqual(h["session_loss_halt_usd"], 750.0)
        self.assertTrue(h["before_stc"])
        self.assertTrue(h["stc_requires_envelope"])
        self.assertFalse(h["stc_on_bto_fill"])
        self.assertFalse(h["working_stc_on_fill"])
        self.assertTrue(h["skip_already_flat"])
        self.assertEqual(h["flatten_limit_thru_usd"], 0.0)
        self.assertEqual(h["hold_exit"], "hold")

    def test_before_bto_blocks_on_keep_halt_date(self):
        from engine.shared.gates import new_session
        from engine.tradier_exec.hooks import SESSION_KEEP_HALT_DATE, before_bto, today_et

        if today_et() != SESSION_KEEP_HALT_DATE:
            return
        st = new_session(today_et())
        d = before_bto(
            st,
            send_ts=99.0,
            direction="BEAR",
            send_spy=764.05,
            spy=764.05,
            bar_high=764.10,
            bar_low=764.00,
            et_hhmm="16:40",
            ask=2.59,
            choppy=False,
            on_arm_bar=False,
            pre_move_spy=0.40,
            chase_spy=0.10,
            regime="TREND",
            plot="sub_alert_send",
            overlay_queued=True,
            is_opposite=False,
        )
        self.assertFalse(d["post"])
        self.assertTrue(st.session_halt)


if __name__ == "__main__":
    unittest.main()

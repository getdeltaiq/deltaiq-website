"""Admin is the full set. Sub is the actionable subset."""

from __future__ import annotations

import unittest

from engine.overlay.publish import Candidate, PublishLedgers, decide_sub_send


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
    )
    base.update(kwargs)
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

    def test_quality_rip_is_sub_send(self):
        d = decide_sub_send(_c())
        self.assertTrue(d["send"])
        self.assertEqual(d["reason"], "sub_alert_send")

    def test_1014_0dte_is_admin_only(self):
        d = decide_sub_send(_c(et_hhmm="10:14", dte=0, direction="BEAR", spy=768.17))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_0dte_open_fade")

    def test_1243_0dte_is_admin_only(self):
        d = decide_sub_send(_c(et_hhmm="12:43", dte=0, direction="BULL", spy=768.41))
        self.assertFalse(d["send"])
        self.assertEqual(d["reason"], "skip_0dte_near_cutover")

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
        self.assertEqual(h["rec_book_ship"], "2026-09-30-alert-quality")
        self.assertTrue(h["fail_streak_0dte_only"])
        self.assertTrue(h["extra_bto"])
        self.assertEqual(h["extra_bto_fill_to"], 16)
        self.assertTrue(h["skip_1dte_not_trend"])
        self.assertEqual(h["dte_cutover_et"], "12:45")
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

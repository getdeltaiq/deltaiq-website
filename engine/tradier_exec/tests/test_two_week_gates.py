"""Climax + 12:30 1DTE book on the last two weeks of SPY 1-minute tape."""

from __future__ import annotations

import unittest

from engine.overlay.publish import decide_sub_send
from engine.shared.gates import starter_dte_for_clock
from engine.tradier_exec.two_week_gates import (
    SESSIONS,
    load_tape,
    lookup,
    replay_vs_pre_climax_book,
    walk_tape,
)


class TwoWeekGateReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tape = load_tape()
        cls.diff = replay_vs_pre_climax_book(cls.tape)
        cls.rows = cls.diff["rows"]

    def test_eleven_sessions_through_oct8(self):
        self.assertEqual(tuple(self.tape), SESSIONS)
        self.assertEqual(len(self.tape["2026-09-24"]), 390)
        self.assertGreaterEqual(len(self.tape["2026-10-08"]), 250)

    def test_documented_winners_still_send(self):
        keep = [
            ("2026-09-24", "10:43", "BEAR", 0),
            ("2026-09-25", "11:55", "BULL", 0),
            ("2026-09-28", "10:42", "BEAR", 0),
            ("2026-10-02", "10:54", "BEAR", 0),
            ("2026-10-05", "12:22", "BULL", 0),
            ("2026-10-08", "12:37", "BEAR", 1),
            ("2026-10-08", "12:38", "BEAR", 1),
        ]
        for day, et, direction, dte in keep:
            r = lookup(self.rows, day, et)
            self.assertIsNotNone(r, msg=f"missing {day} {et}")
            self.assertTrue(r["send"], msg=r)
            self.assertEqual(r["dir"], direction)
            self.assertEqual(r["dte"], dte)
            self.assertEqual(starter_dte_for_clock(et), dte)

    def test_documented_skips_stay_admin_only(self):
        skips = [
            ("2026-10-02", "11:42", "skip_1min_rip"),
            ("2026-10-05", "10:58", "skip_1min_stall"),
            ("2026-10-05", "11:42", "skip_1min_stall"),
            ("2026-10-08", "12:17", "skip_1min_rip"),
        ]
        for day, et, reason in skips:
            r = lookup(self.rows, day, et)
            self.assertIsNotNone(r, msg=f"missing {day} {et}")
            self.assertFalse(r["send"], msg=r)
            self.assertEqual(r["reason"], reason)

    def test_oct8_1217_is_the_climax_the_old_book_would_have_sent(self):
        r = lookup(self.rows, "2026-10-08", "12:17")
        self.assertAlmostEqual(r["rip_1m_spy"], 1.55, places=2)
        self.assertAlmostEqual(r["trend_3m_spy"], 1.61, places=2)
        self.assertGreaterEqual(r["frac"], 0.80)
        removed = {(x["day"], x["et"]) for x in self.diff["removed"]}
        self.assertIn(("2026-10-08", "12:17"), removed)

    def test_930_1244_0dte_stays_near_cutover_1dte_trend_can_send(self):
        from engine.overlay.publish import Candidate

        leftover = decide_sub_send(
            Candidate(
                ts=1.0,
                direction="BULL",
                spy=768.41,
                pre_move_spy=0.40,
                armed=True,
                choppy=False,
                on_arm_bar=False,
                chase_spy=0.10,
                et_hhmm="12:44",
                dte=0,
                regime="TREND",
                rip_1m_spy=0.20,
                trend_3m_spy=0.40,
            )
        )
        self.assertFalse(leftover["send"])
        self.assertEqual(leftover["reason"], "skip_0dte_near_cutover")
        r = lookup(self.rows, "2026-09-30", "12:43")
        self.assertTrue(r["send"])
        self.assertEqual(r["dte"], 1)
        self.assertEqual(r["dir"], "BULL")

    def test_1230_book_adds_only_1dte_in_the_old_dark_window(self):
        added = self.diff["added"]
        self.assertEqual(len(added), 41)
        for r in added:
            self.assertEqual(r["dte"], 1)
            self.assertGreaterEqual(r["et"], "12:30")
            self.assertLess(r["et"], "12:45")
            self.assertTrue(r["send"])
        oct8 = [r for r in added if r["day"] == "2026-10-08"]
        ets = {r["et"] for r in oct8}
        self.assertIn("12:37", ets)
        self.assertIn("12:38", ets)

    def test_climax_removes_seventeen_old_sends_including_1217(self):
        removed = self.diff["removed"]
        self.assertEqual(len(removed), 17)
        for r in removed:
            self.assertGreaterEqual(abs(r["rip_1m_spy"]), 0.50)
            self.assertGreaterEqual(r["frac"], 0.80)
        self.assertEqual(self.diff["old_send_n"], 791)
        self.assertEqual(self.diff["new_send_n"], 815)

    def test_open_fade_0dte_has_no_tape_sends(self):
        fade = [
            r
            for r in self.diff["new_sends"]
            if r["dte"] == 0 and "10:00" <= r["et"] <= "10:20"
        ]
        self.assertEqual(fade, [])

    def test_walk_tape_matches_overlay_decide(self):
        rows = walk_tape(self.tape)
        self.assertGreater(len(rows), 3000)
        self.assertEqual(sum(1 for r in rows if r["send"]), 815)

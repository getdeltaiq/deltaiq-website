"""Last-week cover-us: cut blow-up tails, keep green-day nets, skip post-halt winners."""

from __future__ import annotations

import unittest

from engine.shared.gates import envelope_hit, new_session
from engine.tradier_exec.week_replay import (
    envelope_lot,
    live_session_pnl,
    load_lots,
    replay_929,
    walk_session,
    week_summary,
)


class EnvelopeLotTests(unittest.TestCase):
    def test_fourteen_lot_770c_caps_at_protective(self):
        env, fail = envelope_lot(14, 1912.35, 623.58)
        self.assertTrue(fail)
        self.assertEqual(env, -210.0)
        live = round(623.58 - 1912.35, 2)
        self.assertLess(live, -1200)
        self.assertEqual(
            envelope_hit(
                fill_px=1.36,
                mark_bid=1.21,
                qty=14,
                spy_adverse=0.20,
                seconds_since_fill=12,
                ticket_phase="FAIL",
            ),
            "protective",
        )


class HaltWalkTests(unittest.TestCase):
    def test_nine_twenty_three_skips_blowup_and_tiny_wins(self):
        lots = load_lots()["days"]["2026-09-23"]
        w = walk_session(lots, oldest_first=True, session_date="2026-09-23")
        self.assertEqual(w["halt_reason"], "consecutive_fail")
        skipped_live = [round(x["proceeds"] - x["cost"], 2) for x in w["skipped"]]
        self.assertTrue(any(x < -1200 for x in skipped_live))
        self.assertGreater(w["cover"], -450)
        self.assertLess(w["live_lots"], -1600)

    def test_nine_twenty_four_keeps_the_puts(self):
        lots = load_lots()["days"]["2026-09-24"]
        w = walk_session(lots, oldest_first=True, session_date="2026-09-24")
        self.assertEqual(w["halt_reason"], "none")
        self.assertEqual(w["n_skipped"], 0)
        self.assertGreater(w["wins_kept"], 600)
        self.assertEqual(w["wins_skipped"], 0)
        self.assertGreater(w["cover"], 200)

    def test_nine_twenty_five_keeps_the_1dte_runner(self):
        lots = load_lots()["days"]["2026-09-25"]
        w = walk_session(lots, oldest_first=True, session_date="2026-09-25")
        self.assertGreater(w["cover"], 0)
        self.assertTrue(any(x["live"] > 200 for x in w["kept"]))

    def test_nine_twenty_eight_keeps_the_0dte_put(self):
        lots = load_lots()["days"]["2026-09-28"]
        w = walk_session(lots, oldest_first=True, session_date="2026-09-28")
        self.assertGreater(w["cover"], 800)
        self.assertTrue(any(x["live"] > 1400 for x in w["kept"]))

    def test_halt_does_not_carry_to_next_et_date(self):
        a = new_session("2026-09-23")
        a.on_flatten(-210.0, "FAIL")
        a.on_flatten(-50.0, "FAIL")
        a.on_flatten(-50.0, "FAIL")
        a.on_flatten(-50.0, "FAIL")
        self.assertTrue(a.session_halt)
        b = new_session("2026-09-24")
        self.assertFalse(b.session_halt)
        self.assertEqual(b.session_halt_reason, "none")
        self.assertEqual(b.consecutive_fail_n, 0)

    def test_post_halt_winner_is_not_kept(self):
        # Newest-first: winner printed last in the session, four losers older.
        lots = [
            {"qty": 11, "cost": 1819.99, "proceeds": 3283.9},
            {"qty": 10, "cost": 1500.0, "proceeds": 1400.0},
            {"qty": 10, "cost": 1500.0, "proceeds": 1400.0},
            {"qty": 10, "cost": 1500.0, "proceeds": 1400.0},
            {"qty": 10, "cost": 1500.0, "proceeds": 1400.0},
        ]
        w = walk_session(lots, oldest_first=True)
        self.assertTrue(w["halted"])
        self.assertGreater(w["wins_skipped"], 1400)
        self.assertEqual(w["wins_kept"], 0)


class NineTwentyNineTests(unittest.TestCase):
    def test_bounce_beats_live_and_still_halts(self):
        live = live_session_pnl()["2026-09-29"]
        b = replay_929(bounce_open=True, cap_envelope=True)
        nb = replay_929(bounce_open=False, cap_envelope=True)
        self.assertTrue(b["halted"])
        self.assertTrue(nb["halted"])
        self.assertGreater(b["cover"], live)
        self.assertGreater(nb["cover"], live)
        self.assertGreater(b["cover"], nb["cover"])
        self.assertGreater(b["cover"], -700)
        self.assertEqual(b["n_kept"], 4)
        self.assertNotIn(14, b["qty"])


class WeekNetTests(unittest.TestCase):
    def test_cover_week_avoids_the_bleed(self):
        s = week_summary()
        self.assertLess(s["live_week"], -2400)
        self.assertGreater(s["cover_week"], -500)
        self.assertGreater(s["cover_week"], s["live_week"] + 2000)
        # Green sessions still print a plus once halt resets.
        self.assertGreater(s["days"]["2026-09-24"]["cover"], 0)
        self.assertGreater(s["days"]["2026-09-25"]["cover"], 0)
        self.assertGreater(s["days"]["2026-09-28"]["cover"], 800)
        self.assertGreater(s["cover_week"], 400)


if __name__ == "__main__":
    unittest.main()

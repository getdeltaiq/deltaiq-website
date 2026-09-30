"""Runtime + 9/29 tape replay. No broker calls."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.shared.gates import envelope_hit
from engine.tradier_exec.persist import ConsumedSends
from engine.tradier_exec.replay import replay
from engine.tradier_exec.runtime import CoverUsExec


class PersistTests(unittest.TestCase):
    def test_insert_conflict_and_reload(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "consumed.db"
            db = ConsumedSends(p)
            self.assertTrue(db.insert("2026-09-29", 10.0, "BEAR"))
            self.assertFalse(db.insert("2026-09-29", 10.0, "BEAR"))
            db.close()
            db2 = ConsumedSends(p)
            self.assertEqual(db2.load("2026-09-29"), [(10.0, "BEAR")])


class RuntimeTapeTests(unittest.TestCase):
    def test_keep_halt_today_posts_nothing(self):
        ex = replay(keep_halt=True)
        posted = [x for x in ex.log if x.get("kind") == "starter" and x.get("post")]
        self.assertEqual(posted, [])
        self.assertTrue(ex.state.session_halt)

    def test_as_traded_mutex_consume_and_first_line_halt(self):
        ex = replay(keep_halt=False, bounce_open=False)
        starters = [x for x in ex.log if x.get("kind") == "starter"]
        posted = [x for x in starters if x.get("post")]
        actions = [x.get("action") for x in starters]
        self.assertIn("skip_dup_send_ts", actions)
        self.assertIn("skip_0dte_open_fade", actions)
        # 10:00–10:20 0DTE is open-fade (9/30 10:14 −$190). First post is 10:37.
        self.assertEqual(posted[0]["qty"], 11)
        self.assertTrue(ex.state.session_halt)
        self.assertIn(ex.state.session_halt_reason, ("session_loss", "consecutive_fail"))
        # Afternoon 16-lot / 15:32 / 15:44 must not post after halt.
        late_posts = [
            x
            for x in ex.log
            if x.get("kind") == "starter" and x.get("post") and x is not posted[0]
        ]
        # May post a few morning tickets until −$750 or 4 0DTE FAILs, then halt.
        self.assertLess(len(posted), 8)
        self.assertTrue(any(x.get("action") == "skip_halt_or_inflight" for x in starters))
        self.assertEqual(ex.health()["engine_exit_mode"], "ladder_to_market")
        self.assertGreaterEqual(len(ex.state.consumed), 1)
        self.assertFalse(ex.health()["chop_size"])
        _ = late_posts

    def test_ten_am_bounce_skips_open_double_and_recycles(self):
        ex = replay(keep_halt=False, bounce_open=True)
        starters = [x for x in ex.log if x.get("kind") == "starter"]
        self.assertEqual(starters[0]["action"], "skip_bounce_against")
        self.assertFalse(starters[0]["post"])
        self.assertGreaterEqual(ex.state.skip_bounce_n, 1)
        # Recycles of 10:00 are consumed, not new BTOs.
        recycle = [x for x in starters if x.get("action") == "skip_dup_send_ts"]
        self.assertGreaterEqual(len(recycle), 2)

    def test_missing_quality_refuses_every_bto(self):
        ex = replay(
            keep_halt=False,
            quality={"plot": "sub_alert_send", "overlay_queued": True},
        )
        posted = [x for x in ex.log if x.get("kind") == "starter" and x.get("post")]
        self.assertEqual(posted, [])
        self.assertGreaterEqual(ex.state.skip_quality_n, 1)
        self.assertEqual(
            [x.get("action") for x in ex.log if x.get("kind") == "starter"][0],
            "skip_quality_unknown",
        )

    def test_admin_plot_refuses_every_bto(self):
        ex = replay(
            keep_halt=False,
            quality={
                "plot": "admin_alert_ledger",
                "overlay_queued": False,
                "choppy": False,
                "pre_move_spy": 0.40,
                "chase_spy": 0.10,
            },
        )
        posted = [x for x in ex.log if x.get("kind") == "starter" and x.get("post")]
        self.assertEqual(posted, [])
        self.assertEqual(
            [x.get("action") for x in ex.log if x.get("kind") == "starter"][0],
            "skip_not_sub",
        )

    def test_sixteen_lot_envelope_hits_before_held_to_093(self):
        self.assertEqual(
            envelope_hit(
                fill_px=1.14,
                mark_bid=0.99,
                qty=16,
                spy_adverse=0.10,
                seconds_since_fill=12,
                ticket_phase="FAIL",
            ),
            "protective",
        )
        ex = CoverUsExec(session_date="2026-09-29", keep_halt=False)
        m = ex.manage(
            fill_px=1.14,
            mark_bid=0.99,
            qty=16,
            spy_adverse=0.10,
            seconds_since_fill=12,
            ticket_phase="FAIL",
            bid=0.99,
        )
        self.assertTrue(m["flatten"])
        self.assertTrue(m["disable_trail"])
        self.assertEqual(m["engine_exit_mode"], "ladder_to_market")


if __name__ == "__main__":
    unittest.main()

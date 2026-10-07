"""10/7: live $6,894 is SoT; empty history / unpriced market STC must not zero the day."""

from __future__ import annotations

import unittest

from engine.tradier_exec.sources import (
    SOURCE_RANK,
    backfill_close_px,
    options_pnl_from_sources,
    pnl_label,
    prior_session_close,
    reconcile_session,
    session_close,
    should_drop_unpriced_close,
)


OCT7_HIST = [
    {"date": "2026-09-30", "value": 5379.79},
    {"date": "2026-10-01", "value": 4846.94},
    {"date": "2026-10-02", "value": 5481.77},
    {"date": "2026-10-05", "value": 6405.79},
    {"date": "2026-10-06", "value": 6591.54},
]


class SourceRankTests(unittest.TestCase):
    def test_live_equity_is_first(self):
        self.assertEqual(SOURCE_RANK[0], "live_equity")
        self.assertLess(
            SOURCE_RANK.index("live_equity"),
            SOURCE_RANK.index("account_history"),
        )
        self.assertLess(
            SOURCE_RANK.index("gainloss_proceeds"),
            SOURCE_RANK.index("account_history"),
        )


class Oct7LiveBalanceTests(unittest.TestCase):
    def test_prior_close_is_posted_oct6_not_stale_live(self):
        opening, d = prior_session_close(OCT7_HIST, "2026-10-07")
        self.assertEqual(d, "2026-10-06")
        self.assertAlmostEqual(opening, 6591.54, places=2)

    def test_close_uses_live_until_hist_posts(self):
        close, src = session_close(OCT7_HIST, "2026-10-07", 6894.02)
        self.assertEqual(src, "live")
        self.assertAlmostEqual(close, 6894.02, places=2)

    def test_session_net_is_plus_302_not_ticket_minus_284(self):
        rec = reconcile_session(
            hist=OCT7_HIST,
            session_date="2026-10-07",
            live_equity=6894.02,
            ticket_sum=-284.0,
        )
        self.assertEqual(rec["label"], "WIN")
        self.assertAlmostEqual(rec["session_net"], 302.48, places=2)
        self.assertTrue(rec["used_live_close"])
        self.assertAlmostEqual(rec["ticket_gap"], 586.48, places=2)
        self.assertNotEqual(pnl_label(rec["session_net"]), "LOSS")

    def test_stale_6600_live_must_not_beat_posted_hist_once_present(self):
        hist = OCT7_HIST + [{"date": "2026-10-06", "value": 6591.54}]
        close, src = session_close(hist, "2026-10-06", 6600.20)
        self.assertEqual(src, "hist")
        self.assertAlmostEqual(close, 6591.54, places=2)

    def test_unpriced_market_stc_is_priced_from_gainloss(self):
        px = backfill_close_px(
            order_price=None,
            qty=16,
            gainloss_proceeds=16 * 1.545 * 100.0,
        )
        self.assertIsNotNone(px)
        self.assertFalse(should_drop_unpriced_close(px))
        self.assertTrue(should_drop_unpriced_close(None))

    def test_order_fill_price_wins_over_gainloss(self):
        px = backfill_close_px(
            order_price=2.28,
            qty=11,
            gainloss_proceeds=11 * 2.20 * 100.0,
        )
        self.assertAlmostEqual(px, 2.28, places=2)

    def test_empty_history_is_not_flat_zero_when_orders_filled(self):
        usd, src = options_pnl_from_sources(
            history_option_sum=None,
            gainloss_session_sum=None,
            has_filled_option_orders=True,
        )
        self.assertIsNone(usd)
        self.assertEqual(src, "fills_not_posted")

    def test_gainloss_used_when_history_empty(self):
        usd, src = options_pnl_from_sources(
            history_option_sum=None,
            gainloss_session_sum=302.48,
            has_filled_option_orders=True,
        )
        self.assertEqual(src, "gainloss")
        self.assertAlmostEqual(usd, 302.48, places=2)


if __name__ == "__main__":
    unittest.main()

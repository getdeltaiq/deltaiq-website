"""10/7: live $6,894 is SoT; posted history fills beat order limits / −$284 tape."""

from __future__ import annotations

import unittest

from engine.tradier_exec.sources import (
    HISTORY_OPTION_QUERY_TYPE,
    SOURCE_RANK,
    backfill_close_px,
    leftover_occ_qty,
    occ_round_trips,
    options_pnl_from_sources,
    orders_tape_covers_session,
    pnl_label,
    prior_session_close,
    reconcile_session,
    session_close,
    session_option_cash,
    should_drop_unpriced_close,
)


OCT7_HIST = [
    {"date": "2026-09-30", "value": 5379.79},
    {"date": "2026-10-01", "value": 4846.94},
    {"date": "2026-10-02", "value": 5481.77},
    {"date": "2026-10-05", "value": 6405.79},
    {"date": "2026-10-06", "value": 6591.54},
]


def _h(symbol: str, qty: float, price: float, amount: float, desc: str) -> dict:
    return {
        "date": "2026-10-07T00:00:00Z",
        "amount": amount,
        "type": "trade",
        "trade": {
            "symbol": symbol,
            "quantity": qty,
            "price": price,
            "description": desc,
            "trade_type": "option",
        },
    }


# Posted overnight 10/8. 21 option fills. Cash +$300.14, not limit-tape −$284.
OCT7_HISTORY = [
    _h("SPY261007C00775000", 16.0, 1.08, -1735.25, "CALL SPY 10/07/26 775"),
    _h("SPY261008P00778000", 9.0, 2.15, -1939.08, "PUT SPY 10/08/26 778"),
    _h("SPY261007P00776000", 11.0, 1.77, -1951.99, "PUT SPY 10/07/26 776"),
    _h("SPY261008C00777000", -9.0, 2.16, 1939.87, "CALL SPY 10/08/26 777"),
    _h("SPY261007C00775000", -16.0, 0.91, 1448.72, "CALL SPY 10/07/26 775"),
    _h("SPY261007P00775000", -12.0, 1.50, 1794.52, "PUT SPY 10/07/26 775"),
    _h("SPY261007P00775000", 12.0, 1.52, -1829.44, "PUT SPY 10/07/26 775"),
    _h("SPY261008P00778000", -9.0, 2.12, 1903.88, "PUT SPY 10/08/26 778"),
    _h("SPY261007P00775000", 12.0, 1.59, -1913.44, "PUT SPY 10/07/26 775"),
    _h("SPY261008P00778000", 9.0, 2.06, -1858.08, "PUT SPY 10/08/26 778"),
    _h("SPY261008C00777000", 9.0, 2.18, -1966.08, "CALL SPY 10/08/26 777"),
    _h("SPY261007P00774000", 16.0, 1.20, -1927.25, "PUT SPY 10/07/26 774"),
    _h("SPY261007C00774000", 16.0, 1.18, -1895.25, "CALL SPY 10/07/26 774"),
    _h("SPY261008P00778000", 9.0, 2.07, -1867.08, "PUT SPY 10/08/26 778"),
    _h("SPY261007C00776000", 16.0, 1.19, -1911.25, "CALL SPY 10/07/26 776"),
    _h("SPY261007P00774000", -16.0, 1.19, 1896.71, "PUT SPY 10/07/26 774"),
    _h("SPY261007C00774000", -16.0, 1.24, 1976.70, "CALL SPY 10/07/26 774"),
    _h("SPY261008P00778000", -18.0, 2.12, 3807.76, "PUT SPY 10/08/26 778"),
    _h("SPY261007P00776000", -11.0, 2.28, 2502.95, "PUT SPY 10/07/26 776"),
    _h("SPY261007C00776000", -16.0, 1.25, 1992.70, "CALL SPY 10/07/26 776"),
    _h("SPY261007P00775000", -12.0, 1.53, 1830.52, "PUT SPY 10/07/26 775"),
]


class SourceRankTests(unittest.TestCase):
    def test_live_equity_is_first(self):
        self.assertEqual(SOURCE_RANK[0], "live_equity")
        self.assertLess(
            SOURCE_RANK.index("live_equity"),
            SOURCE_RANK.index("account_history_fills"),
        )

    def test_posted_history_beats_same_day_order_limits(self):
        self.assertLess(
            SOURCE_RANK.index("account_history_fills"),
            SOURCE_RANK.index("orders_same_day_tape"),
        )
        self.assertEqual(HISTORY_OPTION_QUERY_TYPE, "trade")


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

    def test_history_fill_beats_order_limit(self):
        px = backfill_close_px(
            order_price=2.06,
            qty=18,
            history_price=2.12,
        )
        self.assertAlmostEqual(px, 2.12, places=2)

    def test_order_limit_used_only_when_history_missing(self):
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


class Oct7PostedHistoryTests(unittest.TestCase):
    def test_history_cash_is_plus_300_not_limit_tape_minus_284(self):
        cash = session_option_cash(OCT7_HISTORY)
        self.assertIsNotNone(cash)
        self.assertAlmostEqual(cash, 300.14, places=2)
        self.assertEqual(pnl_label(cash), "WIN")
        usd, src = options_pnl_from_sources(
            history_option_sum=cash,
            gainloss_session_sum=None,
            has_filled_option_orders=False,
        )
        self.assertEqual(src, "history")
        self.assertAlmostEqual(usd, 300.14, places=2)

    def test_empty_next_day_orders_is_not_flat(self):
        self.assertFalse(orders_tape_covers_session("2026-10-07", []))
        cash = session_option_cash(OCT7_HISTORY)
        self.assertNotEqual(pnl_label(cash), "FLAT")

    def test_market_stc_774_call_is_a_win_not_dropped(self):
        trips = occ_round_trips(OCT7_HISTORY)
        c774 = [t for t in trips if t["symbol"] == "SPY261007C00774000"]
        self.assertEqual(len(c774), 1)
        self.assertAlmostEqual(c774[0]["open"], 1.18, places=2)
        self.assertAlmostEqual(c774[0]["close"], 1.24, places=2)
        self.assertEqual(c774[0]["label"], "WIN")
        self.assertFalse(should_drop_unpriced_close(c774[0]["close"]))

    def test_778_put_18_lot_closed_at_fill_2_12_not_limit_2_06(self):
        trips = occ_round_trips(OCT7_HISTORY)
        p778 = [t for t in trips if t["symbol"] == "SPY261008P00778000"]
        closes = {round(t["close"], 2) for t in p778}
        self.assertIn(2.12, closes)
        self.assertNotIn(2.06, closes)

    def test_fifo_is_per_occ_no_leftover_spy(self):
        self.assertEqual(leftover_occ_qty(OCT7_HISTORY), {})
        trips = occ_round_trips(OCT7_HISTORY)
        self.assertAlmostEqual(sum(t["cash"] for t in trips), 300.14, places=2)
        symbols = {t["symbol"] for t in trips}
        self.assertGreaterEqual(len(symbols), 8)
        self.assertTrue(all("C" in t["symbol"] or "P" in t["symbol"] for t in trips))


if __name__ == "__main__":
    unittest.main()

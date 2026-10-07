"""Most-updated Tradier sources for daily recap / net-ticket grid.

10/7: live equity was $6,894.02 vs an incomplete ticket sum of −$284 because
get_orders omitted the market STC fill and empty history was treated as $0.
Do not let a staler source win.

Rank (freshest first):
  1. get_account_balances.totalEquity          live close
  2. get_account_historical_balances           official prior / same-day close
  3. get_orders fill-price                     ticket rows
  4. get_gainloss cost/proceeds                price market STCs orders left blank
  5. Rec /health session_realized_usd          cross-check only
  6. get_account_history                       last; empty is NOT FLAT $0
"""

from __future__ import annotations

from datetime import datetime, timedelta

SOURCE_RANK = (
    "live_equity",
    "hist_balance",
    "orders_fill_price",
    "gainloss_proceeds",
    "rec_health_crosscheck",
    "account_history",
)

WIN_EPS = 0.005


def _ymd(s: str) -> datetime:
    return datetime.strptime(s[:10], "%Y-%m-%d")


def prior_session_close(
    hist: list[dict], session_date: str
) -> tuple[float | None, str | None]:
    """Official open = last posted hist strictly before session_date."""
    want = _ymd(session_date)
    best: tuple[datetime, float] | None = None
    for row in hist:
        d = _ymd(str(row["date"]))
        if d >= want:
            continue
        val = float(row["value"])
        if best is None or d > best[0]:
            best = (d, val)
    if best is None:
        return None, None
    return best[1], best[0].strftime("%Y-%m-%d")


def session_close(
    hist: list[dict],
    session_date: str,
    live_equity: float,
) -> tuple[float, str]:
    """Same-day posted hist if present, else live equity (fresher)."""
    want = session_date[:10]
    for row in hist:
        if str(row["date"])[:10] == want:
            return float(row["value"]), "hist"
    return float(live_equity), "live"


def session_net_usd(
    opening: float, closing: float, transfers: float = 0.0
) -> float:
    return round(closing - opening - transfers, 2)


def pnl_label(usd: float) -> str:
    if usd > WIN_EPS:
        return "WIN"
    if usd < -WIN_EPS:
        return "LOSS"
    return "FLAT"


def format_signed(usd: float) -> str:
    mag = abs(round(usd, 2))
    if abs(usd) >= 1000:
        body = f"${mag:,.2f}"
    else:
        body = f"${mag:.2f}"
    if usd > WIN_EPS:
        return f"+{body}"
    if usd < -WIN_EPS:
        return f"−{body}"
    return body


def avg_px_from_cash(cash: float, qty: int) -> float | None:
    if qty <= 0:
        return None
    return round(cash / (qty * 100.0), 4)


def backfill_close_px(
    *,
    order_price: float | None,
    qty: int,
    gainloss_proceeds: float | None = None,
) -> float | None:
    """Market STC with no get_orders price: use gainloss proceeds."""
    if order_price is not None:
        return float(order_price)
    if gainloss_proceeds is None:
        return None
    return avg_px_from_cash(float(gainloss_proceeds), int(qty))


def should_drop_unpriced_close(close_px: float | None) -> bool:
    """Drop a table row only after orders AND gainloss still have no close."""
    return close_px is None


def options_pnl_from_sources(
    *,
    history_option_sum: float | None,
    gainloss_session_sum: float | None,
    has_filled_option_orders: bool,
) -> tuple[float | None, str]:
    """Empty history is not FLAT $0 when the order tape has fills."""
    if history_option_sum is not None:
        return round(float(history_option_sum), 2), "history"
    if gainloss_session_sum is not None:
        return round(float(gainloss_session_sum), 2), "gainloss"
    if has_filled_option_orders:
        return None, "fills_not_posted"
    return 0.0, "none"


def reconcile_session(
    *,
    hist: list[dict],
    session_date: str,
    live_equity: float,
    transfers: float = 0.0,
    ticket_sum: float | None = None,
) -> dict:
    opening, open_date = prior_session_close(hist, session_date)
    if opening is None:
        raise ValueError("no prior hist close")
    closing, close_src = session_close(hist, session_date, live_equity)
    net = session_net_usd(opening, closing, transfers)
    out = {
        "opening": opening,
        "opening_date": open_date,
        "closing": closing,
        "close_source": close_src,
        "transfers": transfers,
        "session_net": net,
        "label": pnl_label(net),
        "ticket_sum": ticket_sum,
        "used_live_close": close_src == "live",
    }
    if ticket_sum is not None:
        out["ticket_gap"] = round(net - ticket_sum, 2)
    return out


def next_weekday(iso: str) -> str:
    d = _ymd(iso) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")

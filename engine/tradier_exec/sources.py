"""Most-updated Tradier sources for daily recap / net-ticket grid.

10/7 night: live equity was WIN +$302.48. Same-day get_orders used LIMIT
prices as fills, FIFO-matched every SPY ticket as one tape, and dropped the
unpriced market STC → LOSS −$284. Overnight get_account_history posted the
real fills (21 option trades, cash +$300.14, premium +$423). get_orders is
empty the next calendar day. Do not let a staler source win.

Rank (freshest / most accurate first):
  1. get_account_balances.totalEquity          live close / session net
  2. get_account_historical_balances           official prior / same-day close
  3. get_account_history option trade fills    posted fill px + cash (beats limits)
  4. get_orders same-calendar-day tape         times only; price is often LIMIT
  5. get_gainloss cost/proceeds                price market STCs if history empty
  6. Rec /health session_realized_usd          premium cross-check only
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

SOURCE_RANK = (
    "live_equity",
    "hist_balance",
    "account_history_fills",
    "orders_same_day_tape",
    "gainloss_proceeds",
    "rec_health_crosscheck",
)

# type=option is empty until overnight post; option fills live under type=trade.
HISTORY_OPTION_QUERY_TYPE = "trade"

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


def fill_px(
    *,
    history_px: float | None = None,
    order_px: float | None = None,
    gainloss_px: float | None = None,
) -> float | None:
    """Posted history fill beats same-day order LIMIT; gainloss last."""
    if history_px is not None:
        return float(history_px)
    if order_px is not None:
        return float(order_px)
    if gainloss_px is not None:
        return float(gainloss_px)
    return None


def backfill_close_px(
    *,
    order_price: float | None,
    qty: int,
    gainloss_proceeds: float | None = None,
    history_price: float | None = None,
) -> float | None:
    """Market STC with no get_orders price: history fill, else gainloss."""
    return fill_px(
        history_px=history_price,
        order_px=order_price,
        gainloss_px=avg_px_from_cash(float(gainloss_proceeds), int(qty))
        if gainloss_proceeds is not None
        else None,
    )


def should_drop_unpriced_close(close_px: float | None) -> bool:
    """Drop a table row only after history, orders, AND gainloss have no close."""
    return close_px is None


def orders_tape_covers_session(session_date: str, order_dates: list[str]) -> bool:
    """get_orders is the current calendar day's tape. Empty next day is not flat."""
    want = session_date[:10]
    return any(str(d)[:10] == want for d in order_dates)


def normalize_history_event(ev: dict) -> dict | None:
    """Keep option trade fills. Equity / ACH / empty rows drop out."""
    trade = ev.get("trade") if isinstance(ev.get("trade"), dict) else {}
    symbol = str(trade.get("symbol") or ev.get("symbol") or "")
    if len(symbol) < 15:
        return None
    qty = trade.get("quantity", ev.get("quantity"))
    px = trade.get("price", ev.get("price"))
    amount = ev.get("amount")
    if qty is None or px is None or amount is None:
        return None
    return {
        "symbol": symbol,
        "qty": float(qty),
        "price": float(px),
        "amount": float(amount),
        "description": str(trade.get("description") or ev.get("description") or ""),
        "commission": float(trade.get("commission") or 0.0),
    }


def session_option_cash(events: list[dict]) -> float | None:
    """Sum posted history amounts. None if no option fills (not $0)."""
    legs = [x for x in (normalize_history_event(e) for e in events) if x]
    if not legs:
        return None
    return round(sum(x["amount"] for x in legs), 2)


def leftover_occ_qty(events: list[dict]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for leg in (normalize_history_event(e) for e in events):
        if not leg:
            continue
        out[leg["symbol"]] += leg["qty"]
    return {k: round(v, 4) for k, v in out.items() if abs(v) > 1e-9}


def occ_round_trips(events: list[dict]) -> list[dict]:
    """FIFO per OCC symbol. Do not blend calls/puts or different strikes."""
    buys: dict[str, list[dict]] = defaultdict(list)
    sells: dict[str, list[dict]] = defaultdict(list)
    descs: dict[str, str] = {}
    for leg in (normalize_history_event(e) for e in events):
        if not leg:
            continue
        descs[leg["symbol"]] = leg["description"]
        if leg["qty"] > 0:
            buys[leg["symbol"]].append(leg)
        elif leg["qty"] < 0:
            sells[leg["symbol"]].append({**leg, "qty": abs(leg["qty"])})
    trips: list[dict] = []
    for sym in sorted(set(buys) | set(sells)):
        bq = list(buys.get(sym, []))
        sq = list(sells.get(sym, []))
        # Equal-qty legs first, remaining combined.
        bi = 0
        si = 0
        used_b: set[int] = set()
        used_s: set[int] = set()
        while bi < len(bq) and si < len(sq):
            if bi in used_b:
                bi += 1
                continue
            if si in used_s:
                si += 1
                continue
            if abs(bq[bi]["qty"] - sq[si]["qty"]) < 1e-9:
                q = int(round(bq[bi]["qty"]))
                cash = round(bq[bi]["amount"] + sq[si]["amount"], 2)
                trips.append(
                    {
                        "symbol": sym,
                        "description": descs.get(sym, ""),
                        "qty": q,
                        "open": bq[bi]["price"],
                        "close": sq[si]["price"],
                        "cash": cash,
                        "label": pnl_label(cash),
                    }
                )
                used_b.add(bi)
                used_s.add(si)
                bi += 1
                si += 1
                continue
            break
        rest_b = [b for i, b in enumerate(bq) if i not in used_b]
        rest_s = [s for i, s in enumerate(sq) if i not in used_s]
        if rest_b or rest_s:
            b_qty = sum(b["qty"] for b in rest_b)
            s_qty = sum(s["qty"] for s in rest_s)
            b_cash = sum(b["amount"] for b in rest_b)
            s_cash = sum(s["amount"] for s in rest_s)
            cash = round(b_cash + s_cash, 2)
            q = int(round(min(b_qty, s_qty) if b_qty and s_qty else b_qty or s_qty))
            open_px = (
                round(sum(b["price"] * b["qty"] for b in rest_b) / b_qty, 4)
                if b_qty
                else None
            )
            close_px = (
                round(sum(s["price"] * s["qty"] for s in rest_s) / s_qty, 4)
                if s_qty
                else None
            )
            trips.append(
                {
                    "symbol": sym,
                    "description": descs.get(sym, ""),
                    "qty": q,
                    "open": open_px,
                    "close": close_px,
                    "cash": cash,
                    "label": pnl_label(cash),
                    "leftover": round(b_qty - s_qty, 4),
                }
            )
    return trips


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

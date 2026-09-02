"""Translate target portfolio weights into concrete orders."""

from __future__ import annotations

from datetime import date
from typing import Mapping

from quant_harness.config import Fees
from quant_harness.paper.account import PaperAccount, PendingOrder


def reconcile(
    target_weights: Mapping[str, float],
    account: PaperAccount,
    closes: Mapping[str, float],
    day: date,
    fees: Fees,
) -> list[PendingOrder]:
    """Orders that move the account toward `target_weights`.

    Sells are full-position exits (a symbol is either in the target or out).
    Buys are sized to the target value, rounded down to round lots, and
    pre-checked against available cash. A symbol never gets both a buy and a
    sell in the same batch.
    """
    equity = account.equity
    orders: list[PendingOrder] = []

    # exits first, so their (future) proceeds free symbols from buy consideration
    for sym, pos in sorted(account.positions.items()):
        if pos.quantity > 0 and target_weights.get(sym, 0.0) <= 0.0:
            orders.append(PendingOrder(sym, "sell", pos.quantity, day, "strategy"))

    exiting = {o.symbol for o in orders}
    cash_left = account.cash
    for sym, weight in sorted(target_weights.items(), key=lambda kv: (-kv[1], kv[0])):
        if weight <= 0.0 or sym in exiting:
            continue
        close = closes.get(sym)
        if close is None or close <= 0:
            continue  # suspended today; strategy will retry once it trades
        pos = account.positions.get(sym)
        current_value = pos.market_value if pos is not None else 0.0
        delta = weight * equity - current_value
        est_price = close * (1 + fees.slippage_rate)
        affordable = min(delta, cash_left)
        qty = int(affordable / (est_price * (1 + fees.commission_rate))) // 100 * 100
        if qty < 100:
            continue
        orders.append(PendingOrder(sym, "buy", qty, day, "strategy"))
        cash_left -= est_price * qty * (1 + fees.commission_rate)
    return orders

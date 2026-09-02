"""Risk controls: stop-loss exits, position caps, drawdown halt."""

from __future__ import annotations

from typing import Mapping

from quant_harness.config import RiskConfig
from quant_harness.data.types import Bar
from quant_harness.paper.account import PaperAccount, PendingOrder


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg

    def drawdown_tripped(self, account: PaperAccount) -> bool:
        return account.equity < account.peak_equity * (1 - self.cfg.drawdown_halt)

    def halt_orders(self, account: PaperAccount, day) -> list[PendingOrder]:
        """Flatten everything — queued when the drawdown halt trips."""
        return [
            PendingOrder(sym, "sell", pos.quantity, day, "halt_flatten")
            for sym, pos in account.positions.items()
            if pos.quantity > 0
        ]

    def forced_exits(self, account: PaperAccount, day_bars: Mapping[str, Bar], day) -> list[PendingOrder]:
        """Stop-loss: close at or below avg cost × (1 − stop_loss) → full exit."""
        orders = []
        for sym, pos in account.positions.items():
            bar = day_bars.get(sym)
            if bar is None:
                continue  # suspended; reassessed when trading resumes
            if bar.close <= pos.avg_price * (1 - self.cfg.stop_loss):
                orders.append(PendingOrder(sym, "sell", pos.quantity, day, "stop_loss"))
        return orders

    def filter_orders(
        self,
        orders: list[PendingOrder],
        account: PaperAccount,
        day_closes: Mapping[str, float],
    ) -> list[PendingOrder]:
        """Trim buys so post-trade symbol weight and total exposure stay within caps.

        Sells pass through untouched. Buy quantities are rounded down to round
        lots; buys that cannot afford at least one lot within the caps are dropped.
        """
        equity = account.equity
        if equity <= 0:
            return [o for o in orders if o.side == "sell"]

        rate = account.fees.commission_rate
        slip = account.fees.slippage_rate
        exposure = sum(p.market_value for p in account.positions.values())
        symbol_values = {sym: p.market_value for sym, p in account.positions.items()}

        accepted: list[PendingOrder] = []
        for order in orders:
            if order.side == "sell":
                accepted.append(order)
                price = day_closes.get(order.symbol)
                if price is not None:
                    exposure -= price * order.quantity
                continue

            close = day_closes.get(order.symbol)
            if close is None or close <= 0:
                continue  # no price today; skip the buy
            est_price = close * (1 + slip)

            def cost(qty: int) -> float:
                return est_price * qty * (1 + rate)

            budget = min(
                self.cfg.max_symbol_weight * equity - symbol_values.get(order.symbol, 0.0),
                self.cfg.max_total_exposure * equity - exposure,
            )
            if budget <= 0:
                continue
            qty = min(order.quantity, int(budget // (est_price * 100)) * 100)
            while qty > 0 and cost(qty) > budget:
                qty -= 100
            if qty <= 0:
                continue
            accepted.append(PendingOrder(order.symbol, "buy", qty, order.queued_date, order.reason))
            exposure += cost(qty)
        return accepted

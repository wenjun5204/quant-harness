from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from quant_harness.data.types import Bar


@dataclass
class Order:
    side: str  # "buy" or "sell"
    quantity: int


@dataclass
class Trade:
    timestamp: datetime
    side: str
    quantity: int
    price: float
    commission: float


@dataclass
class Position:
    quantity: int = 0
    avg_price: float = 0.0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0


class SimBroker:
    """Simulated broker with cash, position, commission and slippage.

    Orders placed during bar ``t`` are queued and filled at the open of bar ``t+1``.
    """

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        commission_rate: float = 0.001,
        slippage_rate: float = 0.0005,
    ):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.position = Position()
        self.trades: list[Trade] = []
        self._pending: list[Order] = []
        self._last_price: float = 0.0

    def buy(self, quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._pending.append(Order("buy", quantity))

    def sell(self, quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._pending.append(Order("sell", quantity))

    def _fill_pending(self, bar: Bar) -> None:
        for order in self._pending:
            fill_price = bar.open
            if order.side == "buy":
                fill_price *= 1 + self.slippage_rate
            else:
                fill_price *= 1 - self.slippage_rate
            commission = fill_price * order.quantity * self.commission_rate

            if order.side == "buy":
                total_cost = fill_price * order.quantity + commission
                if total_cost > self.cash:
                    continue  # insufficient funds, skip
                new_qty = self.position.quantity + order.quantity
                self.position.avg_price = (
                    self.position.avg_price * self.position.quantity + fill_price * order.quantity
                ) / new_qty
                self.position.quantity = new_qty
                self.cash -= total_cost
            else:
                qty = min(order.quantity, self.position.quantity)
                if qty <= 0:
                    continue
                proceeds = fill_price * qty - commission
                self.position.quantity -= qty
                self.cash += proceeds
                if self.position.is_flat:
                    self.position.avg_price = 0.0

            self.trades.append(Trade(bar.timestamp, order.side, order.quantity, fill_price, commission))
        self._pending.clear()
        self._last_price = bar.close

    def mark_to_market(self, price: float) -> None:
        self._last_price = price

    @property
    def equity(self) -> float:
        return self.cash + self.position.quantity * self._last_price

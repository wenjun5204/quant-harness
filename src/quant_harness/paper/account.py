"""A-share paper trading account.

Multi-symbol, long-only, with A-share market rules: round lots (buys in
multiples of 100 shares), T+1 (shares bought today cannot be sold today),
main-board price limits, commission with a per-order minimum, and sell-only
stamp duty. Orders queued at day T's close fill at day T+1's open; every
rejection is recorded with a reason — nothing is dropped silently.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

from quant_harness.config import Fees
from quant_harness.data.types import Bar

MAIN_BOARD_LIMIT = 0.095  # main-board ±10% daily limit, with rounding tolerance


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    avg_price: float = 0.0
    last_price: float = 0.0
    last_buy_date: date | None = None

    @property
    def market_value(self) -> float:
        return self.quantity * self.last_price


@dataclass
class TradeRecord:
    date: date
    symbol: str
    side: str  # "buy" | "sell"
    quantity: int
    price: float
    commission: float
    stamp_duty: float
    realized_pnl: float  # sells only; 0.0 for buys
    reason: str  # "strategy" | "stop_loss" | "halt_flatten"


@dataclass
class PendingOrder:
    symbol: str
    side: str  # "buy" | "sell"
    quantity: int
    queued_date: date
    reason: str = "strategy"


@dataclass
class CancelledOrder:
    date: date
    symbol: str
    side: str
    quantity: int
    reason: str  # "suspended" | "price_limit_up" | "price_limit_down" |
    #            "insufficient_cash" | "no_position" | "t1_restriction"


class PaperAccount:
    """Persistent paper-trading account state."""

    def __init__(self, initial_cash: float, fees: Fees, price_limit_check: bool = True):
        self.initial_cash = float(initial_cash)
        self.fees = fees
        self.price_limit_check = price_limit_check
        self.cash = float(initial_cash)
        self.positions: dict[str, Position] = {}
        self.pending: list[PendingOrder] = []
        self.trades: list[TradeRecord] = []
        self.cancelled: list[CancelledOrder] = []
        self.equity_curve: list[tuple[str, float]] = []  # (iso date, equity)
        self.peak_equity = float(initial_cash)
        self.halted = False
        self.halt_reason: str | None = None
        self.last_processed_date: date | None = None

    # -- order intake ------------------------------------------------------

    def queue(self, symbol: str, side: str, quantity: int, day: date, reason: str = "strategy") -> None:
        if quantity <= 0:
            raise ValueError(f"order quantity must be positive, got {quantity}")
        if side not in ("buy", "sell"):
            raise ValueError(f"order side must be 'buy' or 'sell', got {side!r}")
        self.pending.append(PendingOrder(symbol, side, quantity, day, reason))

    # -- fills -------------------------------------------------------------

    def fill_pending(
        self,
        day: date,
        day_bars: Mapping[str, Bar],
        prev_closes: Mapping[str, float],
    ) -> list[TradeRecord]:
        """Fill queued orders at today's open. Returns the fills that executed."""
        fills: list[TradeRecord] = []
        for order in self.pending:
            trade = self._fill_one(order, day, day_bars, prev_closes)
            if trade is not None:
                fills.append(trade)
        self.pending = []
        return fills

    def _fill_one(
        self,
        order: PendingOrder,
        day: date,
        day_bars: Mapping[str, Bar],
        prev_closes: Mapping[str, float],
    ) -> TradeRecord | None:
        bar = day_bars.get(order.symbol)
        if bar is None:
            self._cancel(order, day, "suspended")
            return None

        if self.price_limit_check:
            prev_close = prev_closes.get(order.symbol)
            if prev_close:
                if order.side == "buy" and bar.open >= prev_close * (1 + MAIN_BOARD_LIMIT):
                    self._cancel(order, day, "price_limit_up")
                    return None
                if order.side == "sell" and bar.open <= prev_close * (1 - MAIN_BOARD_LIMIT):
                    self._cancel(order, day, "price_limit_down")
                    return None

        if order.side == "buy":
            return self._fill_buy(order, day, bar)
        return self._fill_sell(order, day, bar)

    def _fill_buy(self, order: PendingOrder, day: date, bar: Bar) -> TradeRecord | None:
        fill_price = bar.open * (1 + self.fees.slippage_rate)
        notional = fill_price * order.quantity
        commission = max(notional * self.fees.commission_rate, self.fees.commission_min)
        total_cost = notional + commission
        if total_cost > self.cash + 1e-9:
            self._cancel(order, day, "insufficient_cash")
            return None
        self.cash -= total_cost

        pos = self.positions.get(order.symbol)
        if pos is None:
            pos = Position(order.symbol)
            self.positions[order.symbol] = pos
        pos.avg_price = (pos.avg_price * pos.quantity + notional) / (pos.quantity + order.quantity)
        pos.quantity += order.quantity
        pos.last_price = bar.close
        pos.last_buy_date = day

        trade = TradeRecord(day, order.symbol, "buy", order.quantity, fill_price, commission, 0.0, 0.0, order.reason)
        self.trades.append(trade)
        return trade

    def _fill_sell(self, order: PendingOrder, day: date, bar: Bar) -> TradeRecord | None:
        pos = self.positions.get(order.symbol)
        if pos is None or pos.quantity <= 0:
            self._cancel(order, day, "no_position")
            return None
        # T+1: with next-open fills this cannot trigger (a buy filled at day
        # D's open is only ever sold by an order queued at D's close or later,
        # which fills at D+1's open) — kept as defense in depth.
        if pos.last_buy_date == day:
            self._cancel(order, day, "t1_restriction")
            return None

        qty = min(order.quantity, pos.quantity)  # odd-lot sell of a full position is allowed
        fill_price = bar.open * (1 - self.fees.slippage_rate)
        notional = fill_price * qty
        commission = max(notional * self.fees.commission_rate, self.fees.commission_min)
        stamp_duty = notional * self.fees.stamp_duty_rate
        realized_pnl = (fill_price - pos.avg_price) * qty - commission - stamp_duty

        self.cash += notional - commission - stamp_duty
        pos.quantity -= qty
        if pos.quantity == 0:
            pos.avg_price = 0.0
            pos.last_buy_date = None
            del self.positions[order.symbol]

        trade = TradeRecord(day, order.symbol, "sell", qty, fill_price, commission, stamp_duty, realized_pnl, order.reason)
        self.trades.append(trade)
        return trade

    def _cancel(self, order: PendingOrder, day: date, reason: str) -> None:
        self.cancelled.append(CancelledOrder(day, order.symbol, order.side, order.quantity, reason))

    # -- valuation ---------------------------------------------------------

    def mark_to_market(self, day_bars: Mapping[str, Bar]) -> None:
        """Update last prices to today's closes; suspended symbols keep their last known price."""
        for symbol, pos in self.positions.items():
            bar = day_bars.get(symbol)
            if bar is not None:
                pos.last_price = bar.close

    @property
    def equity(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    def record_equity(self, day: date) -> None:
        equity = self.equity
        self.equity_curve.append((day.isoformat(), equity))
        self.peak_equity = max(self.peak_equity, equity)

    @property
    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return self.equity / self.peak_equity - 1.0

    # -- persistence -------------------------------------------------------

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "positions": [
                {
                    "symbol": p.symbol,
                    "quantity": p.quantity,
                    "avg_price": p.avg_price,
                    "last_price": p.last_price,
                    "last_buy_date": p.last_buy_date.isoformat() if p.last_buy_date else None,
                }
                for p in self.positions.values()
            ],
            "pending": [
                {"symbol": o.symbol, "side": o.side, "quantity": o.quantity,
                 "queued_date": o.queued_date.isoformat(), "reason": o.reason}
                for o in self.pending
            ],
            "trades": [self._trade_to_dict(t) for t in self.trades],
            "cancelled": [
                {"date": c.date.isoformat(), "symbol": c.symbol, "side": c.side,
                 "quantity": c.quantity, "reason": c.reason}
                for c in self.cancelled
            ],
            "equity_curve": self.equity_curve,
            "peak_equity": self.peak_equity,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "last_processed_date": self.last_processed_date.isoformat() if self.last_processed_date else None,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    @staticmethod
    def _trade_to_dict(t: TradeRecord) -> dict:
        return {
            "date": t.date.isoformat(), "symbol": t.symbol, "side": t.side,
            "quantity": t.quantity, "price": t.price, "commission": t.commission,
            "stamp_duty": t.stamp_duty, "realized_pnl": t.realized_pnl, "reason": t.reason,
        }

    @classmethod
    def load(cls, path: str | Path, fees: Fees, price_limit_check: bool = True) -> PaperAccount:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        account = cls(payload["initial_cash"], fees, price_limit_check)
        account.cash = payload["cash"]
        for p in payload["positions"]:
            account.positions[p["symbol"]] = Position(
                symbol=p["symbol"], quantity=p["quantity"], avg_price=p["avg_price"],
                last_price=p["last_price"],
                last_buy_date=date.fromisoformat(p["last_buy_date"]) if p["last_buy_date"] else None,
            )
        account.pending = [
            PendingOrder(o["symbol"], o["side"], o["quantity"], date.fromisoformat(o["queued_date"]), o["reason"])
            for o in payload["pending"]
        ]
        account.trades = [
            TradeRecord(
                date.fromisoformat(t["date"]), t["symbol"], t["side"], t["quantity"],
                t["price"], t["commission"], t["stamp_duty"], t["realized_pnl"], t["reason"],
            )
            for t in payload["trades"]
        ]
        account.cancelled = [
            CancelledOrder(date.fromisoformat(c["date"]), c["symbol"], c["side"], c["quantity"], c["reason"])
            for c in payload["cancelled"]
        ]
        account.equity_curve = [(d, float(e)) for d, e in payload["equity_curve"]]
        account.peak_equity = payload["peak_equity"]
        account.halted = payload["halted"]
        account.halt_reason = payload["halt_reason"]
        last = payload["last_processed_date"]
        account.last_processed_date = date.fromisoformat(last) if last else None
        return account

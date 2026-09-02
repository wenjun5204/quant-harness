from __future__ import annotations

from dataclasses import dataclass, field

from quant_harness.data.types import Bar
from quant_harness.engine.broker import SimBroker, Trade
from quant_harness.engine.metrics import compute_metrics
from quant_harness.strategy.base import Strategy


@dataclass
class BacktestResult:
    equity_curve: list[tuple] = field(default_factory=list)  # (timestamp, equity)
    trades: list[Trade] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def run_backtest(
    bars: list[Bar],
    strategy: Strategy,
    initial_cash: float = 100_000.0,
    commission_rate: float = 0.001,
    slippage_rate: float = 0.0005,
) -> BacktestResult:
    broker = SimBroker(initial_cash, commission_rate, slippage_rate)
    strategy.on_start()

    equity_curve = []
    for bar in bars:
        broker._fill_pending(bar)
        strategy.on_bar(bar, broker)
        broker.mark_to_market(bar.close)
        equity_curve.append((bar.timestamp, broker.equity))

    strategy.on_finish()

    metrics = compute_metrics(equity_curve, broker.trades)
    return BacktestResult(equity_curve=equity_curve, trades=broker.trades, metrics=metrics)

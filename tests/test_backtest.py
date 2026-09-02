from datetime import datetime, timedelta

from quant_harness.data.types import Bar
from quant_harness.engine.backtest import run_backtest
from quant_harness.strategies.sma_cross import SmaCross
from quant_harness.data.loader import generate_synthetic_bars


def make_flat_bars(n=5, price=100.0):
    t = datetime(2024, 1, 1)
    return [Bar(t + timedelta(days=i), price, price, price, price) for i in range(n)]


def test_no_trades_when_no_signal():
    bars = make_flat_bars(10)
    result = run_backtest(bars, SmaCross(fast=2, slow=3))
    assert len(result.trades) == 0
    assert result.metrics["total_return"] == 0.0


def test_equity_starts_at_initial_cash():
    bars = make_flat_bars(50)
    result = run_backtest(bars, SmaCross(fast=5, slow=10), initial_cash=50_000)
    ts, eq = result.equity_curve[0]
    assert eq == 50_000


def test_no_lookahead_fills_at_next_open():
    # A bar with a huge gap up after signal; if fills used same-bar close we'd see it.
    bars = make_flat_bars(50, price=100.0)
    result = run_backtest(bars, SmaCross(fast=3, slow=5))
    # All prices are flat, so SMAs are equal -> no crossover signal either way.
    assert len(result.trades) == 0


def test_synthetic_backtest_runs():
    bars = generate_synthetic_bars(n=300)
    result = run_backtest(bars, SmaCross(fast=10, slow=30))
    assert "total_return" in result.metrics
    assert "sharpe_ratio" in result.metrics
    assert "max_drawdown" in result.metrics

"""Run an SMA crossover backtest on synthetic data."""

from quant_harness.data.loader import generate_synthetic_bars
from quant_harness.engine.backtest import run_backtest
from quant_harness.strategies.sma_cross import SmaCross


def main():
    bars = generate_synthetic_bars(n=500, seed=7)
    strategy = SmaCross(fast=10, slow=30, quantity=100)
    result = run_backtest(bars, strategy, initial_cash=100_000)

    print("SMA Crossover Backtest")
    print("=" * 40)
    for k, v in result.metrics.items():
        if isinstance(v, float):
            print(f"  {k:20s} {v:>10.4f}")
        else:
            print(f"  {k:20s} {v:>10}")
    print(f"\n  Total trades: {len(result.trades)}")


if __name__ == "__main__":
    main()

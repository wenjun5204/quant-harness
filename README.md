# quant-harness

A minimal event-driven quant backtesting harness in Python.

## Design

- **Data layer** (`quant_harness.data`): OHLCV bars, CSV loading, synthetic data generation.
- **Strategy** (`quant_harness.strategy`): subclass `Strategy`, implement `on_bar(bar, broker)`.
- **Engine** (`quant_harness.engine`): event-driven backtest loop with a simulated broker.
  Orders queued during bar `t` fill at the open of bar `t+1` to avoid look-ahead bias.
- **Metrics** (`quant_harness.engine.metrics`): total return, annualized return, Sharpe ratio,
  max drawdown, win rate, profit factor.

## Quick start

```bash
pip install -e ".[dev]"
pytest
python examples/run_sma_cross.py
```

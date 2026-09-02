# quant-harness

A minimal event-driven quant backtesting harness in Python, extended with an
**A-share daily paper-trading runner** that runs unattended via cron.

> **Disclaimer**: paper trading validates *process* — no lookahead, realistic
> costs, risk limits, honest reporting. It does not, and cannot, guarantee any
> return. The drawdown halt exists precisely because a profitable month is not
> something anyone can promise.

## Design

### Backtesting core (single symbol, in-memory)

- **Data layer** (`quant_harness.data`): OHLCV bars, CSV loading, synthetic data generation.
- **Strategy** (`quant_harness.strategy`): subclass `Strategy`, implement `on_bar(bar, broker)`.
- **Engine** (`quant_harness.engine`): event-driven backtest loop with a simulated broker.
  Orders queued during bar `t` fill at the open of bar `t+1` to avoid look-ahead bias.
- **Metrics** (`quant_harness.engine.metrics`): total return, annualized return, Sharpe ratio,
  max drawdown, win rate, profit factor.

### Daily paper trading (multi-symbol, persistent)

- **`paper/`**: A-share paper account — T+1, round lots, main-board price limits,
  commission (万2.5, min ¥5) + sell-only stamp duty (0.05%), slippage; every rejected
  order is recorded with a reason. State persists to `state/account.json`.
- **`paper/risk.py`**: per-symbol stop-loss (−8%), symbol weight cap (25%), total
  exposure cap (80%), drawdown circuit breaker (−10% from peak → flatten + halt
  until manual `--resume`).
- **`strategies/momentum_rotation.py`**: momentum rotation over the stock pool
  with a rank buffer (hysteresis), an absolute-momentum floor (hold cash when
  nothing in the pool is rising), and optional risk-adjusted ranking. Strategies
  are pure functions of history — no state to serialize.
- **`daily/sweep.py`**: parameter grid × time-window replays with an equal-weight
  buy-and-hold benchmark (`quant-harness sweep`), so parameter choices can be
  selected on some windows and honestly checked on others.
- **`daily/runner.py`**: the single code path. Per trading day D: fill yesterday's
  queued orders at D's open → mark to market at D's close → record equity → risk
  pass → strategy pass (only sees bars ≤ D) → queue orders for D+1. Date-driven
  and idempotent: missed runs are caught up automatically, re-runs are no-ops.
  `replay` runs the identical loop over historical dates as a walk-forward backtest.
- **`data/akshare_source.py`**: qfq daily bars via akshare, full-snapshot cache
  per symbol (never appended — qfq re-bases history).

## Quick start

```bash
pip install -e ".[dev,live]"
pytest                                   # offline, no network needed
quant-harness replay --start 2026-01-02 --end 2026-08-31 --refresh  # walk-forward backtest
quant-harness daily                      # today's paper-trading cycle (idempotent)
quant-harness status                     # account status
quant-harness report                     # latest daily report
quant-harness sweep --window 2025-01-01:2025-12-31:2025 \
  --window 2026-01-02:2026-06-30:2026H1 \
  --set strategy.momentum_window=20,60 \
  --set strategy.min_momentum=0.0 --benchmark      # parameter sweep + benchmark
```

Configuration lives in `config.toml` (stock pool, fees, risk limits, strategy
parameters). Reports are written to `reports/YYYY-MM-DD.md`.

## Unattended daily run (cron)

```cron
35 16 * * 1-5 ./.venv/bin/quant-harness daily >> logs/daily.log 2>&1
```

Runs twice (17:05 and 21:05) after data is published; a run whose data is not
yet out exits cleanly and the next run catches up the missed day with identical
semantics (publication skew across symbols is handled by a refetch pass). Exit
code 2 means the drawdown halt tripped — review `quant-harness status` and
resume with `quant-harness daily --resume` when ready.

## Honest performance note

The 2023–2026 sweep (selection on 2023–2025, out-of-sample 2026) found **no
momentum configuration that consistently beats equal-weight buy-and-hold** on
the default 8-stock pool. The shipped defaults (`momentum_window=60`,
`top_k=2`, `min_momentum=0.0`) were chosen for worst-case robustness — they
halved the worst-window loss versus buy-and-hold (−4.7% vs −9.7% in 2026H1)
at the cost of matching, not beating, it in good years. Treat this system as
infrastructure for strategy research with strict risk control, not as an edge.

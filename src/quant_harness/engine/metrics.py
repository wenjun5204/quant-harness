from __future__ import annotations

import math

import numpy as np


def compute_metrics(
    equity_curve: list[tuple],
    trades: list,
    periods_per_year: int = 252,
) -> dict:
    """Compute standard performance metrics from an equity curve and trade list."""
    if len(equity_curve) < 2:
        return {}

    equities = np.array([e for _, e in equity_curve])
    returns = np.diff(equities) / equities[:-1]

    total_return = (equities[-1] / equities[0]) - 1.0
    n_years = len(equities) / periods_per_year
    if n_years > 0:
        # Sign-preserving root: a plain ** (1 / n) yields a complex number
        # once the growth ratio goes negative.
        growth = float(equities[-1] / equities[0])
        annualized_return = math.copysign(abs(growth) ** (1 / n_years), growth) - 1.0
    else:
        annualized_return = 0.0

    std = np.std(returns, ddof=1) if len(returns) > 1 else 0.0
    mean_ret = np.mean(returns) if len(returns) > 0 else 0.0
    sharpe = (mean_ret * periods_per_year) / (std * math.sqrt(periods_per_year)) if std > 0 else 0.0

    peak = np.maximum.accumulate(equities)
    drawdown = (equities - peak) / peak
    max_drawdown = float(np.min(drawdown))

    # Trade-level metrics
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0

    # Pair trades: each sell closes a prior buy (long-only round trip).
    open_price = None
    open_qty = 0
    for t in trades:
        if t.side == "buy":
            open_price = t.price
            open_qty = t.quantity
        elif t.side == "sell" and open_price is not None:
            pnl = (t.price - open_price) * min(open_qty, t.quantity)
            if pnl > 0:
                wins += 1
                gross_profit += pnl
            elif pnl < 0:
                losses += 1
                gross_loss += abs(pnl)
            open_price = None
            open_qty = 0

    closed_trades = wins + losses
    win_rate = wins / closed_trades if closed_trades > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    return {
        "total_return": float(total_return),
        "annualized_return": float(annualized_return),
        "sharpe_ratio": float(sharpe),
        "max_drawdown": max_drawdown,
        "num_trades": len(trades),
        "closed_trades": closed_trades,
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor) if math.isfinite(profit_factor) else profit_factor,
    }

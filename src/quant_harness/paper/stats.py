"""Trade-level statistics from recorded per-trade realized P&L."""

from __future__ import annotations

from quant_harness.paper.account import TradeRecord


def trade_stats(trades: list[TradeRecord]) -> dict:
    """Win rate and profit factor over closed round trips (sell fills)."""
    sells = [t for t in trades if t.side == "sell"]
    wins = [t for t in sells if t.realized_pnl > 0]
    losses = [t for t in sells if t.realized_pnl < 0]
    gross_profit = sum(t.realized_pnl for t in wins)
    gross_loss = abs(sum(t.realized_pnl for t in losses))
    closed = len(wins) + len(losses)
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0
    return {
        "num_trades": len(trades),
        "closed_trades": closed,
        "win_rate": len(wins) / closed if closed > 0 else 0.0,
        "profit_factor": profit_factor,
    }

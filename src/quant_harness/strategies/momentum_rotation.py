"""Momentum rotation: hold the top-k symbols by trailing momentum.

Ranking uses `close / close[-1 - momentum_window] - 1`. Held symbols enjoy a
rank buffer (hysteresis): they are only dropped once they fall out of the top
`top_k + rank_buffer`, which suppresses churn around rank boundaries.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Mapping

from quant_harness.data.types import Bar
from quant_harness.strategy.portfolio import PortfolioStrategy, pool_momentum

if TYPE_CHECKING:
    from quant_harness.paper.account import PaperAccount


def _bars_upto(bars: list[Bar], as_of: date) -> int:
    """Count of bars with date <= as_of (bars are ascending by date)."""
    lo, hi = 0, len(bars)
    while lo < hi:
        mid = (lo + hi) // 2
        if bars[mid].timestamp.date() <= as_of:
            lo = mid + 1
        else:
            hi = mid
    return lo


class MomentumRotation(PortfolioStrategy):
    def __init__(self, momentum_window: int = 20, top_k: int = 4, rank_buffer: int = 2,
                 min_history: int = 60, min_momentum: float | None = None,
                 risk_adjusted: bool = False, market_filter_window: int = 0):
        if momentum_window < 1 or top_k < 1 or rank_buffer < 0 or min_history < 1:
            raise ValueError("momentum_window, top_k, min_history must be >= 1 and rank_buffer >= 0")
        self.momentum_window = momentum_window
        self.top_k = top_k
        self.rank_buffer = rank_buffer
        self.min_history = min_history
        self.min_momentum = min_momentum
        self.risk_adjusted = risk_adjusted
        self.market_filter_window = market_filter_window

    def score(self, tail: list[Bar]) -> tuple[float, float]:
        """(raw momentum, ranking score). Score is Sharpe-like when risk_adjusted."""
        closes = [b.close for b in tail[-(self.momentum_window + 1):]]
        momentum = closes[-1] / closes[0] - 1.0
        if not self.risk_adjusted:
            return momentum, momentum
        rets = [closes[i + 1] / closes[i] - 1.0 for i in range(len(closes) - 1)]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
        vol = var ** 0.5
        return momentum, (mean / vol) if vol > 0 else float("-inf")

    def rank(self, history: Mapping[str, list[Bar]], as_of: date) -> list[tuple[str, float]]:
        """Symbols with enough history, best ranking score first. The momentum
        value returned alongside is the raw (unadjusted) return."""
    def _rank_with_raw(self, history: Mapping[str, list[Bar]], as_of: date) -> list[tuple[str, float, float]]:
        """(symbol, ranking score, raw momentum), best score first."""
        out: list[tuple[str, float, float]] = []
        need = max(self.min_history, self.momentum_window + 1)
        for sym, bars in history.items():
            n = _bars_upto(bars, as_of)
            if n < need:
                continue
            tail = bars[n - need : n]
            momentum, score = self.score(tail)
            out.append((sym, score, momentum))
        out.sort(key=lambda t: (-t[1], t[0]))
        return out

    def rank(self, history: Mapping[str, list[Bar]], as_of: date) -> list[tuple[str, float]]:
        """Public ranking for reports: (symbol, raw momentum), best score first."""
        return [(s, m) for s, _, m in self._rank_with_raw(history, as_of)]

    def market_momentum(self, history: Mapping[str, list[Bar]], as_of: date) -> float | None:
        """Equal-weight pool momentum over `market_filter_window` bars; None if not ready."""
        return pool_momentum(history, as_of, self.market_filter_window)

    def target_weights(
        self,
        history: Mapping[str, list[Bar]],
        account: PaperAccount,
        as_of: date,
    ) -> dict[str, float]:
        if self.market_filter_window > 0:
            market = self.market_momentum(history, as_of)
            if market is not None and market <= 0:
                return {}  # pool trend is down: all cash (hysteresis overridden)
        raw_ranked = self._rank_with_raw(history, as_of)

        def passes_floor(momentum: float) -> bool:
            return self.min_momentum is None or momentum > self.min_momentum

        eligible = [(s, m) for s, _, m in raw_ranked if passes_floor(m)]
        if not eligible:
            return {}  # warmup, or nothing clears the absolute-momentum floor
        top = [sym for sym, _ in eligible[: self.top_k]]
        rank_of = {sym: i for i, (sym, _, _) in enumerate(raw_ranked)}
        mom_of = {sym: m for sym, _, m in raw_ranked}
        held = [sym for sym, pos in account.positions.items() if pos.quantity > 0]

        # hysteresis: keep held symbols until they fall out of the buffer zone
        # AND clear the momentum floor; held symbols with no rankable data
        # (long suspension) are kept too
        survivors = [
            s for s in held
            if s in rank_of
            and rank_of[s] < self.top_k + self.rank_buffer
            and passes_floor(mom_of[s])
        ]
        unranked_held = [s for s in held if s not in rank_of]

        weight = 1.0 / self.top_k
        # top_k caps *entries*; held survivors are retained unconditionally —
        # the rank buffer delays exits, it does not admit new names
        weights = {sym: weight for sym in top}
        for sym in survivors:
            weights.setdefault(sym, weight)
        for sym in unranked_held:  # always kept while unrankable — a missing
            weights.setdefault(sym, weight)  # weight would liquidate it
        return weights

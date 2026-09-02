"""Equal-weight buy-and-hold benchmark.

Enters every pool symbol at equal weight on the first day and never exits —
the yardstick any active strategy must beat.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Mapping

from quant_harness.data.types import Bar
from quant_harness.strategy.portfolio import PortfolioStrategy, pool_momentum

if TYPE_CHECKING:
    from quant_harness.paper.account import PaperAccount


class BuyAndHold(PortfolioStrategy):
    """Equal-weight buy-and-hold, optionally gated by a pool trend filter.

    With `market_filter_window > 0` this becomes the classic trend-following
    benchmark: hold the basket while its equal-weight trend is positive, go
    all-cash when it turns negative (Gary Antonacci's GTAA / Faber's timing
    rule applied to the pool itself).
    """

    def __init__(self, symbols: list[str], market_filter_window: int = 0):
        if not symbols:
            raise ValueError("symbols must be non-empty")
        self.symbols = list(symbols)
        self.market_filter_window = market_filter_window

    def target_weights(
        self,
        history: Mapping[str, list[Bar]],
        account: PaperAccount,
        as_of: date,
    ) -> dict[str, float]:
        if self.market_filter_window > 0:
            market = pool_momentum(history, as_of, self.market_filter_window)
            if market is not None and market <= 0:
                return {}
        return {sym: 1.0 / len(self.symbols) for sym in self.symbols}

"""Equal-weight buy-and-hold benchmark.

Enters every pool symbol at equal weight on the first day and never exits —
the yardstick any active strategy must beat.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Mapping

from quant_harness.data.types import Bar
from quant_harness.strategy.portfolio import PortfolioStrategy

if TYPE_CHECKING:
    from quant_harness.paper.account import PaperAccount


class BuyAndHold(PortfolioStrategy):
    def __init__(self, symbols: list[str]):
        if not symbols:
            raise ValueError("symbols must be non-empty")
        self.symbols = list(symbols)

    def target_weights(
        self,
        history: Mapping[str, list[Bar]],
        account: PaperAccount,
        as_of: date,
    ) -> dict[str, float]:
        return {sym: 1.0 / len(self.symbols) for sym in self.symbols}

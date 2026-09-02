"""Portfolio-level strategy interface.

Unlike the bar-by-bar `Strategy`, a `PortfolioStrategy` is a pure function of
(sliced) history plus current account state — there is no internal state to
serialize between daily runs. The runner recomputes targets from cached
history every day.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING, Mapping

from quant_harness.data.types import Bar

if TYPE_CHECKING:
    from quant_harness.paper.account import PaperAccount


def pool_momentum(history: Mapping[str, list[Bar]], as_of: date, window: int) -> float | None:
    """Equal-weight average momentum over `window` bars; None when no symbol has
    enough history. Used by market-regime filters."""
    if window <= 0:
        return None
    momenta = []
    for bars in history.values():
        if len(bars) < window + 1:
            continue
        # bars are ascending; walk back to `as_of` first
        lo, hi = 0, len(bars)
        while lo < hi:
            mid = (lo + hi) // 2
            if bars[mid].timestamp.date() <= as_of:
                lo = mid + 1
            else:
                hi = mid
        if lo < window + 1:
            continue
        tail = bars[lo - (window + 1) : lo]
        momenta.append(tail[-1].close / tail[0].close - 1.0)
    if not momenta:
        return None
    return sum(momenta) / len(momenta)


class PortfolioStrategy(ABC):
    @abstractmethod
    def target_weights(
        self,
        history: Mapping[str, list[Bar]],
        account: PaperAccount,
        as_of: date,
    ) -> dict[str, float]:
        """Target portfolio weights as of `as_of`, from bars at or before it."""

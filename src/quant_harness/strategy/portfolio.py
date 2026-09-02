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


def series_momentum(bars: list[Bar], as_of: date, window: int) -> float | None:
    """Momentum of one series over `window` bars; None when not enough history."""
    if window <= 0:
        return None
    n = _bars_upto(bars, as_of)
    if n < window + 1:
        return None
    tail = bars[n - (window + 1) : n]
    return tail[-1].close / tail[0].close - 1.0


def pool_momentum(history: Mapping[str, list[Bar]], as_of: date, window: int) -> float | None:
    """Equal-weight average momentum over `window` bars; None when no symbol has
    enough history. Used by market-regime filters."""
    if window <= 0:
        return None
    momenta = [m for m in (series_momentum(bars, as_of, window) for bars in history.values()) if m is not None]
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
        market_history: list[Bar] | None = None,
    ) -> dict[str, float]:
        """Target portfolio weights as of `as_of`, from bars at or before it.

        `market_history` carries the market index bars (when configured) for
        regime filters; None means no index data is available."""

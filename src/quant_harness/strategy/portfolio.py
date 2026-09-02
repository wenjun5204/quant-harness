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


class PortfolioStrategy(ABC):
    @abstractmethod
    def target_weights(
        self,
        history: Mapping[str, list[Bar]],
        account: PaperAccount,
        as_of: date,
    ) -> dict[str, float]:
        """Target portfolio weights as of `as_of`, from bars at or before it."""

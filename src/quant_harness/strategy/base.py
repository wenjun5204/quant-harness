from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from quant_harness.data.types import Bar

if TYPE_CHECKING:
    from quant_harness.engine.broker import SimBroker


class Strategy(ABC):
    """Base class for trading strategies.

    Subclass and implement ``on_bar``. Use the broker to place orders;
    queued orders fill at the next bar's open.
    """

    def on_start(self) -> None:
        """Called once before the backtest begins."""

    def on_finish(self) -> None:
        """Called once after the backtest ends."""

    @abstractmethod
    def on_bar(self, bar: Bar, broker: "SimBroker") -> None:
        """Called for each bar. Place orders via the broker."""

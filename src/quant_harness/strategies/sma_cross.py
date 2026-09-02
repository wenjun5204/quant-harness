from collections import deque

from quant_harness.strategy.base import Strategy


class SmaCross(Strategy):
    """Long when fast SMA crosses above slow SMA, exit when it crosses below."""

    def __init__(self, fast: int = 10, slow: int = 30, quantity: int = 100):
        if fast >= slow:
            raise ValueError("fast must be less than slow")
        self.fast = fast
        self.slow = slow
        self.quantity = quantity
        self._closes: deque[float] = deque(maxlen=slow)
        self._in_position = False

    def on_bar(self, bar, broker) -> None:
        self._closes.append(bar.close)
        if len(self._closes) < self.slow:
            return

        fast_sma = sum(list(self._closes)[-self.fast:]) / self.fast
        slow_sma = sum(self._closes) / self.slow

        if fast_sma > slow_sma and not self._in_position:
            broker.buy(self.quantity)
            self._in_position = True
        elif fast_sma < slow_sma and self._in_position:
            broker.sell(self.quantity)
            self._in_position = False

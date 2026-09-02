"""Trading-calendar helpers derived from bar history itself.

The reference symbol's own bar series doubles as the trading calendar, so no
separate calendar API is needed. `slice_history` is the single chokepoint
through which strategies ever see history — everything else may hold the full
series.
"""

from __future__ import annotations

from datetime import date
from typing import Mapping

from quant_harness.data.types import Bar


def trading_days(reference_bars: list[Bar], start: date, end: date) -> list[date]:
    """Trading dates in [start, end] inclusive, per the reference symbol's bars."""
    return [b.timestamp.date() for b in reference_bars if start <= b.timestamp.date() <= end]


def slice_history(history: Mapping[str, list[Bar]], as_of: date) -> dict[str, list[Bar]]:
    """History clipped to bars at or before `as_of` — the no-lookahead boundary."""
    return {
        symbol: [b for b in bars if b.timestamp.date() <= as_of]
        for symbol, bars in history.items()
    }

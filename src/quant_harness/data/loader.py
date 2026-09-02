import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

from quant_harness.data.types import Bar


def load_bars_from_csv(path: str | Path) -> list[Bar]:
    """Load bars from a CSV with columns: timestamp,open,high,low,close,volume."""
    bars = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            bars.append(
                Bar(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0)),
                )
            )
    return bars


def generate_synthetic_bars(
    n: int = 500,
    start_price: float = 100.0,
    start_time: datetime | None = None,
    interval: timedelta = timedelta(days=1),
    seed: int = 42,
) -> list[Bar]:
    """Generate a random-walk price series as OHLC bars."""
    rng = random.Random(seed)
    t = start_time or datetime(2024, 1, 1)
    bars = []
    price = start_price
    for _ in range(n):
        drift = rng.gauss(0.0003, 0.015)
        open_price = price
        close_price = max(open_price * (1 + drift), 0.01)
        high = max(open_price, close_price) * (1 + abs(rng.gauss(0, 0.004)))
        low = min(open_price, close_price) * (1 - abs(rng.gauss(0, 0.004)))
        bars.append(Bar(t, open_price, high, low, close_price, rng.randint(1000, 10000)))
        price = close_price
        t += interval
    return bars

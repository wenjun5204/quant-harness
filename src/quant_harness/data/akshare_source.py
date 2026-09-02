"""Daily bar data from akshare, with a per-symbol snapshot cache.

Primary source is eastmoney (`stock_zh_a_hist`); if it rejects connections
(it rate-limits IPs aggressively), the source falls back to Sina
(`stock_zh_a_daily`) for the rest of the process. Both provide qfq
(forward-adjusted) prices; their adjustment bases can differ slightly, but
each refresh rewrites the whole cache so a snapshot is always internally
consistent.

qfq prices are retroactively re-based whenever a corporate action occurs, so
the cache is rewritten in full on every refresh — never appended to
(appending would stitch inconsistent adjustment bases together).
"""

from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path

from quant_harness.data.loader import load_bars_from_csv
from quant_harness.data.types import Bar


class AkshareDataSource:
    def __init__(self, cache_dir: str | Path, retries: int = 3, retry_sleep_s: int = 300):
        self.cache_dir = Path(cache_dir)
        self.retries = retries
        self.retry_sleep_s = retry_sleep_s
        self._eastmoney_disabled = False  # flipped when eastmoney refuses us

    def refresh(self, symbol: str, end_date: date, start_date: date | None = None) -> list[Bar]:
        """Full fetch of qfq daily bars through `end_date`; rewrites the cache snapshot."""
        start = start_date or date(end_date.year - 4, end_date.month, end_date.day)

        bars: list[Bar] | None = None
        if not self._eastmoney_disabled:
            try:  # one attempt only: a refused connection means we are rate-limited
                bars = self._eastmoney_bars(symbol, start, end_date)
            except Exception as e:  # noqa: BLE001
                print(f"warning: eastmoney unavailable ({e}); switching to Sina for this run")
                self._eastmoney_disabled = True

        if bars is None:
            bars = self._with_retry(self._sina_bars, symbol, start, end_date)
        self._write_cache(symbol, bars)
        return bars

    def _with_retry(self, fetch, symbol: str, start: date, end_date: date) -> list[Bar]:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                return fetch(symbol, start, end_date)
            except Exception as e:  # noqa: BLE001 - network/parse errors all warrant a retry
                last_error = e
                if attempt < self.retries - 1 and self.retry_sleep_s > 0:
                    time.sleep(self.retry_sleep_s)
        raise RuntimeError(f"fetch of {symbol} failed after {self.retries} attempts: {last_error}") from last_error

    def _eastmoney_bars(self, symbol: str, start: date, end_date: date) -> list[Bar]:
        import akshare as ak  # lazy: the core package and tests never need it

        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="qfq",
        )
        bars: list[Bar] = []
        for _, row in df.iterrows():
            ts = row["日期"]
            if isinstance(ts, str):
                timestamp = datetime.strptime(ts, "%Y-%m-%d")
            else:
                timestamp = datetime(ts.year, ts.month, ts.day)
            bars.append(
                Bar(
                    timestamp,
                    float(row["开盘"]),
                    float(row["最高"]),
                    float(row["最低"]),
                    float(row["收盘"]),
                    float(row["成交量"]),
                )
            )
        return bars

    @staticmethod
    def _sina_symbol(symbol: str) -> str:
        """akshare's Sina interface wants an exchange prefix: sh600030 / sz000858."""
        if symbol.startswith(("6",)):
            return f"sh{symbol}"
        if symbol.startswith(("0", "3")):
            return f"sz{symbol}"
        raise ValueError(f"cannot map symbol {symbol!r} to a Sina exchange prefix")

    def _sina_bars(self, symbol: str, start: date, end_date: date) -> list[Bar]:
        import akshare as ak  # lazy: the core package and tests never need it

        df = ak.stock_zh_a_daily(
            symbol=self._sina_symbol(symbol),
            start_date=start.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="qfq",
        )
        bars: list[Bar] = []
        for _, row in df.iterrows():
            ts = row["date"]
            if isinstance(ts, str):
                timestamp = datetime.strptime(ts, "%Y-%m-%d")
            else:
                timestamp = datetime(ts.year, ts.month, ts.day)
            bars.append(
                Bar(
                    timestamp,
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row["volume"]),
                )
            )
        return bars

    def _write_cache(self, symbol: str, bars: list[Bar]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{symbol}.csv"
        tmp = path.with_suffix(".csv.tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            f.write("timestamp,open,high,low,close,volume\n")
            for b in bars:
                f.write(f"{b.timestamp.date().isoformat()},{b.open},{b.high},{b.low},{b.close},{b.volume}\n")
        tmp.replace(path)

    def load_cached(self, symbol: str) -> list[Bar]:
        path = self.cache_dir / f"{symbol}.csv"
        if not path.exists():
            return []
        return load_bars_from_csv(path)

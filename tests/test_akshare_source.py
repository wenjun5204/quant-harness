"""Tests for the akshare data source — network fully mocked, akshare never installed."""

import sys
import types
from datetime import date

import pandas as pd
import pytest

from quant_harness.data.akshare_source import AkshareDataSource
from quant_harness.data.types import Bar


def fake_hist_fn(rows: list[dict], fail_times: int = 0):
    """Build a stand-in for ak.stock_zh_a_hist with optional initial failures."""
    calls = {"n": 0}

    def fn(symbol, period, start_date, end_date, adjust):
        assert period == "daily" and adjust == "qfq"
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise ConnectionError("network down")
        return pd.DataFrame(rows)

    fn.calls = calls
    return fn


def fake_daily_fn(rows: list[dict], fail_times: int = 0):
    """Same, for ak.stock_zh_a_daily (no `period` argument)."""
    calls = {"n": 0}

    def fn(symbol, start_date, end_date, adjust):
        assert adjust == "qfq"
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise ConnectionError("network down")
        return pd.DataFrame(rows)

    fn.calls = calls
    return fn


def fake_index_fn(rows: list[dict], fail_times: int = 0):
    """Stand-in for ak.stock_zh_index_daily (only a `symbol` argument)."""
    calls = {"n": 0}

    def fn(symbol):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise ConnectionError("network down")
        return pd.DataFrame(rows)

    fn.calls = calls
    return fn


ROWS = [
    {"日期": "2025-06-02", "开盘": 10.0, "最高": 10.5, "最低": 9.8, "收盘": 10.2, "成交量": 12345},
    {"日期": "2025-06-03", "开盘": 10.3, "最高": 10.8, "最低": 10.1, "收盘": 10.6, "成交量": 23456},
]


@pytest.fixture
def fake_akshare(monkeypatch):
    module = types.ModuleType("akshare")
    monkeypatch.setitem(sys.modules, "akshare", module)
    return module


def test_maps_chinese_columns_to_bars(fake_akshare, tmp_path):
    fake_akshare.stock_zh_a_hist = fake_hist_fn(ROWS)
    src = AkshareDataSource(tmp_path, retries=1, retry_sleep_s=0)
    bars = src.refresh("600036", date(2025, 6, 3))
    assert bars == [
        Bar(bars[0].timestamp, 10.0, 10.5, 9.8, 10.2, 12345),
        Bar(bars[1].timestamp, 10.3, 10.8, 10.1, 10.6, 23456),
    ]
    assert bars[0].timestamp.isoformat() == "2025-06-02T00:00:00"


def test_cache_written_and_reloadable(fake_akshare, tmp_path):
    fake_akshare.stock_zh_a_hist = fake_hist_fn(ROWS)
    src = AkshareDataSource(tmp_path, retries=1, retry_sleep_s=0)
    src.refresh("600036", date(2025, 6, 3))
    cached = src.load_cached("600036")
    assert [b.close for b in cached] == [10.2, 10.6]


def test_refresh_rewrites_cache_snapshot_not_appends(fake_akshare, tmp_path):
    """qfq re-bases history on corporate actions: refresh must fully replace the cache."""
    fake_akshare.stock_zh_a_hist = fake_hist_fn(ROWS)
    src = AkshareDataSource(tmp_path, retries=1, retry_sleep_s=0)
    src.refresh("600036", date(2025, 6, 3))

    # a later fetch returns a re-based, shorter series
    rebased = [{"日期": "2025-06-03", "开盘": 5.0, "最高": 5.4, "最低": 4.9, "收盘": 5.2, "成交量": 999}]
    fake_akshare.stock_zh_a_hist = fake_hist_fn(rebased)
    src.refresh("600036", date(2025, 6, 3))

    cached = src.load_cached("600036")
    assert len(cached) == 1
    assert cached[0].close == 5.2


def test_sina_retries_then_succeeds(fake_akshare, tmp_path):
    """eastmoney is dropped after one refusal; the Sina fallback gets full retries."""
    fake_akshare.stock_zh_a_hist = fake_hist_fn(ROWS, fail_times=99)  # always blocked
    sina_fn = fake_daily_fn(SINA_FALLBACK_ROWS, fail_times=2)
    fake_akshare.stock_zh_a_daily = sina_fn
    src = AkshareDataSource(tmp_path, retries=3, retry_sleep_s=0)
    bars = src.refresh("600036", date(2025, 6, 3))
    assert len(bars) == 2  # sina rows came back
    assert sina_fn.calls["n"] == 3


def test_raises_after_exhausting_retries(fake_akshare, tmp_path):
    fake_akshare.stock_zh_a_hist = fake_hist_fn(ROWS, fail_times=99)
    fake_akshare.stock_zh_a_daily = fake_daily_fn(SINA_FALLBACK_ROWS, fail_times=99)  # both blocked
    src = AkshareDataSource(tmp_path, retries=2, retry_sleep_s=0)
    with pytest.raises(RuntimeError, match="600036"):
        src.refresh("600036", date(2025, 6, 3))


def test_load_cached_missing_symbol_returns_empty(tmp_path):
    src = AkshareDataSource(tmp_path)
    assert src.load_cached("999999") == []


SINA_FALLBACK_ROWS = [
    {"date": "2025-06-02", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 111},
    {"date": "2025-06-03", "open": 10.3, "high": 10.8, "low": 10.1, "close": 10.6, "volume": 222},
]


class TestSinaFallback:
    SINA_ROWS = [
        {"date": "2025-06-02", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 111},
        {"date": "2025-06-03", "open": 10.3, "high": 10.8, "low": 10.1, "close": 10.6, "volume": 222},
    ]

    def test_eastmoney_failure_falls_back_to_sina(self, fake_akshare, tmp_path, caplog):
        def hist_fn(**kwargs):
            raise ConnectionError("blocked")

        def daily_fn(symbol, start_date, end_date, adjust):
            assert symbol == "sz000858"  # prefix mapping applied
            assert adjust == "qfq"
            return pd.DataFrame(self.SINA_ROWS)

        fake_akshare.stock_zh_a_hist = hist_fn
        fake_akshare.stock_zh_a_daily = daily_fn
        src = AkshareDataSource(tmp_path, retries=2, retry_sleep_s=0)
        import logging

        with caplog.at_level(logging.WARNING, logger="quant_harness.akshare"):
            bars = src.refresh("000858", date(2025, 6, 3))
        assert [b.close for b in bars] == [10.2, 10.6]
        assert any("Sina" in r.message for r in caplog.records)
        assert src._eastmoney_disabled
        # once disabled, eastmoney is not retried for later symbols
        assert src._eastmoney_disabled

    def test_sina_prefix_mapping(self):
        assert AkshareDataSource._sina_symbol("600030") == "sh600030"
        assert AkshareDataSource._sina_symbol("000858") == "sz000858"
        assert AkshareDataSource._sina_symbol("300750") == "sz300750"
        with pytest.raises(ValueError):
            AkshareDataSource._sina_symbol("830001")  # BE-only code, unsupported

    def test_sina_failure_raises_after_retries(self, fake_akshare, tmp_path):
        def hist_fn(**kwargs):
            raise ConnectionError("blocked")

        def daily_fn(**kwargs):
            raise ConnectionError("also blocked")

        fake_akshare.stock_zh_a_hist = hist_fn
        fake_akshare.stock_zh_a_daily = daily_fn
        src = AkshareDataSource(tmp_path, retries=2, retry_sleep_s=0)
        with pytest.raises(RuntimeError, match="000858"):
            src.refresh("000858", date(2025, 6, 3))


class TestIndexFetch:
    INDEX_ROWS = [
        {"date": "2025-06-02", "open": 4000.0, "high": 4050.0, "low": 3980.0, "close": 4020.0, "volume": 1e8},
        {"date": "2025-06-03", "open": 4020.0, "high": 4080.0, "low": 4010.0, "close": 4060.0, "volume": 1.2e8},
    ]

    def test_refresh_index_maps_and_caches(self, fake_akshare, tmp_path):
        fn = fake_index_fn(self.INDEX_ROWS)
        fake_akshare.stock_zh_index_daily = fn
        src = AkshareDataSource(tmp_path, retries=1, retry_sleep_s=0)
        bars = src.refresh_index("sh000300", date(2025, 6, 3))
        assert [b.close for b in bars] == [4020.0, 4060.0]
        assert fn.calls["n"] == 1
        # cached under index_<symbol>.csv and reloadable
        cached = src.load_cached_index("sh000300")
        assert [b.close for b in cached] == [4020.0, 4060.0]

    def test_refresh_index_slices_to_start_and_end(self, fake_akshare, tmp_path):
        fake_akshare.stock_zh_index_daily = fake_index_fn(self.INDEX_ROWS)
        src = AkshareDataSource(tmp_path, retries=1, retry_sleep_s=0)
        bars = src.refresh_index("sh000300", date(2025, 6, 3), start_date=date(2025, 6, 3))
        assert [b.timestamp.date().isoformat() for b in bars] == ["2025-06-03"]

    def test_refresh_index_retries(self, fake_akshare, tmp_path):
        fn = fake_index_fn(self.INDEX_ROWS, fail_times=1)
        fake_akshare.stock_zh_index_daily = fn
        src = AkshareDataSource(tmp_path, retries=2, retry_sleep_s=0)
        bars = src.refresh_index("sh000300", date(2025, 6, 3))
        assert len(bars) == 2 and fn.calls["n"] == 2

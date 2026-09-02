"""Offline sweep tests on a synthetic cache."""

import csv
from datetime import date, timedelta

import pytest

from quant_harness.config import Config, Fees, RiskConfig, StrategyConfig
from quant_harness.daily.sweep import override_config, parse_value, run_sweep
from quant_harness.data.types import Bar
from datetime import datetime


def weekdays(start, n):
    out, cur = [], start
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


@pytest.fixture
def cfg(tmp_path):
    days = weekdays(date(2025, 1, 6), 90)
    cache = tmp_path / "cache"
    cache.mkdir()
    for sym, rate in [("AAA", 0.004), ("REF", 0.0), ("BBB", -0.003)]:
        c = 10.0
        with open(cache / f"{sym}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for day in days:
                c *= 1 + rate
                w.writerow([day.isoformat(), c * 0.995, c * 1.01, c * 0.99, c, 1000])
    return Config(
        initial_cash=100_000.0,
        symbols=["AAA", "BBB", "REF"],
        reference_symbol="REF",
        fees=Fees(),
        risk=RiskConfig(),
        strategy=StrategyConfig(momentum_window=10, top_k=1, rank_buffer=1, min_history=60),
        state_dir=tmp_path / "state",
        cache_dir=cache,
        reports_dir=tmp_path / "reports",
        fetch_retries=1,
        fetch_retry_sleep_s=0,
    )


def test_parse_value():
    assert parse_value("10") == 10
    assert parse_value("0.5") == 0.5
    assert parse_value("true") is True
    assert parse_value("abc") == "abc"


def test_override_config_nested_and_top_level(cfg):
    v = override_config(cfg, "strategy.top_k", 2)
    assert v.strategy.top_k == 2 and cfg.strategy.top_k == 1  # original untouched
    v = override_config(cfg, "risk.stop_loss", 0.05)
    assert v.risk.stop_loss == 0.05
    v = override_config(cfg, "initial_cash", 200_000.0)
    assert v.initial_cash == 200_000.0
    with pytest.raises(ValueError):
        override_config(cfg, "strategy.nonsense", 1)
    with pytest.raises(ValueError):
        override_config(cfg, "nope.x", 1)


def test_run_sweep_grid_and_benchmark(cfg):
    windows = [(date(2025, 3, 3), date(2025, 5, 30), "test")]  # after 60-bar warmup
    grids = [("strategy.min_momentum", [-9.9, 0.0])]
    out = run_sweep(cfg, windows, grids, benchmark=True)

    # one section per benchmark + combo, and a summary matrix
    assert "== buy_hold_equal ==" in out
    assert "strategy.min_momentum=-9.9" in out
    assert "strategy.min_momentum=0.0" in out
    assert "total return by window:" in out
    assert out.count("==") >= 6  # three section headers + one summary header
    # every replay produced a total-return figure
    assert out.count("ret +") + out.count("ret -") >= 3


def test_run_sweep_missing_cache_raises(cfg):
    cfg = override_config(cfg, "cache_dir", cfg.cache_dir / "nonexistent")
    with pytest.raises(RuntimeError, match="cached data"):
        run_sweep(cfg, [(date(2025, 3, 3), date(2025, 5, 30), "t")], [])

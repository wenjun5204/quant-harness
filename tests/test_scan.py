"""Offline scan tests on a synthetic cache."""

import csv
from datetime import date, timedelta

import pytest

from quant_harness.config import Config, Fees, RiskConfig, StrategyConfig
from quant_harness.daily.scan import run_scan


def weekdays(start, n):
    out, cur = [], start
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


@pytest.fixture
def cfg(tmp_path):
    days = weekdays(date(2025, 1, 6), 150)
    cache = tmp_path / "cache"
    cache.mkdir()
    # UP drifts up, FLAT flat, DOWN drifts down
    for sym, rate in [("UP", 0.003), ("FLAT", 0.0), ("DOWN", -0.003), ("REF", 0.0)]:
        c = 10.0
        with open(cache / f"{sym}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for day in days:
                c *= 1 + rate
                w.writerow([day.isoformat(), c * 0.995, c * 1.01, c * 0.99, c, 1000])
    return Config(
        initial_cash=100_000.0,
        symbols=["UP", "FLAT", "DOWN", "REF"],
        reference_symbol="REF",
        fees=Fees(),
        risk=RiskConfig(),
        strategy=StrategyConfig(),
        state_dir=tmp_path / "state",
        cache_dir=cache,
        reports_dir=tmp_path / "reports",
        fetch_retries=1,
        fetch_retry_sleep_s=0,
    )


def test_scan_ranks_by_window_return(cfg):
    out = run_scan(cfg, as_of=cfg_dates_last(cfg), window=60)
    syms_in_order = [line.split("|")[1].strip() for line in out.splitlines()
                     if line.startswith("|") and "---" not in line and "标的" not in line]
    assert syms_in_order[0] == "UP"   # best 3-month return first
    assert syms_in_order[-1] == "DOWN"


def cfg_dates_last(cfg) -> date:
    with open(cfg.cache_dir / "REF.csv") as f:
        return date.fromisoformat([r["timestamp"] for r in csv.DictReader(f)][-1])


def test_scan_contains_market_and_pool_momentum(cfg):
    out = run_scan(cfg, as_of=cfg_dates_last(cfg), window=60)
    assert "池等权窗口动量" in out
    assert "持仓信号" in out  # UP strongly positive → pool momentum > 0
    assert "非投资建议" in out


def test_scan_insufficient_history_reports_n_gracefully(cfg):
    """When a window exceeds available bars, cells say n/a instead of crashing."""
    out = run_scan(cfg, as_of=cfg_dates_last(cfg), window=200)  # > 150 bars available
    assert "n/a" in out
    # symbols with insufficient history are still listed
    assert "UP" in out and "DOWN" in out

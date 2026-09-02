"""End-to-end tests of the daily runner loop — the no-lookahead proofs.

The runner's contract: orders queued at day T's close fill at day T+1's open
(with slippage), decisions at day T depend only on bars through T, and
re-processing a day is a no-op.
"""

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from quant_harness.config import Config, Fees, RiskConfig, StrategyConfig
from quant_harness.data.types import Bar
from quant_harness.daily import runner as runner_mod
from quant_harness.daily.runner import run_day, run_daily
from quant_harness.paper.account import PaperAccount
from quant_harness.paper.risk import RiskManager
from quant_harness.strategy.portfolio import PortfolioStrategy
from quant_harness.strategies.momentum_rotation import MomentumRotation

FEES = Fees(slippage_rate=0.001)


def d(day: str) -> date:
    return date.fromisoformat(day)


def series(points: list[tuple[str, float]], opens: list[float] | None = None) -> list[Bar]:
    """Bars from (iso-date, close) pairs; open defaults to prior close."""
    bars = []
    prev_close = None
    for i, (day, close) in enumerate(points):
        o = opens[i] if opens else (prev_close if prev_close is not None else close)
        c = float(close)
        bars.append(Bar(datetime.fromisoformat(day), o, max(o, c) * 1.02, min(o, c) * 0.98, c))
        prev_close = c
    return bars


def weekdays(start: date, n: int) -> list[date]:
    """n weekday dates starting at `start` (skips weekends, like A-share bars)."""
    out, cur = [], start
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


class StubStrategy(PortfolioStrategy):
    """Emits fixed target weights from the trigger day onward (holds the view)."""

    def __init__(self, trigger_day: date, weights: dict[str, float], hold_after: bool = True):
        self.trigger_day = trigger_day
        self.weights = weights
        self.hold_after = hold_after

    def target_weights(self, history, account, as_of, market_history=None):
        if as_of == self.trigger_day or (self.hold_after and as_of > self.trigger_day):
            return dict(self.weights)
        return {}


def loop(account, history, days, strategy=None, risk=None):
    strategy = strategy or MomentumRotation(momentum_window=5, top_k=2, min_history=10)
    risk = risk or RiskManager(RiskConfig(max_symbol_weight=1.0, max_total_exposure=1.0, stop_loss=0.08, drawdown_halt=0.10))
    results = []
    for day in days:
        results.append(run_day(account, risk, strategy, history, day))
    return results


def flat_history(days: list[date], symbols: list[str], price: float = 10.0) -> dict[str, list[Bar]]:
    return {sym: series([(day.isoformat(), price) for day in days]) for sym in symbols}


class TestNoLookahead:
    def test_order_fills_at_next_day_open_not_signal_close(self):
        """Signal on D (close 11) must fill at D+1's open (12), not D's close."""
        days = weekdays(d("2025-06-02"), 4)
        d_signal = days[1]  # close jumps to 11 on this day
        d_fill = days[2]
        opens = [10.0, 10.0, 12.0, 12.0]  # D+1 gaps up to 12
        closes = [10.0, 11.0, 12.0, 12.0]
        history = {
            "AAA": series([(day.isoformat(), c) for day, c in zip(days, closes)], opens),
            "REF": series([(day.isoformat(), 10.0) for day in days]),
        }
        account = PaperAccount(100_000.0, FEES)
        strategy = StubStrategy(d_signal, {"AAA": 0.1})

        loop(account, history, days[:3], strategy=strategy)

        buys = [t for t in account.trades if t.side == "buy"]
        assert len(buys) == 1
        assert buys[0].date == d_fill  # next trading day
        assert buys[0].price == pytest.approx(12.0 * 1.001)  # D+1 open + slippage
        assert buys[0].price != pytest.approx(11.0 * 1.001)  # not the signal-day close

    def test_future_bars_do_not_change_past_decisions(self):
        """Altering data after D leaves every decision through D identical."""
        days = weekdays(d("2025-06-02"), 5)
        trigger = days[0]
        closes = [11.0, 11.0, 11.0, 11.0, 11.0]

        hist_a = {"AAA": series([(day.isoformat(), c) for day, c in zip(days, closes)]),
                  "REF": series([(day.isoformat(), 10.0) for day in days])}
        # same history but with wild data after the trigger day
        closes_b = [11.0, 11.0, 50.0, 2.0, 99.0]  # identical through day 2, wild after
        hist_b = {"AAA": series([(day.isoformat(), c) for day, c in zip(days, closes_b)]),
                  "REF": series([(day.isoformat(), 10.0) for day in days])}

        acct_a = PaperAccount(100_000.0, FEES)
        acct_b = PaperAccount(100_000.0, FEES)
        strategy = StubStrategy(trigger, {"AAA": 0.2})
        loop(acct_a, hist_a, days[:2], strategy=strategy)
        loop(acct_b, hist_b, days[:2], strategy=strategy)

        assert [ (t.date, t.symbol, t.side, t.quantity, t.price) for t in acct_a.trades ] == \
               [ (t.date, t.symbol, t.side, t.quantity, t.price) for t in acct_b.trades ]
        assert [(o.symbol, o.side, o.quantity) for o in acct_a.pending] == \
               [(o.symbol, o.side, o.quantity) for o in acct_b.pending]

    def test_orders_queued_on_final_day_never_fill(self):
        days = weekdays(d("2025-06-02"), 3)
        history = flat_history(days, ["AAA", "REF"])
        account = PaperAccount(100_000.0, FEES)
        strategy = StubStrategy(days[-1], {"AAA": 0.5}, hold_after=False)

        loop(account, history, days, strategy=strategy)

        assert account.trades == []  # signal on the last day → no fill ever
        assert len(account.pending) == 1 and account.pending[0].side == "buy"

    def test_slice_history_clips_at_as_of(self):
        from quant_harness.data.calendar import slice_history

        days = weekdays(d("2025-06-02"), 5)
        history = flat_history(days, ["AAA"])
        cut = slice_history(history, days[2])
        assert [b.timestamp.date() for b in cut["AAA"]] == days[:3]


class TestTradingCalendar:
    def test_weekends_are_skipped(self):
        from quant_harness.data.calendar import trading_days

        # 2025-06-02 is a Monday; bars exist Mon-Fri, then next Mon-Wed
        bar_days = [d(x) for x in ["2025-06-02", "2025-06-03", "2025-06-04", "2025-06-05",
                                    "2025-06-06", "2025-06-09", "2025-06-10", "2025-06-11"]]
        bars = series([(x.isoformat(), 10.0) for x in bar_days])
        days = trading_days(bars, d("2025-06-01"), d("2025-06-12"))
        assert d("2025-06-07") not in days and d("2025-06-08") not in days  # weekend
        assert d("2025-06-09") in days
        assert len(days) == 8

    def test_equity_curve_only_has_trading_days(self):
        days = weekdays(d("2025-06-02"), 10)
        history = flat_history(days, ["AAA", "REF"])
        account = PaperAccount(100_000.0, FEES)
        loop(account, history, days)
        assert [date.fromisoformat(x) for x, _ in account.equity_curve] == days


class TestHaltAndStopLoss:
    def test_gap_crash_trips_halt_and_flattens(self):
        """A one-day 30% crash on a 50%-weight position → drawdown halt + flatten."""
        days = weekdays(d("2025-06-02"), 8)
        closes = [10.0, 10.0, 10.0, 10.0, 7.0, 7.0, 7.0, 7.0]  # crash on day index 4
        opens = [10.0, 10.0, 10.0, 10.0, 7.0, 7.0, 7.0, 7.0]
        history = {
            "AAA": series([(day.isoformat(), c) for day, c in zip(days, closes)], opens),
            "REF": series([(day.isoformat(), 10.0) for day in days]),
        }
        account = PaperAccount(100_000.0, FEES)
        strategy = StubStrategy(days[0], {"AAA": 0.5})

        loop(account, history, days, strategy=strategy)
        # buy filled on day 2 at ~10, crash on day 4 → equity ~85k vs peak ~100k → halt
        assert account.halted
        assert account.halt_reason is not None
        # flatten order queued; fills on the next processed day (or was the last day)
        sells = [t for t in account.trades if t.reason == "halt_flatten"]
        flatten_pending = [o for o in account.pending if o.reason == "halt_flatten"]
        assert len(sells) + len(flatten_pending) == 1
        # while halted the strategy is suppressed even if it re-signals
        assert not any(t.reason == "strategy" and t.date >= days[4] for t in account.trades)

    def test_stop_loss_exits_position_below_threshold(self):
        days = weekdays(d("2025-06-02"), 8)
        closes = [10.0, 10.0, 10.0, 10.0, 9.1, 9.1, 9.1, 9.1]  # −9% vs avg cost ~10.01
        history = {
            "AAA": series([(day.isoformat(), c) for day, c in zip(days, closes)]),
            "REF": series([(day.isoformat(), 10.0) for day in days]),
        }
        account = PaperAccount(100_000.0, FEES)
        strategy = StubStrategy(days[0], {"AAA": 0.2})

        loop(account, history, days, strategy=strategy)
        stop_sells = [t for t in account.trades if t.reason == "stop_loss"]
        assert len(stop_sells) == 1
        assert stop_sells[0].realized_pnl < 0
        assert stop_sells[0].date == days[5]  # queued on the breach day, filled next open
        assert not account.halted  # −9% × 20% weight ≈ −1.8% portfolio, no halt


class TestMomentumRotationIntegration:
    def test_full_loop_with_real_strategy_stays_solvent(self):
        days = weekdays(d("2025-06-02"), 80)
        # three symbols with distinct drifts; enough history for min_history=60
        def drift_series(rate: float) -> list[Bar]:
            c, out = 10.0, []
            for day in days:
                c *= 1 + rate
                out.append(Bar(datetime.combine(day, datetime.min.time()), c * 0.995, c * 1.01, c * 0.99, c))
            return out

        history = {"AAA": drift_series(0.004), "BBB": drift_series(0.000), "REF": drift_series(-0.002)}
        account = PaperAccount(100_000.0, FEES)
        strategy = MomentumRotation(momentum_window=10, top_k=2, rank_buffer=1, min_history=60)
        risk = RiskManager(RiskConfig())  # default caps

        results = loop(account, history, days, strategy=strategy, risk=risk)

        assert all(r is not None for r in results)
        for _, equity in account.equity_curve:
            assert equity > 0
        assert account.cash >= -1e-9
        assert len(account.trades) > 0  # it actually traded
        for t in account.trades:
            assert t.quantity > 0
            assert t.commission >= FEES.commission_min - 1e-9 or t.commission > 0


@pytest.fixture
def env(tmp_path, monkeypatch):
    days = weekdays(d("2025-06-02"), 70)
    cache = tmp_path / "cache"
    cache.mkdir()
    # AAA drifts up, REF flat; enough history for the rotation strategy
    for sym, rate in [("AAA", 0.003), ("REF", 0.0)]:
        c = 10.0
        with open(cache / f"{sym}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for day in days:
                c *= 1 + rate
                w.writerow([day.isoformat(), c * 0.995, c * 1.01, c * 0.99, c, 1000])
    cfg = Config(
        initial_cash=100_000.0,
        symbols=["AAA", "REF"],
        reference_symbol="REF",
        fees=FEES,
        risk=RiskConfig(),
        strategy=StrategyConfig(momentum_window=10, top_k=1, rank_buffer=1, min_history=60),
        state_dir=tmp_path / "state",
        cache_dir=cache,
        reports_dir=tmp_path / "reports",
        fetch_retries=1,
        fetch_retry_sleep_s=0,
    )

    class StubSource:
        """Offline stand-in: refresh = read the cache the test wrote."""

        def __init__(self, cache_dir, retries=1, retry_sleep_s=0, lookback_years=4):
            self.cache_dir = Path(cache_dir)

        def refresh(self, symbol, end_date, start_date=None):
            from quant_harness.data.loader import load_bars_from_csv

            return [b for b in load_bars_from_csv(self.cache_dir / f"{symbol}.csv")
                    if b.timestamp.date() <= end_date]

        def load_cached(self, symbol):
            from quant_harness.data.loader import load_bars_from_csv

            p = self.cache_dir / f"{symbol}.csv"
            return load_bars_from_csv(p) if p.exists() else []

        def refresh_index(self, symbol, end_date, start_date=None):
            return []  # no index data in this test env

        def load_cached_index(self, symbol):
            return []

    monkeypatch.setattr(runner_mod, "AkshareDataSource", StubSource)
    return cfg, days


class TestRunDailyIdempotency:
    def test_catch_up_and_idempotency(self, env):
        cfg, days = env
        # first run processes only `today` (fresh account)
        assert run_daily(cfg, today=days[-3]) == 0
        state = cfg.state_dir / "account.json"
        account1 = PaperAccount.load(state, cfg.fees)
        assert account1.last_processed_date == days[-3]
        n_trades_1 = len(account1.trades)

        # second run catches up the two missed days
        assert run_daily(cfg, today=days[-1]) == 0
        account2 = PaperAccount.load(state, cfg.fees)
        assert account2.last_processed_date == days[-1]
        assert len(account2.equity_curve) == 3  # 3 processed days total

        # re-running the same day is a no-op
        assert run_daily(cfg, today=days[-1]) == 0
        account3 = PaperAccount.load(state, cfg.fees)
        assert account3.last_processed_date == days[-1]
        assert len(account3.equity_curve) == 3
        assert len(account3.trades) == len(account2.trades)
        assert n_trades_1 <= len(account3.trades)

        # non-trading day (weekend) is a no-op
        assert run_daily(cfg, today=days[-1] + timedelta(days=1)) == 0
        account4 = PaperAccount.load(state, cfg.fees)
        assert account4.last_processed_date == days[-1]

        # reports written for each processed day
        report_files = sorted(p.name for p in cfg.reports_dir.glob("*.md"))
        assert len(report_files) == 3
        content = (cfg.reports_dir / f"{days[-1].isoformat()}.md").read_text(encoding="utf-8")
        assert "净值" in content
        assert "不保证任何收益" in content


class TestPublicationSkewRefetch:
    def test_late_symbol_is_refetched_before_processing(self, env, monkeypatch):
        """A pool symbol whose data publishes after the reference's must be
        refetched, not mistaken for a suspension."""
        cfg, days = env
        today = days[-1]
        calls = {"AAA": 0}

        class LaggySource:
            def __init__(self, cache_dir, retries=1, retry_sleep_s=0, lookback_years=4):
                self.cache_dir = Path(cache_dir)

            def _bars(self, symbol):
                from quant_harness.data.loader import load_bars_from_csv

                return load_bars_from_csv(self.cache_dir / f"{symbol}.csv")

            def refresh(self, symbol, end_date, start_date=None):
                bars = [b for b in self._bars(symbol) if b.timestamp.date() <= end_date]
                if symbol == "AAA":
                    calls["AAA"] += 1
                    if calls["AAA"] == 1 and bars and bars[-1].timestamp.date() == end_date:
                        bars = bars[:-1]  # first fetch: today's bar "not yet published"
                return bars

            def load_cached(self, symbol):
                return self._bars(symbol)

            def refresh_index(self, symbol, end_date, start_date=None):
                return []

            def load_cached_index(self, symbol):
                return []

        monkeypatch.setattr(runner_mod, "AkshareDataSource", LaggySource)
        assert run_daily(cfg, today=today) == 0

        # AAA lagged on the first fetch, was refetched, and got its order queued
        assert calls["AAA"] == 2
        account = PaperAccount.load(cfg.state_dir / "account.json", cfg.fees)
        assert account.last_processed_date == today
        assert any(o.symbol == "AAA" and o.side == "buy" for o in account.pending)

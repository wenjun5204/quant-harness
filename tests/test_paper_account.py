"""Unit tests for the A-share paper account: fills, fees, T+1, limits, persistence."""

from datetime import date, datetime, timedelta

import pytest

from quant_harness.config import Fees
from quant_harness.data.types import Bar
from quant_harness.paper.account import PaperAccount

FEES = Fees(commission_rate=0.00025, commission_min=5.0, stamp_duty_rate=0.0005, slippage_rate=0.001)


def bar(day: str, o: float, h: float | None = None, l: float | None = None, c: float | None = None) -> Bar:
    return Bar(
        datetime.fromisoformat(day),
        o,
        h if h is not None else o * 1.01,
        l if l is not None else o * 0.99,
        c if c is not None else o,
    )


def make_account(cash: float = 100_000.0) -> PaperAccount:
    return PaperAccount(cash, FEES)


def fill_one(account: PaperAccount, symbol: str, side: str, qty: int, day: date,
             day_bar: Bar, prev_close: float) -> list:
    account.queue(symbol, side, qty, day - timedelta(days=1))
    return account.fill_pending(day, {symbol: day_bar}, {symbol: prev_close})


class TestBuyFills:
    def test_small_buy_pays_minimum_commission(self):
        acct = make_account()
        day = date(2025, 6, 2)
        fills = fill_one(acct, "600036", "buy", 100, day, bar("2025-06-02", 10.0), 10.0)
        assert len(fills) == 1
        t = fills[0]
        # notional 100 * 10 * 1.001 = 1001 → 0.025% = 0.25 < ¥5 minimum
        assert t.commission == pytest.approx(5.0)
        assert t.price == pytest.approx(10.0 * 1.001)
        assert acct.cash == pytest.approx(100_000.0 - 1001.0 - 5.0)

    def test_large_buy_pays_rate_commission(self):
        acct = make_account(1_000_000.0)
        day = date(2025, 6, 2)
        fills = fill_one(acct, "600036", "buy", 5_000, day, bar("2025-06-02", 100.0), 100.0)
        t = fills[0]
        notional = 5_000 * 100.0 * 1.001
        assert t.commission == pytest.approx(notional * 0.00025)

    def test_buy_updates_weighted_avg_price(self):
        acct = make_account(1_000_000.0)
        d1, d2 = date(2025, 6, 2), date(2025, 6, 3)
        fill_one(acct, "600036", "buy", 100, d1, bar("2025-06-02", 10.0), 10.0)
        fill_one(acct, "600036", "buy", 100, d2, bar("2025-06-03", 20.0), 20.0)
        pos = acct.positions["600036"]
        assert pos.quantity == 200
        assert pos.avg_price == pytest.approx((100 * 10.0 * 1.001 + 100 * 20.0 * 1.001) / 200)
        assert pos.last_buy_date == d2

    def test_insufficient_cash_cancels_with_reason(self):
        acct = make_account(1_000.0)
        day = date(2025, 6, 2)
        fills = fill_one(acct, "600036", "buy", 100, day, bar("2025-06-02", 10.0), 10.0)
        assert fills == []
        assert len(acct.cancelled) == 1
        assert acct.cancelled[0].reason == "insufficient_cash"
        assert "600036" not in acct.positions
        assert acct.cash == pytest.approx(1_000.0)


class TestSellFills:
    def test_sell_charges_stamp_duty_and_records_realized_pnl(self):
        acct = make_account()
        d1, d2 = date(2025, 6, 2), date(2025, 6, 3)
        fill_one(acct, "600036", "buy", 100, d1, bar("2025-06-02", 10.0), 10.0)
        fills = fill_one(acct, "600036", "sell", 100, d2, bar("2025-06-03", 12.0), 10.0)
        t = fills[0]
        assert t.side == "sell"
        notional = 100 * 12.0 * 0.999
        assert t.stamp_duty == pytest.approx(notional * 0.0005)
        assert t.commission == pytest.approx(5.0)
        avg_cost = 100 * 10.0 * 1.001 / 100
        expected_pnl = (12.0 * 0.999 - avg_cost) * 100 - 5.0 - notional * 0.0005
        assert t.realized_pnl == pytest.approx(expected_pnl)
        assert acct.cash == pytest.approx(
            100_000.0 - (100 * 10.0 * 1.001 + 5.0) + notional - 5.0 - notional * 0.0005
        )
        assert "600036" not in acct.positions  # flat position removed

    def test_sell_without_position_cancels(self):
        acct = make_account()
        day = date(2025, 6, 2)
        fills = fill_one(acct, "600036", "sell", 100, day, bar("2025-06-02", 10.0), 10.0)
        assert fills == []
        assert acct.cancelled[0].reason == "no_position"

    def test_sell_same_day_as_buy_is_rejected_t1(self):
        """Defense in depth: buy and sell both pending, FIFO fills buy then sell hits T+1."""
        acct = make_account()
        day = date(2025, 6, 2)
        day_bar = bar("2025-06-02", 10.0)
        acct.queue("600036", "buy", 100, day - timedelta(days=1))
        acct.queue("600036", "sell", 100, day - timedelta(days=1))
        fills = acct.fill_pending(day, {"600036": day_bar}, {"600036": 10.0})
        assert [t.side for t in fills] == ["buy"]
        assert acct.cancelled[0].reason == "t1_restriction"
        assert acct.positions["600036"].quantity == 100


class TestPriceLimitsAndSuspension:
    def test_buy_cancelled_at_limit_up_open(self):
        acct = make_account(1_000_000.0)
        day = date(2025, 6, 2)
        fills = fill_one(acct, "600036", "buy", 100, day, bar("2025-06-02", 11.0), 10.0)
        assert fills == []
        assert acct.cancelled[0].reason == "price_limit_up"

    def test_sell_cancelled_at_limit_down_open(self):
        acct = make_account()
        d1, d2 = date(2025, 6, 2), date(2025, 6, 3)
        fill_one(acct, "600036", "buy", 100, d1, bar("2025-06-02", 10.0), 10.0)
        fills = fill_one(acct, "600036", "sell", 100, d2, bar("2025-06-03", 8.5), 10.0)
        assert fills == []
        assert acct.cancelled[0].reason == "price_limit_down"

    def test_favorable_direction_at_limit_still_fills(self):
        """A sell at limit-up open (or buy at limit-down open) is fine."""
        acct = make_account()
        d1, d2 = date(2025, 6, 2), date(2025, 6, 3)
        fill_one(acct, "600036", "buy", 100, d1, bar("2025-06-02", 10.0), 10.0)
        fills = fill_one(acct, "600036", "sell", 100, d2, bar("2025-06-03", 11.0), 10.0)
        assert len(fills) == 1

    def test_price_limit_check_disabled(self):
        acct = PaperAccount(1_000_000.0, FEES, price_limit_check=False)
        day = date(2025, 6, 2)
        fills = fill_one(acct, "600036", "buy", 100, day, bar("2025-06-02", 11.0), 10.0)
        assert len(fills) == 1

    def test_suspended_symbol_cancels(self):
        acct = make_account()
        day = date(2025, 6, 2)
        acct.queue("600036", "buy", 100, date(2025, 6, 1))
        fills = acct.fill_pending(day, {}, {})  # no bar at all today
        assert fills == []
        assert acct.cancelled[0].reason == "suspended"


class TestValuationAndPersistence:
    def test_mark_to_market_keeps_suspended_price(self):
        acct = make_account()
        d1 = date(2025, 6, 2)
        fill_one(acct, "600036", "buy", 100, d1, bar("2025-06-02", 10.0, c=10.0), 10.0)
        # next day 600036 suspended, another symbol trades
        acct.mark_to_market({})
        assert acct.positions["600036"].last_price == pytest.approx(10.0)
        assert acct.equity == pytest.approx(acct.cash + 100 * 10.0)

    def test_equity_identity(self):
        acct = make_account()
        d1, d2 = date(2025, 6, 2), date(2025, 6, 3)
        fill_one(acct, "600036", "buy", 100, d1, bar("2025-06-02", 10.0, c=10.5), 10.0)
        acct.mark_to_market({"600036": bar("2025-06-03", 10.6, c=11.0)})
        assert acct.equity == pytest.approx(acct.cash + 100 * 11.0)

    def test_json_round_trip_preserves_everything(self, tmp_path):
        acct = make_account()
        d1, d2 = date(2025, 6, 2), date(2025, 6, 3)
        fill_one(acct, "600036", "buy", 100, d1, bar("2025-06-02", 10.0, c=10.5), 10.0)
        fill_one(acct, "000858", "buy", 200, d1, bar("2025-06-02", 20.0, c=20.0), 20.0)
        acct.queue("600900", "buy", 300, d2)
        acct.mark_to_market({"600036": bar("2025-06-03", 10.6, c=11.0), "000858": bar("2025-06-03", 20.1, c=21.0)})
        acct.record_equity(d1)
        acct.record_equity(d2)
        acct.last_processed_date = d2

        path = tmp_path / "account.json"
        acct.save(path)
        loaded = PaperAccount.load(path, FEES)

        assert loaded.cash == pytest.approx(acct.cash)
        assert loaded.peak_equity == pytest.approx(acct.peak_equity)
        assert loaded.equity_curve == acct.equity_curve
        assert loaded.last_processed_date == d2
        assert loaded.pending[0].symbol == "600900" and loaded.pending[0].quantity == 300
        assert set(loaded.positions) == {"600036", "000858"}
        for sym in loaded.positions:
            assert loaded.positions[sym].quantity == acct.positions[sym].quantity
            assert loaded.positions[sym].avg_price == pytest.approx(acct.positions[sym].avg_price)
            assert loaded.positions[sym].last_price == pytest.approx(acct.positions[sym].last_price)
        assert len(loaded.trades) == len(acct.trades) == 2
        assert loaded.trades[0].commission == pytest.approx(acct.trades[0].commission)

    def test_save_is_atomic_no_tmp_left_behind(self, tmp_path):
        acct = make_account()
        path = tmp_path / "account.json"
        acct.save(path)
        assert path.exists()
        assert list(tmp_path.glob("*.tmp")) == []

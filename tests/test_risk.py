"""Unit tests for the risk layer: stop-loss, caps, drawdown halt."""

from datetime import date, datetime

from quant_harness.config import Fees, RiskConfig
from quant_harness.data.types import Bar
from quant_harness.paper.account import PaperAccount, PendingOrder, Position
from quant_harness.paper.risk import RiskManager

FEES = Fees()


def bar(day: str, o: float, c: float) -> Bar:
    return Bar(datetime.fromisoformat(day), o, max(o, c) * 1.01, min(o, c) * 0.99, c)


def account_with_position(symbol: str, qty: int, avg_price: float, cash: float = 50_000.0) -> PaperAccount:
    acct = PaperAccount(cash, FEES)
    acct.positions[symbol] = Position(symbol, quantity=qty, avg_price=avg_price, last_price=avg_price)
    return acct


class TestStopLoss:
    def test_triggers_at_threshold(self):
        risk = RiskManager(RiskConfig(stop_loss=0.08))
        acct = account_with_position("600036", 100, 10.0)
        day = date(2025, 6, 2)
        exits = risk.forced_exits(acct, {"600036": bar("2025-06-02", 9.1, 9.1)}, day)
        assert len(exits) == 1
        assert exits[0].reason == "stop_loss"
        assert exits[0].quantity == 100

    def test_no_trigger_above_threshold(self):
        risk = RiskManager(RiskConfig(stop_loss=0.08))
        acct = account_with_position("600036", 100, 10.0)
        exits = risk.forced_exits(acct, {"600036": bar("2025-06-02", 9.3, 9.3)}, date(2025, 6, 2))
        assert exits == []

    def test_suspended_symbol_not_assessed(self):
        risk = RiskManager(RiskConfig(stop_loss=0.08))
        acct = account_with_position("600036", 100, 10.0)
        exits = risk.forced_exits(acct, {}, date(2025, 6, 2))
        assert exits == []


class TestDrawdownHalt:
    def test_trips_below_threshold(self):
        risk = RiskManager(RiskConfig(drawdown_halt=0.10))
        # equity 85k (cash 50k + 1000 shares @ 35) vs peak 100k → −15% < −10%
        acct = PaperAccount(100_000.0, FEES)
        acct.cash = 50_000.0
        acct.positions["600036"] = Position("600036", quantity=1000, avg_price=40.0, last_price=35.0)
        assert acct.equity == 85_000.0
        assert risk.drawdown_tripped(acct)

    def test_no_trip_above_threshold(self):
        risk = RiskManager(RiskConfig(drawdown_halt=0.10))
        # equity 95k (cash 50k + 1000 shares @ 45) vs peak 100k → −5%
        acct = PaperAccount(100_000.0, FEES)
        acct.cash = 50_000.0
        acct.positions["600036"] = Position("600036", quantity=1000, avg_price=40.0, last_price=45.0)
        assert acct.equity == 95_000.0
        assert not risk.drawdown_tripped(acct)

    def test_halt_orders_flatten_everything(self):
        risk = RiskManager(RiskConfig())
        acct = account_with_position("600036", 100, 10.0)
        acct.positions["000858"] = Position("000858", quantity=200, avg_price=20.0, last_price=20.0)
        orders = risk.halt_orders(acct, date(2025, 6, 2))
        assert {o.symbol: o.quantity for o in orders} == {"600036": 100, "000858": 200}
        assert all(o.reason == "halt_flatten" for o in orders)


class TestOrderFilter:
    def test_symbol_weight_cap_trims_buy(self):
        cfg = RiskConfig(max_symbol_weight=0.25, max_total_exposure=1.0)
        risk = RiskManager(cfg)
        acct = PaperAccount(100_000.0, FEES)  # equity 100k, cap 25k
        # buy 30000 shares @ ~1 → ~30k notional, over the 25% cap
        order = PendingOrder("600036", "buy", 30_000, date(2025, 6, 2))
        filtered = risk.filter_orders([order], acct, {"600036": 1.0})
        assert len(filtered) == 1
        assert filtered[0].quantity * 1.001 * 1.00025 <= 25_000.0
        assert filtered[0].quantity % 100 == 0

    def test_total_exposure_cap_trims_buy(self):
        cfg = RiskConfig(max_symbol_weight=0.5, max_total_exposure=0.80)
        risk = RiskManager(cfg)
        # existing exposure 75k of 80k cap → only 5k of buying left
        acct = PaperAccount(25_000.0, FEES)
        acct.positions["000858"] = Position("000858", 7_500, 10.0, last_price=10.0)
        order = PendingOrder("600036", "buy", 10_000, date(2025, 6, 2))
        filtered = risk.filter_orders([order], acct, {"600036": 1.0})
        assert len(filtered) == 1
        assert filtered[0].quantity * 1.001 * 1.00025 <= 5_000.0

    def test_buy_below_one_lot_is_dropped(self):
        cfg = RiskConfig(max_symbol_weight=0.01, max_total_exposure=1.0)
        risk = RiskManager(cfg)
        acct = PaperAccount(100_000.0, FEES)  # 1% cap = ¥1000; share @ ¥50 → lot ¥5000 > cap
        order = PendingOrder("600036", "buy", 100, date(2025, 6, 2))
        assert risk.filter_orders([order], acct, {"600036": 50.0}) == []

    def test_sells_pass_through(self):
        risk = RiskManager(RiskConfig())
        acct = account_with_position("600036", 100, 10.0)
        order = PendingOrder("600036", "sell", 100, date(2025, 6, 2))
        assert risk.filter_orders([order], acct, {"600036": 10.0}) == [order]

"""BuyAndHold benchmark strategy."""

from datetime import date, timedelta

from tests.test_momentum_rotation import DAY0, bars_from_closes, make_account
from quant_harness.strategies.buy_and_hold import BuyAndHold


def test_equal_weights_for_all_symbols():
    strat = BuyAndHold(["A", "B", "C"])
    history = {s: bars_from_closes([10.0] * 12) for s in "ABC"}
    weights = strat.target_weights(history, make_account(), DAY0 + timedelta(days=11))
    assert weights == {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}


def test_weights_do_not_change_with_market():
    strat = BuyAndHold(["A", "B"])
    history = {"A": bars_from_closes([10.0, 20.0, 5.0]), "B": bars_from_closes([10.0, 10.0, 10.0])}
    w1 = strat.target_weights(history, make_account(), DAY0)
    w2 = strat.target_weights(history, make_account(), DAY0 + timedelta(days=2))
    assert w1 == w2 == {"A": 0.5, "B": 0.5}


def test_empty_symbols_rejected():
    import pytest

    with pytest.raises(ValueError):
        BuyAndHold([])


class TestMarketFilter:
    def _rising(self, n=15):
        return {"A": bars_from_closes([10.0 * 1.01 ** i for i in range(n)]),
                "B": bars_from_closes([20.0 * 1.005 ** i for i in range(n)])}

    def _falling(self, n=15):
        return {"A": bars_from_closes([10.0 * 0.99 ** i for i in range(n)]),
                "B": bars_from_closes([20.0 * 0.995 ** i for i in range(n)])}

    def test_falling_pool_goes_cash(self):
        strat = BuyAndHold(["A", "B"], market_filter_window=10)
        w = strat.target_weights(self._falling(), make_account(), DAY0 + timedelta(days=14))
        assert w == {}

    def test_rising_pool_holds(self):
        strat = BuyAndHold(["A", "B"], market_filter_window=10)
        w = strat.target_weights(self._rising(), make_account(), DAY0 + timedelta(days=14))
        assert w == {"A": 0.5, "B": 0.5}

    def test_warmup_passes_through(self):
        strat = BuyAndHold(["A", "B"], market_filter_window=10)
        # only 5 bars: filter not ready → hold normally
        hist = {"A": bars_from_closes([10.0] * 5), "B": bars_from_closes([10.0] * 5)}
        w = strat.target_weights(hist, make_account(), DAY0 + timedelta(days=4))
        assert w == {"A": 0.5, "B": 0.5}


class TestStrategyDispatch:
    def test_build_strategy_buy_hold(self):
        from quant_harness.config import Config, StrategyConfig
        from quant_harness.daily.runner import _build_strategy
        from quant_harness.strategies.buy_and_hold import BuyAndHold

        cfg = Config(symbols=["A", "B"], strategy=StrategyConfig(name="buy_hold", market_filter_window=120))
        strat = _build_strategy(cfg)
        assert isinstance(strat, BuyAndHold)
        assert strat.market_filter_window == 120
        assert strat.symbols == ["A", "B"]

    def test_build_strategy_momentum(self):
        from quant_harness.config import Config, StrategyConfig
        from quant_harness.daily.runner import _build_strategy
        from quant_harness.strategies.momentum_rotation import MomentumRotation

        cfg = Config(symbols=["A", "B"],
                     strategy=StrategyConfig(name="momentum_rotation", momentum_window=60, top_k=2))
        strat = _build_strategy(cfg)
        assert isinstance(strat, MomentumRotation)
        assert strat.momentum_window == 60

    def test_build_strategy_unknown_rejected(self):
        from quant_harness.config import Config, StrategyConfig
        from quant_harness.daily.runner import _build_strategy
        import pytest

        cfg = Config(symbols=["A"], strategy=StrategyConfig(name="nope"))
        with pytest.raises(ValueError, match="nope"):
            _build_strategy(cfg)

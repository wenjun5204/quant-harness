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

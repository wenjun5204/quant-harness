"""Unit tests for the momentum rotation strategy."""

from datetime import date, datetime, timedelta

import pytest

from quant_harness.config import Fees
from quant_harness.data.types import Bar
from quant_harness.paper.account import PaperAccount, Position
from quant_harness.strategies.momentum_rotation import MomentumRotation

DAY0 = date(2025, 1, 1)


def bars_from_closes(closes: list[float], start: date = DAY0) -> list:
    out = []
    d = start
    for c in closes:
        out.append(Bar(datetime.combine(d, datetime.min.time()), c, c * 1.01, c * 0.99, c))
        d += timedelta(days=1)
    return out


def make_account(holding: str | None = None) -> PaperAccount:
    acct = PaperAccount(100_000.0, Fees())
    if holding:
        acct.positions[holding] = Position(holding, quantity=100, avg_price=50.0, last_price=50.0)
    return acct


def test_ranks_by_momentum_descending():
    strat = MomentumRotation(momentum_window=5, top_k=2, rank_buffer=1, min_history=10)
    # flat A, +50% B, −30% C over the window
    history = {
        "A": bars_from_closes([10.0] * 12),
        "B": bars_from_closes([10.0] * 7 + [10.0 * 1.5] * 5),
        "C": bars_from_closes([10.0] * 7 + [7.0] * 5),
    }
    ranked = strat.rank(history, DAY0 + timedelta(days=11))
    assert [s for s, _ in ranked] == ["B", "A", "C"]


def test_warmup_returns_empty():
    strat = MomentumRotation(momentum_window=5, top_k=2, min_history=60)
    history = {"A": bars_from_closes([10.0] * 30)}  # not enough history
    assert strat.target_weights(history, make_account(), DAY0 + timedelta(days=29)) == {}


def test_top_k_equal_weights():
    strat = MomentumRotation(momentum_window=5, top_k=2, rank_buffer=1, min_history=10)
    history = {
        "B": bars_from_closes([10.0] * 7 + [15.0] * 5),
        "A": bars_from_closes([10.0] * 12),
        "C": bars_from_closes([10.0] * 7 + [7.0] * 5),
    }
    weights = strat.target_weights(history, make_account(), DAY0 + timedelta(days=11))
    assert weights == {"B": 0.5, "A": 0.5}


def test_hysteresis_keeps_held_symbol_inside_buffer():
    """A held symbol ranked just outside top_k (rank 3 of top_k=2, buffer 1) is kept."""
    strat = MomentumRotation(momentum_window=5, top_k=2, rank_buffer=1, min_history=10)
    history = {
        "B": bars_from_closes([10.0] * 7 + [15.0] * 5),  # rank 1
        "A": bars_from_closes([10.0] * 12 + [12.0]),     # rank 2
        "C": bars_from_closes([10.0] * 12 + [11.0]),     # rank 3 (held)
        "D": bars_from_closes([10.0] * 7 + [7.0] * 6),   # rank 4
    }
    weights = strat.target_weights(history, make_account(holding="C"), DAY0 + timedelta(days=12))
    assert set(weights) == {"B", "A", "C"}


def test_hysteresis_drops_held_symbol_outside_buffer():
    """A held symbol ranked top_k + rank_buffer + 1 (rank 4) is dropped."""
    strat = MomentumRotation(momentum_window=5, top_k=2, rank_buffer=1, min_history=10)
    history = {
        "B": bars_from_closes([10.0] * 7 + [15.0] * 5),  # rank 1
        "A": bars_from_closes([10.0] * 12 + [12.0]),  # rank 2
        "C": bars_from_closes([10.0] * 12 + [11.5]),  # rank 3
        "D": bars_from_closes([10.0] * 12 + [11.0]),  # held, rank 4 → outside buffer
    }
    weights = strat.target_weights(history, make_account(holding="D"), DAY0 + timedelta(days=12))
    assert set(weights) == {"B", "A"}


def test_unrankable_held_symbol_is_kept():
    strat = MomentumRotation(momentum_window=5, top_k=2, rank_buffer=1, min_history=10)
    history = {
        "B": bars_from_closes([10.0] * 7 + [15.0] * 5),
        "A": bars_from_closes([10.0] * 12),
        "S": bars_from_closes([10.0] * 3),  # suspended early: not rankable, but held
    }
    weights = strat.target_weights(history, make_account(holding="S"), DAY0 + timedelta(days=11))
    assert "S" in weights


class TestMinMomentum:
    def _history(self):
        return {
            "UP": bars_from_closes([10.0] * 7 + [15.0] * 5),   # +50%
            "FLAT": bars_from_closes([10.0] * 12),              # 0%
            "DOWN": bars_from_closes([10.0] * 7 + [7.0] * 5),   # -30%
        }

    def test_floor_filters_negative_momentum_entries(self):
        strat = MomentumRotation(momentum_window=5, top_k=2, rank_buffer=1, min_history=10, min_momentum=0.0)
        weights = strat.target_weights(self._history(), make_account(), DAY0 + timedelta(days=11))
        assert set(weights) == {"UP"}  # only positive momentum clears the floor

    def test_no_floor_keeps_relative_momentum(self):
        strat = MomentumRotation(momentum_window=5, top_k=2, rank_buffer=1, min_history=10)
        weights = strat.target_weights(self._history(), make_account(), DAY0 + timedelta(days=11))
        assert set(weights) == {"UP", "FLAT"}

    def test_floor_exits_held_negative_momentum_even_inside_buffer(self):
        strat = MomentumRotation(momentum_window=5, top_k=2, rank_buffer=1, min_history=10, min_momentum=0.0)
        weights = strat.target_weights(self._history(), make_account(holding="DOWN"), DAY0 + timedelta(days=11))
        assert "DOWN" not in weights  # hysteresis never overrides the momentum floor

    def test_all_below_floor_returns_empty(self):
        strat = MomentumRotation(momentum_window=5, top_k=2, min_history=10, min_momentum=0.0)
        history = {"A": bars_from_closes([10.0] * 7 + [7.0] * 5), "B": bars_from_closes([10.0] * 7 + [8.0] * 5)}
        assert strat.target_weights(history, make_account(), DAY0 + timedelta(days=11)) == {}

    def test_rank_ignores_bars_after_as_of(self):
        strat = MomentumRotation(momentum_window=5, top_k=1, min_history=10)
        # 20 bars total, but as_of cuts at bar 11 — the later crash must not affect the rank
        history = {"A": bars_from_closes([10.0] * 11 + [2.0] * 9)}
        ranked = strat.rank(history, DAY0 + timedelta(days=10))
        assert ranked == [("A", 0.0)]


class TestRiskAdjusted:
    def test_smooth_trend_outranks_choppy_trend_of_similar_return(self):
        """Same end-to-end return, far less volatility → higher ranking score."""
        strat = MomentumRotation(momentum_window=5, top_k=2, min_history=10, risk_adjusted=True)
        smooth = [10.0 + 0.22 * i for i in range(12)]  # 10 → 12.42, steady
        choppy = [10.0, 12.0, 10.4, 12.2, 10.5, 12.4, 10.6, 12.6, 10.7, 12.5, 10.8, 12.42]
        history = {"SMOOTH": bars_from_closes(smooth), "CHOPPY": bars_from_closes(choppy)}
        ranked = strat.rank(history, DAY0 + timedelta(days=11))
        assert ranked[0][0] == "SMOOTH"
        assert ranked[1][0] == "CHOPPY"

    def test_rank_reports_raw_momentum_even_when_adjusted(self):
        strat = MomentumRotation(momentum_window=5, top_k=2, min_history=10, risk_adjusted=True)
        history = {"A": bars_from_closes([10.0] * 7 + [15.0] * 5)}
        ranked = strat.rank(history, DAY0 + timedelta(days=11))
        assert ranked[0][0] == "A"
        assert ranked[0][1] == pytest.approx(15.0 / 10.0 - 1.0)  # raw, not the score

    def test_floor_uses_raw_momentum_with_adjusted_ranking(self):
        """The absolute-momentum floor must judge raw return, not the score."""
        strat = MomentumRotation(momentum_window=5, top_k=2, min_history=10,
                                 risk_adjusted=True, min_momentum=0.0)
        # UP: strong positive momentum (high score, positive raw)
        # DOWN_SMOOTH: negative momentum but smooth (score could still be finite negative)
        history = {
            "UP": bars_from_closes([10.0] * 7 + [15.0] * 5),
            "DN": bars_from_closes([15.0] * 7 + [10.0] * 5),
        }
        weights = strat.target_weights(history, make_account(), DAY0 + timedelta(days=11))
        assert set(weights) == {"UP"}  # DN excluded by the raw-momentum floor


class TestMarketFilter:
    def _history(self, up=True):
        # pool of 2: both trend the same way over 12 bars
        rate = 0.02 if up else -0.02
        a = [10.0 * (1 + rate) ** i for i in range(12)]
        return {"A": bars_from_closes(a), "B": bars_from_closes([x * 1.5 for x in a])}

    def test_down_market_goes_all_cash(self):
        strat = MomentumRotation(momentum_window=5, top_k=2, min_history=10,
                                 min_momentum=-9.9, market_filter_window=5)
        # pool falling but per-symbol floor off: only the market filter should trigger
        weights = strat.target_weights(self._history(up=False), make_account(), DAY0 + timedelta(days=11))
        assert weights == {}

    def test_up_market_passes_through(self):
        strat = MomentumRotation(momentum_window=5, top_k=2, min_history=10,
                                 min_momentum=-9.9, market_filter_window=5)
        weights = strat.target_weights(self._history(up=True), make_account(), DAY0 + timedelta(days=11))
        assert set(weights) == {"A", "B"}

    def test_filter_overrides_hysteresis(self):
        """A held position is still liquidated when the pool trend turns down."""
        strat = MomentumRotation(momentum_window=5, top_k=2, min_history=10,
                                 min_momentum=-9.9, market_filter_window=5)
        weights = strat.target_weights(self._history(up=False), make_account(holding="A"), DAY0 + timedelta(days=11))
        assert weights == {}

    def test_filter_warmup_is_pass_through(self):
        """Not enough history for the filter yet → normal (unfiltered) behavior."""
        strat = MomentumRotation(momentum_window=2, top_k=1, min_history=5,
                                 min_momentum=-9.9, market_filter_window=10)
        # only 6 bars: filter needs 11 → not ready; ranking needs 5 → ready
        history = {"A": bars_from_closes([10.0, 10.5, 10.2, 10.8, 11.0, 10.9])}
        weights = strat.target_weights(history, make_account(), DAY0 + timedelta(days=5))
        assert weights == {"A": 1.0}  # no market data → filter passes

    def test_disabled_filter_keeps_old_behavior(self):
        strat = MomentumRotation(momentum_window=5, top_k=2, min_history=10,
                                 min_momentum=-9.9, market_filter_window=0)
        weights = strat.target_weights(self._history(up=False), make_account(), DAY0 + timedelta(days=11))
        assert set(weights) == {"A", "B"}

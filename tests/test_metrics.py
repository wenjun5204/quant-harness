import math

from quant_harness.engine.metrics import compute_metrics


def test_metrics_empty_curve():
    assert compute_metrics([], []) == {}


def test_metrics_flat_equity():
    curve = [(i, 100.0) for i in range(10)]
    m = compute_metrics(curve, [])
    assert m["total_return"] == 0.0
    assert m["max_drawdown"] == 0.0


def test_metrics_growing_equity():
    curve = [(i, 100.0 * (1.01 ** i)) for i in range(100)]
    m = compute_metrics(curve, [])
    assert m["total_return"] > 0
    assert m["annualized_return"] > 0
    assert m["max_drawdown"] == 0.0

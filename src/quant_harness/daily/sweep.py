"""Parameter sweeps: one history load, many replays across a parameter grid.

Windows are evaluated independently so a config can be selected on some
windows and honestly checked on others (select on 2023-2025, validate on 2026
— picking the best cell of the full grid and quoting its best window is how
sweeps lie to you).
"""

from __future__ import annotations

import dataclasses
import itertools
from datetime import date

from quant_harness.config import Config, Fees, RiskConfig, StrategyConfig
from quant_harness.data.types import Bar
from quant_harness.strategies.buy_and_hold import BuyAndHold

from .runner import _load_history, run_replay
from quant_harness.data.akshare_source import AkshareDataSource

_NESTED = {"fees": Fees, "risk": RiskConfig, "strategy": StrategyConfig}


def parse_value(raw: str):
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            continue
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    return raw


def override_config(cfg: Config, path: str, value) -> Config:
    """Return a copy of cfg with `path` (e.g. 'strategy.top_k') overridden."""
    parts = path.split(".")
    if len(parts) == 2 and parts[0] in _NESTED:
        sub = getattr(cfg, parts[0])
        if not hasattr(sub, parts[1]):
            raise ValueError(f"unknown config field: {path}")
        return dataclasses.replace(cfg, **{parts[0]: dataclasses.replace(sub, **{parts[1]: value})})
    if len(parts) == 1 and hasattr(cfg, path):
        return dataclasses.replace(cfg, **{path: value})
    raise ValueError(f"unknown config path: {path}")


def _fmt(v, kind: str) -> str:
    if kind == "pct":
        return f"{v:+.1%}" if isinstance(v, (int, float)) else str(v)
    return f"{v:.2f}" if isinstance(v, (int, float)) else str(v)


def run_sweep(
    cfg: Config,
    windows: list[tuple[date, date, str]],  # (start, end, label)
    grids: list[tuple[str, list]],          # (config path, values)
    benchmark: bool = False,
    refresh: bool = False,
) -> str:
    """Run every grid combination × window; return the full report as text."""
    source = AkshareDataSource(cfg.cache_dir, cfg.fetch_retries, cfg.fetch_retry_sleep_s, cfg.fetch_lookback_years)
    history: dict[str, list[Bar]] = _load_history(
        cfg, source, max(end for _, end, _ in windows), allow_fetch=refresh
    )
    missing = [s for s in cfg.symbols if not history.get(s)]
    if missing:
        raise RuntimeError(f"no cached data for {missing}; run with --refresh first")
    market_history = source.load_cached_index(cfg.market_index) if cfg.market_index else None

    combos = list(itertools.product(*[values for _, values in grids])) if grids else [()]
    lines: list[str] = []
    summary_rows: list[tuple[str, list[str]]] = []

    if benchmark:
        for bf in (0, 120, 250):
            label = "buy_hold_equal" if bf == 0 else f"buy_hold_mtf{bf}"
            lines.append(f"== {label} ==")
            rets = []
            for start, end, wlabel in windows:
                result = run_replay(cfg, start, end, history=history, market_history=market_history,
                                    strategy=BuyAndHold(cfg.symbols, market_filter_window=bf))
                m = result["metrics"]
                lines.append(
                    f"  {wlabel:<14} ret {m.get('total_return', float('nan')):+.1%}"
                    f"  mdd {m.get('max_drawdown', float('nan')):+.1%}"
                    f"  sharpe {m.get('sharpe_ratio', 0.0):.2f}"
                    f"  halt {'yes' if result['halted'] else 'no'}"
                )
                rets.append(f"{m.get('total_return', float('nan')):+.1%}")
            summary_rows.append((label, rets))
            lines.append("")

    for combo in combos:
        variant = cfg
        parts = []
        for (path, _), value in zip(grids, combo):
            variant = override_config(variant, path, value)
            parts.append(f"{path}={value}")
        header = " ".join(parts) if parts else "defaults"
        lines.append(f"== {header} ==")
        rets = []
        for start, end, wlabel in windows:
            result = run_replay(variant, start, end, history=history, market_history=market_history)
            m = result["metrics"]
            pf = m.get("profit_factor", 0.0)
            pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
            lines.append(
                f"  {wlabel:<14} ret {m.get('total_return', float('nan')):+.1%}"
                f"  mdd {m.get('max_drawdown', float('nan')):+.1%}"
                f"  sharpe {m.get('sharpe_ratio', 0.0):.2f}"
                f"  closed {m.get('closed_trades', 0)}"
                f"  win {m.get('win_rate', 0.0):.0%}"
                f"  pf {pf_s}"
                f"  halt {'yes' if result['halted'] else 'no'}"
            )
            rets.append(f"{m.get('total_return', float('nan')):+.1%}")
        summary_rows.append((header, rets))
        lines.append("")

    # summary matrix: one row per combo, one column per window (total return)
    labels = [w for _, _, w in windows]
    width = max([len(h) for h, _ in summary_rows] + [12])
    lines.append("total return by window:")
    lines.append(f"  {'':<{width}} " + " ".join(f"{l:>12}" for l in labels))
    for header, rets in summary_rows:
        lines.append(f"  {header:<{width}} " + " ".join(f"{r:>12}" for r in rets))
    return "\n".join(lines)

"""Configuration for the daily paper-trading runner."""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info < (3, 11):  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class Fees:
    """A-share trading costs."""

    commission_rate: float = 0.00025  # 万2.5
    commission_min: float = 5.0  # minimum commission per order, CNY
    stamp_duty_rate: float = 0.0005  # sell-only
    slippage_rate: float = 0.001


@dataclass(frozen=True)
class RiskConfig:
    max_symbol_weight: float = 0.25
    max_total_exposure: float = 0.80
    stop_loss: float = 0.08
    drawdown_halt: float = 0.10


@dataclass(frozen=True)
class StrategyConfig:
    # "buy_hold" (equal-weight basket + optional pool trend filter — the shipped
    # default, chosen for worst-case robustness) or "momentum_rotation" (research).
    name: str = "buy_hold"
    momentum_window: int = 20
    top_k: int = 4
    rank_buffer: int = 2
    min_history: int = 60
    min_momentum: float | None = None  # absolute-momentum floor; None = off
    risk_adjusted: bool = False  # rank by return/volatility instead of raw return
    market_filter_window: int = 0  # market trend filter; 0 = off. When the
    # filter series return over this window is <= 0, hold cash entirely.
    market_filter_source: str = "index"  # "index" (CSI 300 via market_index)
    # or "pool" (equal-weight pool return — the pre-index proxy)


@dataclass(frozen=True)
class Config:
    initial_cash: float = 100_000.0
    symbols: list[str] = field(default_factory=list)
    reference_symbol: str = "600036"  # trading-calendar source (a pool symbol
    # or the market index)
    market_index: str = "sh000300"  # index used by the market filter and,
    # when set as reference_symbol, the trading calendar
    fees: Fees = field(default_factory=Fees)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    state_dir: Path = Path("state")
    cache_dir: Path = Path("data/cache")
    reports_dir: Path = Path("reports")
    fetch_retries: int = 3
    fetch_retry_sleep_s: int = 300
    fetch_lookback_years: int = 8
    price_limit_check: bool = True


def load_config(path: str | Path) -> Config:
    """Load config.toml; relative paths resolve against the config file's directory."""
    path = Path(path)
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    root = path.parent.resolve()
    paths = raw.get("paths", {})
    data_cfg = raw.get("data", {})
    return Config(
        initial_cash=float(raw.get("initial_cash", 100_000.0)),
        symbols=[str(s) for s in raw.get("symbols", [])],
        reference_symbol=str(raw.get("reference_symbol", "600036")),
        market_index=str(raw.get("market_index", "sh000300")),
        fees=Fees(**raw.get("fees", {})),
        risk=RiskConfig(**raw.get("risk", {})),
        strategy=StrategyConfig(**raw.get("strategy", {})),
        state_dir=(root / paths.get("state_dir", "state")).resolve(),
        cache_dir=(root / paths.get("cache_dir", "data/cache")).resolve(),
        reports_dir=(root / paths.get("reports_dir", "reports")).resolve(),
        fetch_retries=int(data_cfg.get("fetch_retries", 3)),
        fetch_retry_sleep_s=int(data_cfg.get("fetch_retry_sleep_s", 300)),
        fetch_lookback_years=int(data_cfg.get("fetch_lookback_years", 8)),
        price_limit_check=bool(data_cfg.get("price_limit_check", True)),
    )

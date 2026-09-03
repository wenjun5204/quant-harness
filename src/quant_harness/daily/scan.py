"""Market scan: recent performance of every pool symbol.

The "what should I look at" view — trailing returns over multiple horizons,
volatility, and intra-window drawdown per symbol, sorted by 3-month return.
Purely descriptive: it reports what happened, with no recommendation (the
8-year sweep showed no price-pattern edge; scan is for orientation, not
stock-picking).
"""

from __future__ import annotations

from datetime import date

from quant_harness.config import Config
from quant_harness.data.akshare_source import AkshareDataSource
from quant_harness.data.types import Bar
from quant_harness.strategy.portfolio import pool_momentum, series_momentum

from .runner import _load_history


def _momentum(bars: list[Bar], as_of: date, window: int) -> float | None:
    upto = [b for b in bars if b.timestamp.date() <= as_of]
    if len(upto) < window + 1:
        return None
    return upto[-1].close / upto[-1 - window].close - 1


def _daily_vol(bars: list[Bar], as_of: date, window: int) -> float | None:
    upto = [b.close for b in bars if b.timestamp.date() <= as_of]
    if len(upto) < window + 1:
        return None
    closes = upto[-(window + 1):]
    rets = [closes[i + 1] / closes[i] - 1 for i in range(len(closes) - 1)]
    mean = sum(rets) / len(rets)
    return (sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)) ** 0.5


def _max_drawdown(bars: list[Bar], as_of: date, window: int) -> float | None:
    upto = [b.close for b in bars if b.timestamp.date() <= as_of]
    if len(upto) < window + 1:
        return None
    closes = upto[-(window + 1):]
    peak, mdd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    return mdd


def _fmt(v: float | None, pct: bool = True) -> str:
    if v is None:
        return "  n/a"
    return f"{v:+.1%}" if pct else f"{v:.2%}"


def run_scan(cfg: Config, as_of: date | None = None, window: int = 60,
             refresh: bool = False) -> str:
    """Per-symbol recent-performance table, best 3-month return first."""
    source = AkshareDataSource(cfg.cache_dir, cfg.fetch_retries, cfg.fetch_retry_sleep_s,
                               cfg.fetch_lookback_years)
    if refresh and as_of is None:
        as_of = date.today()
    history = _load_history(cfg, source, as_of or date.today(), allow_fetch=refresh)

    ref = history.get(cfg.reference_symbol) or []
    if as_of is None:
        as_of = ref[-1].timestamp.date() if ref else date.today()

    index = source.load_cached_index(cfg.market_index) if cfg.market_index else []
    mkt = series_momentum(index, as_of, window) if index else None
    pool = pool_momentum(history, as_of, window)

    rows = []
    for sym, bars in history.items():
        if not bars:
            continue
        rows.append(
            (
                sym,
                _momentum(bars, as_of, window),        # ~3 months
                _momentum(bars, as_of, 20),            # ~1 month
                _momentum(bars, as_of, 120),           # ~6 months
                _daily_vol(bars, as_of, window),
                _max_drawdown(bars, as_of, window),
                bars[-1].close,
            )
        )
    rows.sort(key=lambda r: -(r[1] if r[1] is not None else -9))

    lines: list[str] = []
    lines.append(f"# 扫描 as of {as_of}（窗口 {window} 交易日 ≈ 3 个月）")
    lines.append("")
    mkt_s = _fmt(mkt) if mkt is not None else "n/a"
    lines.append(f"- 沪深300 窗口动量: {mkt_s}")
    lines.append(f"- 池等权窗口动量: {_fmt(pool)}（{'持仓' if pool is not None and pool > 0 else '空仓'}信号）")
    lines.append("")
    lines.append(f"| {'标的':<8} | {'近3月':>7} | {'近20日':>7} | {'近半年':>7} | {'日波动':>7} | {'窗口内回撤':>8} | {'现价':>8} |")
    lines.append("|" + "---|" * 7)
    for sym, r3, r20, r120, vol, mdd, close in rows:
        lines.append(
            f"| {sym:<8} | {_fmt(r3):>7} | {_fmt(r20):>7} | {_fmt(r120):>7} "
            f"| {_fmt(vol, pct=False):>7} | {_fmt(mdd):>8} | {close:>8.2f} |"
        )
    lines.append("")
    lines.append("> 描述性统计，非投资建议。8年回测表明价格形态选股无可验证优势；关注列表不等于买入列表。")
    return "\n".join(lines)

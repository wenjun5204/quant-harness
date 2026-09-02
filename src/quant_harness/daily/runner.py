"""The daily runner — one code path for live paper trading and backtest replay.

Daily event sequence for each trading day D (identical live and replay):

1. Fill orders queued at D-1's close at D's open (rejections recorded).
2. Mark to market at D's close.
3. Record the equity point; update the peak.
4. Risk pass: drawdown halt (flatten + stop trading) or stop-loss exits.
5. Strategy pass on `slice_history(history, D)` — it never sees past D —
   reconcile targets into orders, filter them through risk, queue for D+1.
6. (live) persist state; write the daily report.

The runner is date-driven and idempotent: it processes every trading day
strictly after `account.last_processed_date`, so missed runs are caught up
with the same historical prices and re-runs are no-ops.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Mapping

from quant_harness.config import Config
from quant_harness.data.akshare_source import AkshareDataSource
from quant_harness.data.calendar import slice_history, trading_days
from quant_harness.data.types import Bar
from quant_harness.engine.metrics import compute_metrics
from quant_harness.paper.account import PaperAccount, TradeRecord
from quant_harness.paper.orders import reconcile
from quant_harness.paper.risk import RiskManager
from quant_harness.paper.stats import trade_stats
from quant_harness.strategy.portfolio import PortfolioStrategy
from quant_harness.strategies.momentum_rotation import MomentumRotation


@dataclass
class DayResult:
    day: date
    fills: list[TradeRecord] = field(default_factory=list)
    cancelled: list = field(default_factory=list)
    queued: list = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    ranks: list[tuple[str, float]] = field(default_factory=list)  # (symbol, momentum), best first


def _day_bars(history: Mapping[str, list[Bar]], day: date) -> dict[str, Bar]:
    out: dict[str, Bar] = {}
    for sym, bars in history.items():
        for b in reversed(bars):
            d = b.timestamp.date()
            if d == day:
                out[sym] = b
                break
            if d < day:
                break
    return out


def _prev_closes(history: Mapping[str, list[Bar]], day: date) -> dict[str, float]:
    out: dict[str, float] = {}
    for sym, bars in history.items():
        for b in reversed(bars):
            if b.timestamp.date() < day:
                out[sym] = b.close
                break
    return out


def run_day(
    account: PaperAccount,
    risk: RiskManager,
    strategy: PortfolioStrategy,
    history: Mapping[str, list[Bar]],
    day: date,
) -> DayResult | None:
    """Process one trading day. Returns None if `day` is not a trading day."""
    day_bars = _day_bars(history, day)
    if not day_bars:
        return None

    result = DayResult(day=day)

    # 1. fill yesterday's queued orders at today's open
    n_cancelled = len(account.cancelled)
    result.fills = account.fill_pending(day, day_bars, _prev_closes(history, day))
    result.cancelled = account.cancelled[n_cancelled:]

    # 2-3. mark to market, record equity
    account.mark_to_market(day_bars)
    account.record_equity(day)

    # 4. risk pass
    if not account.halted and risk.drawdown_tripped(account):
        account.halted = True
        account.halt_reason = (
            f"drawdown {account.drawdown:.1%} breached halt threshold "
            f"({risk.cfg.drawdown_halt:.0%}); flattened, trading paused until manual resume"
        )
        for order in risk.halt_orders(account, day):
            account.queue(order.symbol, order.side, order.quantity, day, order.reason)
        result.risk_notes.append(f"HALT: {account.halt_reason}")

    if account.halted:
        result.risk_notes.append(f"halted (manual --resume required): {account.halt_reason}")
    else:
        exits = risk.forced_exits(account, day_bars, day)
        for order in exits:
            account.queue(order.symbol, order.side, order.quantity, day, order.reason)
        for order in exits:
            close = day_bars[order.symbol].close
            avg_cost = account.positions[order.symbol].avg_price
            result.risk_notes.append(
                f"stop-loss exit: {order.symbol} close {close:.2f} vs avg cost {avg_cost:.2f} "
                f"({close / avg_cost - 1:+.1%})"
            )

        # 5. strategy pass (never sees bars after `day`)
        view = slice_history(history, day)
        weights = strategy.target_weights(view, account, day)
        closes = {sym: bar.close for sym, bar in day_bars.items()}
        orders = reconcile(weights, account, closes, day, account.fees)
        # never buy back a symbol that is being force-exited today
        exiting = {o.symbol for o in exits}
        orders = [o for o in orders if not (o.side == "buy" and o.symbol in exiting)]
        orders = risk.filter_orders(orders, account, closes)
        for order in orders:
            account.queue(order.symbol, order.side, order.quantity, day, order.reason)
        result.queued = orders

        if hasattr(strategy, "rank"):
            result.ranks = strategy.rank(view, day)

    account.last_processed_date = day
    return result


def _build_strategy(cfg: Config) -> MomentumRotation:
    s = cfg.strategy
    return MomentumRotation(
        momentum_window=s.momentum_window,
        top_k=s.top_k,
        rank_buffer=s.rank_buffer,
        min_history=s.min_history,
        min_momentum=s.min_momentum,
        risk_adjusted=s.risk_adjusted,
    )


FETCH_SYMBOL_DELAY_S = 2.0  # pacing between symbols to avoid upstream rate limits


def _refetch_laggards(history: dict[str, list[Bar]], source: AkshareDataSource, cfg: Config, today: date) -> None:
    """Publication-skew guard: the reference symbol has today's bar but some pool
    symbols don't. Their data may simply be published a few minutes later —
    refetch once before treating them as suspended (a wrongly-"suspended"
    symbol gets no buy orders for the day)."""
    ref = history.get(cfg.reference_symbol) or []
    if not ref or ref[-1].timestamp.date() != today:
        return
    lagging = [s for s, b in history.items() if not b or b[-1].timestamp.date() < today]
    for sym in lagging:
        try:
            time.sleep(FETCH_SYMBOL_DELAY_S)
            refreshed = source.refresh(sym, today)
        except RuntimeError:
            continue
        old_last = history[sym][-1].timestamp.date() if history[sym] else None
        new_last = refreshed[-1].timestamp.date() if refreshed else None
        if new_last and (old_last is None or new_last > old_last):
            history[sym] = refreshed
            print(f"note: {sym} data was published late; refetched")
    still = [s for s, b in history.items() if not b or b[-1].timestamp.date() < today]
    if still:
        print(f"warning: no bar today for {still}; treated as suspended")


def _load_history(cfg: Config, source: AkshareDataSource, end_date: date, allow_fetch: bool) -> dict[str, list[Bar]]:
    history: dict[str, list[Bar]] = {}
    for i, sym in enumerate(cfg.symbols):
        if i and allow_fetch:
            time.sleep(FETCH_SYMBOL_DELAY_S)
        bars = source.load_cached(sym)
        if not bars and allow_fetch:
            try:
                bars = source.refresh(sym, end_date)
            except RuntimeError:
                if not bars:
                    raise
        history[sym] = bars
    return history


def run_daily(
    cfg: Config,
    today: date | None = None,
    resume: bool = False,
) -> int:
    """Live paper-trading run: refresh data, process every unprocessed trading day.

    Exit codes: 0 = ok or no-op (non-trading day, already processed, data not
    yet published); 2 = drawdown halt tripped, needs attention.
    """
    from quant_harness.reporting import write_daily_report

    today = today or date.today()
    for d in (cfg.state_dir, cfg.cache_dir, cfg.reports_dir):
        d.mkdir(parents=True, exist_ok=True)
    state_path = cfg.state_dir / "account.json"

    if state_path.exists():
        account = PaperAccount.load(state_path, cfg.fees, cfg.price_limit_check)
    else:
        account = PaperAccount(cfg.initial_cash, cfg.fees, cfg.price_limit_check)

    if resume and account.halted:
        print("resuming: halt flag cleared")
        account.halted = False
        account.halt_reason = None

    source = AkshareDataSource(cfg.cache_dir, cfg.fetch_retries, cfg.fetch_retry_sleep_s)
    history: dict[str, list[Bar]] = {}
    for i, sym in enumerate(cfg.symbols):
        if i:
            time.sleep(FETCH_SYMBOL_DELAY_S)
        try:
            history[sym] = source.refresh(sym, today)
        except RuntimeError as e:
            print(f"warning: {e}; falling back to cache")
            history[sym] = source.load_cached(sym)
    _refetch_laggards(history, source, cfg, today)

    ref_bars = history.get(cfg.reference_symbol, [])
    start = account.last_processed_date + timedelta(days=1) if account.last_processed_date else today
    days = trading_days(ref_bars, start, today)
    if not days:
        print("no new trading days to process")
        return 0

    risk = RiskManager(cfg.risk)
    strategy = _build_strategy(cfg)
    for day in days:
        result = run_day(account, risk, strategy, history, day)
        if result is None:
            continue
        account.save(state_path)
        write_daily_report(cfg.reports_dir / f"{day.isoformat()}.md", result, account)
        print(f"processed {day}: equity {account.equity:,.2f}")

    if account.halted:
        print(f"HALTED: {account.halt_reason}")
        return 2
    return 0


def run_replay(
    cfg: Config,
    start: date,
    end: date,
    refresh: bool = False,
    strategy: PortfolioStrategy | None = None,
    history: dict[str, list[Bar]] | None = None,
) -> dict:
    """Walk-forward backtest: the same run_day loop over historical dates, in memory."""
    if history is None:
        source = AkshareDataSource(cfg.cache_dir, cfg.fetch_retries, cfg.fetch_retry_sleep_s)
        history = _load_history(cfg, source, end, allow_fetch=refresh)
    missing = [s for s in cfg.symbols if not history.get(s)]
    if missing:
        raise RuntimeError(f"no cached data for {missing}; run with --refresh first")

    ref_bars = history.get(cfg.reference_symbol, [])
    days = trading_days(ref_bars, start, end)

    account = PaperAccount(cfg.initial_cash, cfg.fees, cfg.price_limit_check)
    risk = RiskManager(cfg.risk)
    if strategy is None:
        strategy = _build_strategy(cfg)
    for day in days:
        run_day(account, risk, strategy, history, day)

    metrics = compute_metrics(account.equity_curve, []) if len(account.equity_curve) >= 2 else {}
    metrics.update(trade_stats(account.trades))
    return {
        "days_processed": len(days),
        "metrics": metrics,
        "trades": account.trades,
        "equity_curve": account.equity_curve,
        "halted": account.halted,
        "halt_reason": account.halt_reason,
    }

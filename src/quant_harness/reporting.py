"""Markdown daily reports and terminal status output."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quant_harness.daily.runner import DayResult
    from quant_harness.paper.account import PaperAccount

DISCLAIMER = (
    "> 模拟盘结果不构成投资建议，不保证任何收益。本系统保证的是过程严谨："
    "无未来函数、真实费用与规则、风控约束、如实报告。"
)


def _fmt_money(v: float) -> str:
    return f"{v:,.2f}"


def write_daily_report(path: str | Path, result: DayResult, account: PaperAccount) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    prev_equity = account.equity_curve[-2][1] if len(account.equity_curve) >= 2 else account.initial_cash
    equity = account.equity
    day_pnl = equity - prev_equity
    total_return = equity / account.initial_cash - 1

    lines: list[str] = []
    lines.append(f"# 日报 {result.day.isoformat()}")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 净值 | ¥{_fmt_money(equity)} |")
    lines.append(f"| 当日盈亏 | ¥{_fmt_money(day_pnl)} ({day_pnl / prev_equity:+.2%}) |")
    lines.append(f"| 累计收益 | {total_return:+.2%} |")
    lines.append(f"| 当前回撤 | {account.drawdown:+.2%} (峰值 ¥{_fmt_money(account.peak_equity)}) |")
    lines.append(f"| 现金 | ¥{_fmt_money(account.cash)} |")
    lines.append("")

    lines.append("## 持仓")
    if account.positions:
        lines.append("| 标的 | 数量 | 成本 | 现价 | 市值 | 权重 | 浮动盈亏 |")
        lines.append("|---|---|---|---|---|---|---|")
        for sym, pos in sorted(account.positions.items()):
            weight = pos.market_value / equity if equity > 0 else 0.0
            unreal = (pos.last_price - pos.avg_price) * pos.quantity
            lines.append(
                f"| {sym} | {pos.quantity} | {pos.avg_price:.2f} | {pos.last_price:.2f} "
                f"| ¥{_fmt_money(pos.market_value)} | {weight:.1%} | ¥{_fmt_money(unreal)} ({unreal / (pos.avg_price * pos.quantity):+.1%}) |"
            )
    else:
        lines.append("(空仓)")
    lines.append("")

    lines.append("## 当日成交")
    if result.fills:
        lines.append("| 标的 | 方向 | 数量 | 价格 | 佣金 | 印花税 | 已实现盈亏 | 原因 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for t in result.fills:
            lines.append(
                f"| {t.symbol} | {t.side} | {t.quantity} | {t.price:.2f} | {t.commission:.2f} "
                f"| {t.stamp_duty:.2f} | ¥{_fmt_money(t.realized_pnl)} | {t.reason} |"
            )
    else:
        lines.append("(无)")
    lines.append("")

    if result.cancelled:
        lines.append("## 未成交（取消）")
        lines.append("| 标的 | 方向 | 数量 | 原因 |")
        lines.append("|---|---|---|---|")
        for c in result.cancelled:
            lines.append(f"| {c.symbol} | {c.side} | {c.quantity} | {c.reason} |")
        lines.append("")

    if result.queued:
        lines.append("## 明日待成交（次日开盘价成交）")
        lines.append("| 标的 | 方向 | 数量 | 原因 |")
        lines.append("|---|---|---|---|")
        for o in result.queued:
            lines.append(f"| {o.symbol} | {o.side} | {o.quantity} | {o.reason} |")
        lines.append("")

    if result.risk_notes:
        lines.append("## 风控")
        for note in result.risk_notes:
            lines.append(f"- {note}")
        lines.append("")

    if result.ranks:
        lines.append("## 动量排名")
        lines.append("| 排名 | 标的 | 动量 |")
        lines.append("|---|---|---|")
        for i, (sym, mom) in enumerate(result.ranks, 1):
            held = "✓" if sym in account.positions else ""
            lines.append(f"| {i} | {sym}{held} | {mom:+.2%} |")
        lines.append("")

    lines.append(DISCLAIMER)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_status(account: PaperAccount) -> str:
    equity = account.equity
    out = [
        f"净值:       ¥{_fmt_money(equity)}",
        f"现金:       ¥{_fmt_money(account.cash)}",
        f"累计收益:   {equity / account.initial_cash - 1:+.2%}",
        f"当前回撤:   {account.drawdown:+.2%}",
        f"最近处理:   {account.last_processed_date}",
        f"状态:       {'⛔ 已熔断 — ' + str(account.halt_reason) if account.halted else '✓ 正常'}",
    ]
    if account.positions:
        out.append("持仓:")
        for sym, pos in sorted(account.positions.items()):
            unreal = (pos.last_price - pos.avg_price) * pos.quantity
            out.append(
                f"  {sym}: {pos.quantity}股 @ {pos.avg_price:.2f} → {pos.last_price:.2f} "
                f"(¥{_fmt_money(pos.market_value)}, 浮动 ¥{_fmt_money(unreal)})"
            )
    else:
        out.append("持仓: (空仓)")
    if account.pending:
        out.append("待成交订单:")
        for o in account.pending:
            out.append(f"  {o.side} {o.symbol} x{o.quantity} ({o.reason}, 排队于 {o.queued_date})")
    return "\n".join(out)

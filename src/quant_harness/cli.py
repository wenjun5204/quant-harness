"""Command-line entry point: quant-harness <command>."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from quant_harness.config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quant-harness", description="A-share daily paper-trading harness")
    parser.add_argument("--config", default="config.toml", help="path to config.toml (default: ./config.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_daily = sub.add_parser("daily", help="run today's paper-trading cycle (idempotent)")
    p_daily.add_argument("--resume", action="store_true", help="clear the drawdown-halt flag and resume trading")

    p_replay = sub.add_parser("replay", help="walk-forward backtest over cached history")
    p_replay.add_argument("--start", required=True, type=date.fromisoformat)
    p_replay.add_argument("--end", required=True, type=date.fromisoformat)
    p_replay.add_argument("--refresh", action="store_true", help="refresh data from akshare before replaying")

    sub.add_parser("status", help="print current account status")

    p_sweep = sub.add_parser("sweep", help="parameter grid × time-window replay sweep")
    p_sweep.add_argument("--window", action="append", required=True,
                         help="YYYY-MM-DD:YYYY-MM-DD:label (repeatable; label optional)")
    p_sweep.add_argument("--set", dest="sets", action="append", default=[],
                         help="config path and values, e.g. strategy.momentum_window=10,20,60 (repeatable)")
    p_sweep.add_argument("--benchmark", action="store_true", help="include equal-weight buy-and-hold rows")
    p_sweep.add_argument("--refresh", action="store_true", help="refresh data from akshare first")

    p_report = sub.add_parser("report", help="print a daily report")
    p_report.add_argument("date", nargs="?", help="report date YYYY-MM-DD (default: latest)")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    if args.command == "daily":
        from quant_harness.daily.runner import run_daily

        return run_daily(cfg, resume=args.resume)

    if args.command == "replay":
        from quant_harness.daily.runner import run_replay

        result = run_replay(cfg, args.start, args.end, refresh=args.refresh)
        print(f"replay {args.start} → {args.end}: {result['days_processed']} trading days")
        if result["halted"]:
            print(f"HALTED: {result['halt_reason']}")
        for key, value in result["metrics"].items():
            print(f"  {key}: {value:.4f}" if isinstance(value, float) else f"  {key}: {value}")
        return 0

    if args.command == "sweep":
        from quant_harness.daily.sweep import parse_value, run_sweep

        windows = []
        for spec in args.window:
            parts = spec.split(":")
            if len(parts) == 2:
                start_s, end_s, label = parts[0], parts[1], f"{parts[0][5:]}~{parts[1][5:]}"
            elif len(parts) == 3:
                start_s, end_s, label = parts
            else:
                parser.error(f"--window expects START:END[:LABEL], got {spec!r}")
            windows.append((date.fromisoformat(start_s), date.fromisoformat(end_s), label))
        grids = []
        for spec in args.sets:
            path, _, values = spec.partition("=")
            if not path or not values:
                parser.error(f"--set expects PATH=v1,v2,..., got {spec!r}")
            grids.append((path.strip(), [parse_value(v) for v in values.split(",")]))
        print(run_sweep(cfg, windows, grids, benchmark=args.benchmark, refresh=args.refresh))
        return 0

    if args.command == "status":
        from quant_harness.paper.account import PaperAccount
        from quant_harness.reporting import format_status

        state_path = cfg.state_dir / "account.json"
        if not state_path.exists():
            print("no account yet — run `quant-harness daily` first")
            return 1
        print(format_status(PaperAccount.load(state_path, cfg.fees, cfg.price_limit_check)))
        return 0

    if args.command == "report":
        reports = sorted(cfg.reports_dir.glob("*.md")) if cfg.reports_dir.exists() else []
        if not reports:
            print("no reports yet — run `quant-harness daily` first")
            return 1
        if args.date:
            path = cfg.reports_dir / f"{args.date}.md"
            if not path.exists():
                print(f"no report for {args.date}")
                return 1
        else:
            path = reports[-1]
        sys.stdout.write(path.read_text(encoding="utf-8"))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""バックテスト CLI。

使い方:
    python backtest.py

config/ の設定（universe.txt / rules.yaml）に従い、過去データで
スクリーニング3条件のエントリー＋損切り/利確/時間切れ手仕舞いを検証する。

注意:
  これは過去データに対する機械的シミュレーションであり、将来の成績を保証する
  ものではありません。約定は理想化（損切り/利確は指定価格ちょうどで約定、
  スリッページ・手数料・流動性は未考慮）されています。
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import date

from src import backtest as bt
from src import config_loader
from src.data_fetcher import DataFetcher

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

try:
    from rich.console import Console
    from rich.table import Table
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False


def _save_trades_csv(trades, target_date: date) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"backtest_trades_{target_date.isoformat()}.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "entry_date", "entry_price", "exit_date",
                    "exit_price", "exit_reason", "ret_pct", "r_multiple"])
        for t in trades:
            w.writerow([
                t.code, t.name,
                t.entry_date.date().isoformat(), round(t.entry_price, 2),
                t.exit_date.date().isoformat(), round(t.exit_price, 2),
                t.exit_reason, round(t.ret_pct, 4), round(t.r_multiple, 3),
            ])
    return path


def _save_summary_csv(s: bt.BacktestSummary, target_date: date) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"backtest_summary_{target_date.isoformat()}.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerow(["total_trades", s.total_trades])
        w.writerow(["wins", s.wins])
        w.writerow(["losses", s.losses])
        w.writerow(["win_rate", round(s.win_rate, 4)])
        w.writerow(["avg_win_R", round(s.avg_win_r, 4)])
        w.writerow(["avg_loss_R", round(s.avg_loss_r, 4)])
        w.writerow(["expectancy_R_direct", round(s.expectancy_r_direct, 4)])
        w.writerow(["expectancy_R_formula", round(s.expectancy_r_formula, 4)])
        w.writerow(["max_drawdown_R", round(s.max_drawdown_r, 4)])
        for reason, cnt in s.reason_counts.items():
            w.writerow([f"exit_{reason}", cnt])
    return path


def _render(s: bt.BacktestSummary, n_stocks: int, failures: list, skipped: list,
            period: str, target_date: date) -> None:
    rows = [
        ("総トレード数", f"{s.total_trades:,}"),
        ("勝ち / 負け", f"{s.wins:,} / {s.losses:,}"),
        ("勝率", f"{s.win_rate*100:.1f}%"),
        ("平均利益R（勝ち平均）", f"{s.avg_win_r:+.3f} R"),
        ("平均損失R（負け平均）", f"{s.avg_loss_r:.3f} R"),
        ("期待値R（全トレード平均）", f"{s.expectancy_r_direct:+.3f} R"),
        ("期待値R（式: 勝率×利益R−(1−勝率)×損失R）", f"{s.expectancy_r_formula:+.3f} R"),
        ("最大ドローダウン", f"{s.max_drawdown_r:.3f} R"),
    ]
    reason_str = " / ".join(f"{k}:{v}" for k, v in sorted(s.reason_counts.items()))

    if _HAS_RICH:
        console = Console()
        console.rule(f"[bold]バックテスト結果  ({period}, 実行日 {target_date.isoformat()})[/bold]")
        console.print(f"対象 {n_stocks} 銘柄  /  取得失敗 {len(failures)}  /  データ不足スキップ {len(skipped)}",
                      style="dim")
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("指標")
        table.add_column("値", justify="right")
        for label, val in rows:
            table.add_row(label, val)
        console.print(table)
        console.print(f"手仕舞い内訳: {reason_str}", style="dim")
        if s.expectancy_r_direct >= 0:
            console.print("→ 期待値はプラス（このルール・期間では優位性あり）", style="green")
        else:
            console.print("→ 期待値はマイナス（このルール・期間では優位性なし）", style="red")
    else:
        print("=" * 60)
        print(f"バックテスト結果 ({period}, 実行日 {target_date.isoformat()})")
        print(f"対象 {n_stocks} 銘柄 / 取得失敗 {len(failures)} / スキップ {len(skipped)}")
        print("=" * 60)
        for label, val in rows:
            print(f"{label:<40} {val:>12}")
        print(f"手仕舞い内訳: {reason_str}")


def main() -> int:
    today = date.today()

    try:
        rules = config_loader.load_rules()
        stocks = config_loader.load_universe()
    except (FileNotFoundError, ValueError) as e:
        print(f"[設定エラー] {e}", file=sys.stderr)
        return 1

    if "backtest" not in rules:
        print("[設定エラー] rules.yaml に backtest セクションがありません。", file=sys.stderr)
        return 1

    period = str(rules["backtest"].get("history_period", "540d"))
    name_map = config_loader.build_name_map(stocks)
    fetcher = DataFetcher(rules, today=today, period=period)

    all_trades: list = []
    failures: list = []
    skipped: list = []

    total = len(stocks)
    print(f"{total} 銘柄をバックテスト中... (取得期間 {period}, 初回はデータ取得に数分かかります)\n")

    for n, stock in enumerate(stocks, 1):
        code, name = stock.code, name_map.get(stock.code, stock.code)
        # 進捗（同一行更新）
        print(f"\r  [{n}/{total}] {code}        ", end="", flush=True)

        try:
            df = fetcher.fetch(code)
        except Exception as e:
            failures.append({"code": code, "reason": str(e)})
            continue

        if df is None:
            failures.append({"code": code, "reason": "取得失敗"})
            continue

        # データが指標計算に足りない銘柄はスキップ（compute_signals が None を返す）
        signals = bt.compute_signals(df, rules)
        if signals is None:
            skipped.append({"code": code, "reason": "データ不足"})
            continue

        all_trades.extend(bt.backtest_symbol(df, rules, code, name))

    print("\r" + " " * 40 + "\r", end="")  # 進捗行をクリア

    summary = bt.summarize(all_trades)
    _render(summary, total, failures, skipped, period, today)

    trades_path = _save_trades_csv(all_trades, today)
    summary_path = _save_summary_csv(summary, today)
    print(f"\nトレード明細: {trades_path}")
    print(f"サマリー:     {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

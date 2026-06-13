#!/usr/bin/env python3
"""ポートフォリオ制約・現実約定・期間分割を加えたバックテストの比較分析。

使い方:
    python backtest_portfolio.py

出力:
  - 4構成（制約有無 × 楽観/現実）の比較表
  - ^N225 の200日線局面（上昇 / 調整・下落）別の期待値R
  - エクイティカーブ用CSV（data/equity_*.csv）
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import date

import pandas as pd

from src import backtest as bt
from src import config_loader, portfolio_backtest as pbt
from src.data_fetcher import DataFetcher
from src.indicators import moving_average

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BIG = 10 ** 9  # 「制限なし」を表す十分大きい値

try:
    from rich.console import Console
    from rich.table import Table
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False


def load_all_stocks(rules: dict, period: str) -> tuple[list, int, int]:
    """キャッシュ（前回のバックテスト取得分）から225銘柄を読み込み、StockData化する。"""
    stocks_cfg = config_loader.load_universe()
    name_map = config_loader.build_name_map(stocks_cfg)
    fetcher = DataFetcher(rules, period=period)

    prepared = []
    failures = skipped = 0
    total = len(stocks_cfg)
    for n, st in enumerate(stocks_cfg, 1):
        print(f"\r  読込 [{n}/{total}] {st.code}        ", end="", flush=True)
        df = None
        try:
            df = fetcher.fetch(st.code)
        except Exception:
            df = None
        if df is None:
            failures += 1
            continue
        sd = pbt.prepare_stock(df, rules, st.code, name_map.get(st.code, st.code))
        if sd is None:
            skipped += 1
            continue
        prepared.append(sd)
    print("\r" + " " * 50 + "\r", end="")
    return prepared, failures, skipped


def run_config(stocks, rules, max_conc, max_risk, gap, cost):
    trades = pbt.run_portfolio(stocks, rules, max_conc, max_risk, gap, cost,
                               priority=rules["backtest"].get("priority", "rsi_asc"))
    summary = bt.summarize(trades)
    return trades, summary


def fetch_n225_regime(rules: dict, period: str) -> pd.DataFrame:
    """^N225 を取得し、各日の 200日線に対する局面（up / weak）を返す。"""
    macro = rules["macro"]
    fetcher = DataFetcher(rules, period=period)
    df = fetcher.fetch(macro.get("n225_symbol", "^N225"))
    if df is None:
        return pd.DataFrame()
    window = int(macro.get("n225_ma", 200))
    close = df["Close"].astype(float)
    ma = moving_average(close, window)
    regime = pd.Series("weak", index=df.index)
    regime[close >= ma] = "up"
    regime[ma.isna()] = "n/a"
    return pd.DataFrame({"close": close, "ma200": ma, "regime": regime})


def regime_split(trades, regime_df: pd.DataFrame) -> dict:
    """各トレードをエントリー日の局面で分類し、局面別の集計を返す。"""
    if regime_df.empty:
        return {}
    reg_by_date = regime_df["regime"].to_dict()
    buckets: dict = {"up": [], "weak": []}
    for t in trades:
        d = pd.Timestamp(t.entry_date)
        reg = reg_by_date.get(d)
        if reg in ("up", "weak"):
            buckets[reg].append(t)
    out = {}
    for k, ts in buckets.items():
        out[k] = bt.summarize(ts)
    return out


def save_equity(trades, label: str, target_date: date) -> str:
    eq = pbt.equity_curve(trades)
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"equity_{label}_{target_date.isoformat()}.csv")
    eq.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def main() -> int:
    today = date.today()
    try:
        rules = config_loader.load_rules()
    except (FileNotFoundError, ValueError) as e:
        print(f"[設定エラー] {e}", file=sys.stderr)
        return 1

    bt_cfg = rules["backtest"]
    period = str(bt_cfg.get("history_period", "540d"))
    max_conc = int(bt_cfg.get("max_concurrent", 5))
    max_risk = float(bt_cfg.get("max_total_risk_r", 5.0))
    cost = float(bt_cfg.get("cost_round_trip", 0.002))

    print(f"キャッシュから225銘柄を読込中... (取得期間 {period})")
    stocks, failures, skipped = load_all_stocks(rules, period)
    print(f"読込完了: {len(stocks)}銘柄 / 取得失敗 {failures} / スキップ {skipped}\n")

    # --- 4構成 ---
    configs = [
        ("A 制約なし・楽観",   BIG,      BIG,      False, 0.0),
        ("B 制約あり・楽観",   max_conc, max_risk, False, 0.0),
        ("C 制約なし・現実",   BIG,      BIG,      True,  cost),
        ("D 制約あり・現実",   max_conc, max_risk, True,  cost),
    ]
    results = {}
    for label, mc, mr, gap, cst in configs:
        trades, summary = run_config(stocks, rules, mc, mr, gap, cst)
        results[label] = (trades, summary)

    # --- 比較表 ---
    _render_comparison(results, today)

    # --- ベースライン整合チェック（A が既存 src/backtest.py を再現するか） ---
    _validate_baseline(results["A 制約なし・楽観"][1])

    # --- 期間分割（局面別） ---
    regime_df = fetch_n225_regime(rules, period)
    _render_regime(results, regime_df)

    # --- エクイティカーブ保存（A=ベースライン, D=最も現実的） ---
    pa = save_equity(results["A 制約なし・楽観"][0], "A_baseline", today)
    pd_ = save_equity(results["D 制約あり・現実"][0], "D_realistic", today)
    print(f"\nエクイティCSV: {pa}")
    print(f"エクイティCSV: {pd_}")
    return 0


def _fmt(s: bt.BacktestSummary) -> dict:
    return {
        "trades": f"{s.total_trades:,}",
        "win": f"{s.win_rate*100:.1f}%",
        "win_r": f"{s.avg_win_r:+.3f}",
        "loss_r": f"{s.avg_loss_r:.3f}",
        "exp": f"{s.expectancy_r_direct:+.3f}",
        "dd": f"{s.max_drawdown_r:.1f}",
    }


def _render_comparison(results: dict, target_date: date) -> None:
    if _HAS_RICH:
        console = Console()
        console.rule(f"[bold]構成別 比較  (実行日 {target_date.isoformat()})[/bold]")
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("構成")
        table.add_column("トレード数", justify="right")
        table.add_column("勝率", justify="right")
        table.add_column("平均利益R", justify="right")
        table.add_column("平均損失R", justify="right")
        table.add_column("期待値R", justify="right")
        table.add_column("最大DD(R)", justify="right")
        for label, (_, s) in results.items():
            f = _fmt(s)
            table.add_row(label, f["trades"], f["win"], f["win_r"], f["loss_r"], f["exp"], f["dd"])
        console.print(table)
    else:
        print("=== 構成別 比較 ===")
        for label, (_, s) in results.items():
            f = _fmt(s)
            print(f"{label}: トレード{f['trades']} 勝率{f['win']} 期待値R{f['exp']} 最大DD{f['dd']}")


def _validate_baseline(summary_a: bt.BacktestSummary) -> None:
    print(f"\n[整合チェック] A(制約なし・楽観): {summary_a.total_trades:,}トレード / "
          f"期待値R {summary_a.expectancy_r_direct:+.3f}  "
          f"(既存 src/backtest.py の 1,377 / +0.122 を再現していれば一致)")


def _render_regime(results: dict, regime_df: pd.DataFrame) -> None:
    print()
    if regime_df.empty:
        print("[期間分割] ^N225 を取得できず、局面別集計はスキップしました。")
        return

    # 局面別集計はサンプル最大の A(制約なし・楽観) で実施（シグナルの優位性を見る）
    trades_a = results["A 制約なし・楽観"][0]
    split = regime_split(trades_a, regime_df)
    up_days = int((regime_df["regime"] == "up").sum())
    weak_days = int((regime_df["regime"] == "weak").sum())

    if _HAS_RICH:
        console = Console()
        console.rule("[bold]局面別 期待値（A:制約なし・楽観で集計）[/bold]")
        console.print(f"対象期間の地合い: 上昇(200日線上) {up_days}日 / 調整・下落(200日線下) {weak_days}日",
                      style="dim")
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("局面")
        table.add_column("トレード数", justify="right")
        table.add_column("勝率", justify="right")
        table.add_column("期待値R", justify="right")
        table.add_column("最大DD(R)", justify="right")
        labels = {"up": "上昇局面(200日線上)", "weak": "調整・下落(200日線下)"}
        for k in ("up", "weak"):
            s = split.get(k)
            if s is None:
                continue
            table.add_row(labels[k], f"{s.total_trades:,}", f"{s.win_rate*100:.1f}%",
                          f"{s.expectancy_r_direct:+.3f}", f"{s.max_drawdown_r:.1f}")
        console.print(table)
    else:
        print("=== 局面別 期待値（A） ===")
        for k in ("up", "weak"):
            s = split.get(k)
            if s:
                print(f"{k}: トレード{s.total_trades} 勝率{s.win_rate*100:.1f}% 期待値R{s.expectancy_r_direct:+.3f}")


if __name__ == "__main__":
    raise SystemExit(main())

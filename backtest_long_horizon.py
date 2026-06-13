#!/usr/bin/env python3
"""長期（約5年）での最終検証。

構成D（同時保有5・現実約定）＋マクロフィルタを、約2年版と約5年版で比較し、
DD/利益比が期間延長でどう動くか、下落相場でも壊滅的でないかを判定する。
※ パラメータ（閾値・損切り/利確・保有日数）は一切変更しない。

使い方:
    python backtest_long_horizon.py
"""

from __future__ import annotations

import copy
import sys
from datetime import date

import pandas as pd

from src import backtest as bt
from src import config_loader, portfolio_backtest as pbt
from src.data_fetcher import DataFetcher
from src.indicators import moving_average

from backtest_portfolio import load_all_stocks, save_equity

try:
    from rich.console import Console
    from rich.table import Table
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False


def build_allowed(rules: dict, period: str):
    """^N225 を取得し、200日線の上の日（新規許可日）集合と局面Seriesを返す。"""
    macro = rules["macro"]
    df = DataFetcher(rules, period=period).fetch(macro.get("n225_symbol", "^N225"))
    if df is None:
        return None, None
    window = int(macro.get("n225_ma", 200))
    close = df["Close"].astype(float)
    ma = moving_average(close, window)
    regime = pd.Series("up", index=df.index)
    regime[close < ma] = "weak"
    regime[ma.isna()] = "n/a"
    allowed = {d for d, r in regime.items() if r != "weak"}
    return allowed, regime


def run_dfilter(stocks, rules, allowed, max_conc, max_risk, cost):
    return pbt.run_portfolio(stocks, rules, max_conc, max_risk, True, cost,
                             priority=rules["backtest"].get("priority", "rsi_asc"),
                             entry_allowed=allowed)


def metrics(trades):
    s = bt.summarize(trades)
    eq = pbt.equity_curve(trades)
    final = float(eq["cum_r"].iloc[-1]) if not eq.empty else 0.0
    mdd = float(eq["drawdown_r"].max()) if not eq.empty else 0.0
    ratio = (mdd / final) if final > 0 else float("inf")
    span = (eq["date"].min().date(), eq["date"].max().date()) if not eq.empty else (None, None)
    return s, final, mdd, ratio, span, eq


def regime_split(trades, regime: pd.Series):
    reg_by_date = regime.to_dict()
    buckets = {"up": [], "weak": []}
    for t in trades:
        r = reg_by_date.get(pd.Timestamp(t.entry_date))
        if r in buckets:
            buckets[r].append(t)
    return {k: bt.summarize(v) for k, v in buckets.items()}, \
           {k: len(v) for k, v in buckets.items()}


def main() -> int:
    today = date.today()
    rules = config_loader.load_rules()
    bt_cfg = rules["backtest"]
    short_period = str(bt_cfg.get("history_period", "540d"))
    long_period = str(bt_cfg.get("long_history_period", "5y"))
    long_regime_period = str(bt_cfg.get("long_regime_period", "max"))
    max_conc = int(bt_cfg.get("max_concurrent", 5))
    max_risk = float(bt_cfg.get("max_total_risk_r", 5.0))
    cost = float(bt_cfg.get("cost_round_trip", 0.002))

    # マクロ許可日は長期N225から作る（短期窓も内包するので両方に使える）
    print(f"^N225 を {long_regime_period} で取得しマクロ局面を作成中...")
    allowed, regime = build_allowed(rules, long_regime_period)
    if allowed is None:
        print("[エラー] ^N225 取得失敗。", file=sys.stderr)
        return 1

    results = {}
    for tag, period in [("約2年", short_period), ("約5年", long_period)]:
        print(f"\n[{tag}] 225銘柄を {period} で読込中...")
        stocks, failures, skipped = load_all_stocks(rules, period)
        print(f"  読込: {len(stocks)}銘柄 / 取得失敗 {failures} / スキップ {skipped}")
        trades = run_dfilter(stocks, rules, allowed, max_conc, max_risk, cost)
        s, final, mdd, ratio, span, eq = metrics(trades)
        split, counts = regime_split(trades, regime)
        results[tag] = dict(s=s, final=final, mdd=mdd, ratio=ratio, span=span,
                            eq=eq, split=split, counts=counts, trades=trades)
        if tag == "約5年":
            save_equity(trades, "Dfilter_5y", today)

    _render_compare(results)
    _render_regime(results["約5年"])
    return 0


def _render_compare(results):
    if _HAS_RICH:
        c = Console()
        c.rule("[bold]D+マクロフィルタ：約2年 vs 約5年[/bold]")
        t = Table(show_header=True, header_style="bold cyan")
        for col in ["期間", "データ範囲", "トレード", "勝率", "期待値R", "最大DD(R)", "累計R", "DD/利益比"]:
            t.add_column(col, justify="right" if col != "期間" else "left")
        for tag, r in results.items():
            sp = r["span"]
            t.add_row(tag, f"{sp[0]}〜{sp[1]}", f"{r['s'].total_trades:,}",
                      f"{r['s'].win_rate*100:.1f}%", f"{r['s'].expectancy_r_direct:+.3f}",
                      f"{r['mdd']:.1f}", f"{r['final']:+.1f}", f"{r['ratio']:.2f}")
        c.print(t)
    else:
        for tag, r in results.items():
            print(f"{tag}: トレード{r['s'].total_trades} 期待値R{r['s'].expectancy_r_direct:+.3f} "
                  f"最大DD{r['mdd']:.1f} 累計{r['final']:+.1f} 比{r['ratio']:.2f}")


def _render_regime(r5):
    split, counts = r5["split"], r5["counts"]
    if _HAS_RICH:
        c = Console()
        c.rule("[bold]約5年版：局面別（上昇 vs 下落）[/bold]")
        t = Table(show_header=True, header_style="bold cyan")
        for col in ["局面", "トレード", "勝率", "期待値R"]:
            t.add_column(col, justify="right" if col != "局面" else "left")
        labels = {"up": "上昇局面(200日線上)", "weak": "下落局面(200日線下)"}
        for k in ("up", "weak"):
            s = split.get(k)
            if s and s.total_trades:
                t.add_row(labels[k], f"{s.total_trades:,}", f"{s.win_rate*100:.1f}%",
                          f"{s.expectancy_r_direct:+.3f}")
        c.print(t)
        c.print("注: D+フィルタは原則 上昇局面でのみ新規建て。下落局面の建玉は、"
                "建てた後に200日線を割って下落局面入りした分。", style="dim")
    else:
        for k in ("up", "weak"):
            s = split.get(k)
            if s:
                print(f"{k}: {s.total_trades} 勝率{s.win_rate*100:.1f}% 期待値R{s.expectancy_r_direct:+.3f}")


if __name__ == "__main__":
    raise SystemExit(main())

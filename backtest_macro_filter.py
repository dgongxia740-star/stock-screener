#!/usr/bin/env python3
"""構成D（同時保有5・現実約定）にマクロフィルタを加えた再計測と判定。

マクロフィルタ: ^N225 の終値が200日線の下にある日は新規エントリーを停止
              （既存建玉の手仕舞いは通常どおり）。

使い方:
    python backtest_macro_filter.py

判定:
  (a) フィルタ版の期待値R > 0 か
  (b) 最大ドローダウン(R) < 累計R（利益 > 谷）か
"""

from __future__ import annotations

import sys
from datetime import date

import pandas as pd

from src import backtest as bt
from src import config_loader, portfolio_backtest as pbt
from src.data_fetcher import DataFetcher
from src.indicators import moving_average

# backtest_portfolio の読込・保存ヘルパを再利用
from backtest_portfolio import DATA_DIR, load_all_stocks, save_equity

try:
    from rich.console import Console
    from rich.table import Table
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False


def build_regime(rules: dict):
    """^N225 を長め期間で取得し、各日の200日線局面と「新規許可日」集合を返す。"""
    macro = rules["macro"]
    period = str(rules["backtest"].get("regime_history_period", "1000d"))
    fetcher = DataFetcher(rules, period=period)
    df = fetcher.fetch(macro.get("n225_symbol", "^N225"))
    if df is None:
        return None, None
    window = int(macro.get("n225_ma", 200))
    close = df["Close"].astype(float)
    ma = moving_average(close, window)
    regime = pd.Series("up", index=df.index)
    regime[close < ma] = "weak"        # 200日線の下 → 新規停止
    regime[ma.isna()] = "n/a"          # 200日線が出ない極初期 → 判定不能（許可）
    # 新規エントリー許可日 = weak 以外（up と n/a）
    allowed = {d for d, r in regime.items() if r != "weak"}
    return regime, allowed


def evaluate_pair(d_summary, df_summary, d_eq, df_eq):
    """フィルタなし(D)・あり(D+filter)の比較行を返す。"""
    def row(label, s, eq):
        final = float(eq["cum_r"].iloc[-1]) if not eq.empty else 0.0
        mdd = float(eq["drawdown_r"].max()) if not eq.empty else 0.0
        return {
            "label": label,
            "trades": s.total_trades,
            "win": s.win_rate,
            "exp": s.expectancy_r_direct,
            "mdd": mdd,
            "final": final,
        }
    return row("D 現実約定（フィルタなし）", d_summary, d_eq), \
           row("D+マクロフィルタ", df_summary, df_eq)


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
    priority = bt_cfg.get("priority", "rsi_asc")

    print(f"キャッシュから225銘柄を読込中... (取得期間 {period})")
    stocks, failures, skipped = load_all_stocks(rules, period)
    print(f"読込完了: {len(stocks)}銘柄 / 取得失敗 {failures} / スキップ {skipped}")

    regime, allowed = build_regime(rules)
    if allowed is None:
        print("[エラー] ^N225 を取得できず、マクロフィルタを適用できません。", file=sys.stderr)
        return 1

    # --- 構成D: フィルタなし / フィルタあり ---
    d_trades = pbt.run_portfolio(stocks, rules, max_conc, max_risk, True, cost,
                                 priority=priority)
    df_trades = pbt.run_portfolio(stocks, rules, max_conc, max_risk, True, cost,
                                  priority=priority, entry_allowed=allowed)
    d_sum, df_sum = bt.summarize(d_trades), bt.summarize(df_trades)
    d_eq, df_eq = pbt.equity_curve(d_trades), pbt.equity_curve(df_trades)

    # --- エントリー日の局面内訳（フィルタなしDのトレードで、停止された日数の感覚） ---
    reg_by_date = regime.to_dict()
    blocked = sum(1 for t in d_trades if reg_by_date.get(pd.Timestamp(t.entry_date)) == "weak")
    print(f"\nマクロ判定: ^N225 期間 {bt_cfg.get('regime_history_period')} / "
          f"上昇日 {int((regime=='up').sum())} / 停止日(200日線下) {int((regime=='weak').sum())} / "
          f"判定不能 {int((regime=='n/a').sum())}")
    print(f"フィルタなしDのうち、200日線下で建てていたトレード: {blocked}件 → フィルタ版では除外対象")

    # --- 比較表 ---
    r_d, r_df = evaluate_pair(d_sum, df_sum, d_eq, df_eq)
    _render(r_d, r_df, today)

    # --- 判定 ---
    a_pass = r_df["exp"] > 0
    b_pass = r_df["mdd"] < r_df["final"]
    _verdict(r_df, a_pass, b_pass)

    # --- 保存 ---
    p = save_equity(df_trades, "Dfilter_realistic", today)
    print(f"\nエクイティCSV(フィルタ版): {p}")
    return 0


def _render(r_d, r_df, target_date):
    if _HAS_RICH:
        c = Console()
        c.rule(f"[bold]構成D：マクロフィルタ有無の比較  ({target_date.isoformat()})[/bold]")
        t = Table(show_header=True, header_style="bold cyan")
        t.add_column("構成")
        t.add_column("トレード数", justify="right")
        t.add_column("勝率", justify="right")
        t.add_column("期待値R", justify="right")
        t.add_column("最大DD(R)", justify="right")
        t.add_column("累計R", justify="right")
        for r in (r_d, r_df):
            t.add_row(r["label"], f"{r['trades']:,}", f"{r['win']*100:.1f}%",
                      f"{r['exp']:+.3f}", f"{r['mdd']:.1f}", f"{r['final']:+.1f}")
        c.print(t)
    else:
        print("=== 構成D：マクロフィルタ有無 ===")
        for r in (r_d, r_df):
            print(f"{r['label']}: トレード{r['trades']} 勝率{r['win']*100:.1f}% "
                  f"期待値R{r['exp']:+.3f} 最大DD{r['mdd']:.1f} 累計R{r['final']:+.1f}")


def _verdict(r_df, a_pass, b_pass):
    mark = lambda ok: "✅ Pass" if ok else "❌ Fail"
    lines = [
        f"(a) 期待値Rがプラス: {mark(a_pass)}  （期待値R = {r_df['exp']:+.3f}）",
        f"(b) 最大DD < 累計利益: {mark(b_pass)}  （最大DD {r_df['mdd']:.1f}R vs 累計 {r_df['final']:+.1f}R）",
    ]
    if _HAS_RICH:
        c = Console()
        c.rule("[bold]判定（D+マクロフィルタ）[/bold]")
        for ln in lines:
            c.print(ln, style="green" if "Pass" in ln else "red")
        if a_pass and b_pass:
            c.print("→ (a)(b)とも満たす：自動化を検討できる水準", style="bold green")
        else:
            c.print("→ 条件を満たさない項目あり：このままの自動化は非推奨", style="bold red")
    else:
        print("=== 判定 ===")
        for ln in lines:
            print(ln)


if __name__ == "__main__":
    raise SystemExit(main())

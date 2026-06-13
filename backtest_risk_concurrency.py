#!/usr/bin/env python3
"""リスク割合の不変性の実証 ＋ 同時保有数(5→3)の再計測。

前提: 損益はR建て（R=損切り幅）。資金リスク割合を変えてもR指標は不変なはず。
本スクリプトは (1) それを実証し、(2) 次善策として同時保有5→3を比較する。

すべて「D＋マクロフィルタ＋現実約定」をベースに実施。
"""

from __future__ import annotations

import copy
import sys
from datetime import date

from src import backtest as bt
from src import config_loader, portfolio_backtest as pbt

from backtest_portfolio import load_all_stocks, save_equity
from backtest_macro_filter import build_regime

try:
    from rich.console import Console
    from rich.table import Table
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False


def metrics(trades):
    s = bt.summarize(trades)
    eq = pbt.equity_curve(trades)
    final = float(eq["cum_r"].iloc[-1]) if not eq.empty else 0.0
    mdd = float(eq["drawdown_r"].max()) if not eq.empty else 0.0
    return s, final, mdd


def run_dfilter(stocks, rules, allowed, max_conc, max_risk, cost):
    return pbt.run_portfolio(stocks, rules, max_conc, max_risk, True, cost,
                             priority=rules["backtest"].get("priority", "rsi_asc"),
                             entry_allowed=allowed)


def main() -> int:
    today = date.today()
    rules = config_loader.load_rules()
    bt_cfg = rules["backtest"]
    period = str(bt_cfg.get("history_period", "540d"))
    max_risk = float(bt_cfg.get("max_total_risk_r", 5.0))
    cost = float(bt_cfg.get("cost_round_trip", 0.002))

    print(f"キャッシュから225銘柄を読込中... (取得期間 {period})")
    stocks, failures, skipped = load_all_stocks(rules, period)
    print(f"読込完了: {len(stocks)}銘柄 / 取得失敗 {failures} / スキップ {skipped}")

    _, allowed = build_regime(rules)
    if allowed is None:
        print("[エラー] ^N225 を取得できず中止。", file=sys.stderr)
        return 1

    # === (1) リスク割合の不変性を実証 ===
    # risk_ratio を 1% と 0.5% にしても、R建ての結果は完全一致するはず
    rules_1pct = copy.deepcopy(rules); rules_1pct["risk"]["risk_ratio"] = 0.01
    rules_05pct = copy.deepcopy(rules); rules_05pct["risk"]["risk_ratio"] = 0.005
    t1 = run_dfilter(stocks, rules_1pct, allowed, 5, max_risk, cost)
    t05 = run_dfilter(stocks, rules_05pct, allowed, 5, max_risk, cost)
    s1, f1, d1 = metrics(t1)
    s05, f05, d05 = metrics(t05)
    identical = (s1.total_trades == s05.total_trades
                 and abs(s1.expectancy_r_direct - s05.expectancy_r_direct) < 1e-12
                 and abs(d1 - d05) < 1e-12 and abs(f1 - f05) < 1e-12)
    print("\n[不変性の実証] D+フィルタを risk 1.0% と 0.5% で実行")
    print(f"  1.0%: トレード{s1.total_trades} 期待値R{s1.expectancy_r_direct:+.4f} "
          f"最大DD{d1:.2f}R 累計{f1:+.2f}R")
    print(f"  0.5%: トレード{s05.total_trades} 期待値R{s05.expectancy_r_direct:+.4f} "
          f"最大DD{d05:.2f}R 累計{f05:+.2f}R")
    print(f"  → R指標は完全一致: {identical}  （資金建てDDは実額で半減するが、R比＝(b)判定は不変）")

    # === (2) 次善策: 同時保有 5 → 3 ===
    rows = []
    for mc in (5, 3):
        trades = run_dfilter(stocks, rules, allowed, mc, float(mc), cost)
        s, final, mdd = metrics(trades)
        rows.append({
            "label": f"D+フィルタ（同時保有{mc}）",
            "mc": mc, "trades": s.total_trades, "win": s.win_rate,
            "exp": s.expectancy_r_direct, "mdd": mdd, "final": final,
            "trades_obj": trades,
        })
        if mc == 3:
            save_equity(trades, "Dfilter_conc3", today)

    _render(rows, today)
    for r in rows:
        a = r["exp"] > 0
        b = r["mdd"] < r["final"]
        _verdict(r, a, b)
    return 0


def _render(rows, target_date):
    if _HAS_RICH:
        c = Console()
        c.rule(f"[bold]D+マクロフィルタ：同時保有 5 vs 3  ({target_date.isoformat()})[/bold]")
        t = Table(show_header=True, header_style="bold cyan")
        for col, j in [("構成", "left"), ("トレード数", "right"), ("勝率", "right"),
                       ("期待値R", "right"), ("最大DD(R)", "right"), ("累計R", "right")]:
            t.add_column(col, justify=j)
        for r in rows:
            t.add_row(r["label"], f"{r['trades']:,}", f"{r['win']*100:.1f}%",
                      f"{r['exp']:+.3f}", f"{r['mdd']:.1f}", f"{r['final']:+.1f}")
        c.print(t)
    else:
        for r in rows:
            print(f"{r['label']}: トレード{r['trades']} 勝率{r['win']*100:.1f}% "
                  f"期待値R{r['exp']:+.3f} 最大DD{r['mdd']:.1f} 累計R{r['final']:+.1f}")


def _verdict(r, a, b):
    mark = lambda ok: "✅Pass" if ok else "❌Fail"
    msg = (f"[{r['label']}] (a)期待値R>0: {mark(a)} ({r['exp']:+.3f})  / "
           f"(b)最大DD<累計利益: {mark(b)} (DD {r['mdd']:.1f}R vs 利益 {r['final']:+.1f}R)")
    if _HAS_RICH:
        Console().print(msg, style="green" if (a and b) else "yellow")
    else:
        print(msg)


if __name__ == "__main__":
    raise SystemExit(main())

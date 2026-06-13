#!/usr/bin/env python3
"""日本株スイングトレード・スクリーニング CLI。

使い方:
    python screen.py

config/ 配下の設定に従って当日のスクリーニングを実行し、
結果をターミナルに表示し、data/screen_YYYY-MM-DD.csv に保存する。

注意:
  本ツールは投資助言ではなく、事前定義したルールに合致する銘柄を
  機械的に抽出するだけのものです。売買判断は利用者自身が行ってください。
"""

from __future__ import annotations

import sys
from datetime import date

from src import config_loader, indicators, macro_filter, position_sizing, reporter, screener
from src.data_fetcher import DataFetcher


def main() -> int:
    today = date.today()

    # --- 設定読み込み ---
    try:
        rules = config_loader.load_rules()
        stocks = config_loader.load_universe()
    except (FileNotFoundError, ValueError) as e:
        print(f"[設定エラー] {e}", file=sys.stderr)
        return 1

    name_map = config_loader.build_name_map(stocks)
    fetcher = DataFetcher(rules, today=today)

    passed: list[dict] = []
    failures: list[dict] = []   # 取得失敗
    skipped: list[dict] = []    # データ不足等でスキップ

    print(f"{len(stocks)}銘柄をスクリーニング中... (yfinanceは15〜20分遅延・日足ベース)\n")

    for stock in stocks:
        code = stock.code
        name = name_map.get(code, code)

        # 1銘柄の失敗で全体を止めない
        try:
            df = fetcher.fetch(code)
        except Exception as e:  # 想定外も握りつぶしてスキップ
            failures.append({"code": code, "name": name, "reason": str(e)})
            continue

        if df is None:
            failures.append({"code": code, "name": name, "reason": "取得失敗"})
            continue

        ind = indicators.compute(df, rules)
        if ind is None:
            skipped.append({"code": code, "name": name, "reason": "データ不足"})
            continue

        result = screener.check(ind, rules)
        if result["passed"]:
            pos = position_sizing.compute(ind, rules)
            passed.append({
                "code": code,
                "name": name,
                "close": ind.close,
                "ma25": ind.ma,
                "ma25_slope": ind.ma_slope,
                "ma25_slope_up": ind.ma_slope_up,
                "rsi14": ind.rsi,
                "last_volume": ind.last_volume,
                "avg_volume": ind.avg_volume,
                "volume_ratio": ind.volume_ratio,
                "stop_loss": pos.stop_loss,
                "risk_per_share": pos.risk_per_share,
                "shares": pos.shares,
                "note": pos.note,
            })

    # --- マクロ・フィルタ ---
    macro = macro_filter.evaluate(fetcher, rules)

    # --- 出力 ---
    reporter.render(passed, macro, failures, skipped, today)
    csv_path = reporter.save_csv(passed, today)
    print(f"\nCSVを保存しました: {csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""事前計算スクリプト（GitHub Actions が毎営業日に実行）。

225銘柄をスクリーニングし、結果を data/latest_screen.json に書き出す。
クラウドの閲覧アプリ（streamlit_app.py）はこのJSONを読むだけにすることで、
高速・安定に表示できる（アプリ側でデータ取得をしない）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from src import config_loader, screen_runner

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "latest_screen.json")


def _json_default(o):
    """numpy型などをJSONに変換するためのフォールバック。"""
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(f"JSON変換できない型: {type(o)}")


def main() -> int:
    rules = config_loader.load_rules()
    jst = ZoneInfo("Asia/Tokyo")
    today = datetime.now(jst).date()

    result = screen_runner.screen_universe(rules, today)
    result["generated_at"] = datetime.now(jst).strftime("%Y-%m-%d %H:%M JST")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=_json_default)

    print(f"wrote {OUT_PATH}: 買い時 "
          f"{sum(1 for c in result['candidates'] if c['tradeable'])}件 / "
          f"候補 {len(result['candidates'])}件 / 全 {len(result['all_rows'])}件 / "
          f"取得失敗 {len(result['failures'])}件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

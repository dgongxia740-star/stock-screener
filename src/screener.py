"""スクリーニング条件の判定（3条件すべてAND）。

条件1: 25MA が上向き（slope_lookback営業日前より高い）
条件2: RSI(14) が [min, max] の範囲内（両端含む）
条件3: 直近の出来高 >= 過去avg_window日平均出来高
"""

from __future__ import annotations

from typing import Dict

from .indicators import Indicators


def check(ind: Indicators, rules: dict) -> Dict[str, bool]:
    """各条件の真偽を辞書で返す（"passed" に総合結果）。"""
    rsi_min = float(rules["rsi"]["min"])
    rsi_max = float(rules["rsi"]["max"])

    cond_trend = ind.ma_slope_up
    cond_rsi = rsi_min <= ind.rsi <= rsi_max
    cond_volume = ind.last_volume >= ind.avg_volume

    return {
        "trend": cond_trend,
        "rsi": cond_rsi,
        "volume": cond_volume,
        "passed": cond_trend and cond_rsi and cond_volume,
    }

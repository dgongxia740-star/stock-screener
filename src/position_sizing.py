"""損切り価格と推奨株数の算出。

- 損切り価格 = max(終値 × (1 - pct), 直近 low_lookback 日の安値)
- 推奨株数  = floor( (総資金 × リスク割合) / (終値 - 損切り価格) )

エッジケース:
  終値 <= 損切り価格（損切り幅 0 以下）の場合は株数 0（建て見送り）として扱う。
"""

from __future__ import annotations

import math
from typing import NamedTuple

from .indicators import Indicators


class Position(NamedTuple):
    stop_loss: float       # 推奨損切り価格
    risk_per_share: float  # 1株あたりのリスク幅（終値 - 損切り）
    shares: int            # 推奨株数（100株単位には丸めない素の計算値）
    note: str              # 補足（建て見送り等）


def compute(ind: Indicators, rules: dict) -> Position:
    pct = float(rules["stop_loss"]["pct"])
    total_capital = float(rules["risk"]["total_capital"])
    risk_ratio = float(rules["risk"]["risk_ratio"])

    pct_stop = ind.close * (1.0 - pct)
    stop_loss = max(pct_stop, ind.low_lookback)

    risk_per_share = ind.close - stop_loss

    if risk_per_share <= 0:
        # 損切り価格が終値以上 → リスク幅が取れない
        return Position(
            stop_loss=stop_loss,
            risk_per_share=risk_per_share,
            shares=0,
            note="建て見送り（損切り幅0以下）",
        )

    risk_budget = total_capital * risk_ratio
    raw_shares = risk_budget / risk_per_share
    shares = int(math.floor(raw_shares))

    if shares <= 0:
        return Position(
            stop_loss=stop_loss,
            risk_per_share=risk_per_share,
            shares=0,
            note="建て見送り（株数0）",
        )

    return Position(
        stop_loss=stop_loss,
        risk_per_share=risk_per_share,
        shares=shares,
        note="",
    )

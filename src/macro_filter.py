"""マクロ・フィルタ（日経平均の200日線、VIXの水準）による相場環境の警告。

データが取得できない場合も落とさず、「判定不能」として扱う。
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from .data_fetcher import DataFetcher
from .indicators import moving_average


class MacroResult:
    def __init__(self):
        self.warnings: List[str] = []   # ヘッダに出す警告メッセージ
        self.n225_close: Optional[float] = None
        self.n225_ma: Optional[float] = None
        self.n225_status: str = "判定不能"
        self.vix: Optional[float] = None
        self.vix_status: str = "判定不能"


def evaluate(fetcher: DataFetcher, rules: dict) -> MacroResult:
    macro = rules["macro"]
    result = MacroResult()

    # --- 日経平均 200日線 ---
    n225_symbol = macro.get("n225_symbol", "^N225")
    n225_ma_window = int(macro.get("n225_ma", 200))
    df = fetcher.fetch(n225_symbol)
    if df is not None and len(df) >= n225_ma_window:
        close = df["Close"].astype(float)
        ma = moving_average(close, n225_ma_window)
        last_close = close.iloc[-1]
        last_ma = ma.iloc[-1]
        if not pd.isna(last_ma):
            result.n225_close = float(last_close)
            result.n225_ma = float(last_ma)
            if last_close < last_ma:
                result.n225_status = "200日線の下"
                result.warnings.append("⚠相場全体が弱い：新規建て縮小推奨")
            else:
                result.n225_status = "200日線の上"

    # --- VIX ---
    vix_symbol = macro.get("vix_symbol", "^VIX")
    vix_warn = float(macro.get("vix_warn", 25.0))
    vix_halt = float(macro.get("vix_halt", 30.0))
    dfv = fetcher.fetch(vix_symbol)
    if dfv is not None and not dfv.empty:
        vix_val = float(dfv["Close"].astype(float).iloc[-1])
        result.vix = vix_val
        if vix_val > vix_halt:
            result.vix_status = f"{vix_halt}超"
            result.warnings.append("⚠VIX警戒：新規停止")
        elif vix_val > vix_warn:
            result.vix_status = f"{vix_warn}超"
            result.warnings.append("⚠VIX高：新規半減")
        else:
            result.vix_status = "平常"

    return result

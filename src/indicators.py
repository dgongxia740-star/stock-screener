"""テクニカル指標の計算（25MA・傾き・RSI(Wilder)・出来高比）。

データ欠損や行数不足でも例外を投げず、計算不能なら None を返す方針。
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def moving_average(close: pd.Series, window: int) -> pd.Series:
    """単純移動平均。"""
    return close.rolling(window=window, min_periods=window).mean()


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder平滑（RMA）によるRSI。

    Wilderの正式な手順:
      1. 最初の avg_gain / avg_loss を、最初 period 本の値幅の単純平均でシードする。
      2. 以降は  avg = (前avg × (period-1) + 当日値) / period  で平滑化する。
    （単純なEWMをデータ先頭から回す方法は系列が短いと教科書値からずれるため、
      履歴の長短に関わらず正しい値が出るこの方式を採用する。）

    返り値は close と同じ index の Series。最初の period 本は NaN。
    """
    n = len(close)
    rsi = pd.Series(index=close.index, dtype=float)  # 既定 NaN
    if n <= period:
        return rsi

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    g = gain.to_numpy()
    l = loss.to_numpy()

    # index 1..period（period本）の単純平均でシード（index period 位置に確定）
    avg_gain = g[1:period + 1].mean()
    avg_loss = l[1:period + 1].mean()

    out = [float("nan")] * n

    def _rsi(ag: float, al: float) -> float:
        if al == 0 and ag == 0:
            return 50.0          # 値動きなし
        if al == 0:
            return 100.0         # 下落なし
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + g[i]) / period
        avg_loss = (avg_loss * (period - 1) + l[i]) / period
        out[i] = _rsi(avg_gain, avg_loss)

    return pd.Series(out, index=close.index)


class Indicators:
    """1銘柄分の指標計算結果を保持する。"""

    def __init__(
        self,
        close: float,
        ma: float,
        ma_prev: float,
        ma_slope: float,
        ma_slope_up: bool,
        rsi: float,
        last_volume: float,
        avg_volume: float,
        volume_ratio: float,
        low_lookback: float,
    ):
        self.close = close
        self.ma = ma                    # 直近の25MA
        self.ma_prev = ma_prev          # slope_lookback 営業日前の25MA
        self.ma_slope = ma_slope        # ma - ma_prev
        self.ma_slope_up = ma_slope_up  # 上向きか
        self.rsi = rsi
        self.last_volume = last_volume
        self.avg_volume = avg_volume
        self.volume_ratio = volume_ratio
        self.low_lookback = low_lookback  # 直近 low_lookback 日の安値


def compute(df: pd.DataFrame, rules: dict) -> Optional[Indicators]:
    """指標を計算して Indicators を返す。データ不足なら None。"""
    ma_window = int(rules["trend"]["ma_window"])
    slope_lookback = int(rules["trend"]["slope_lookback"])
    rsi_period = int(rules["rsi"]["period"])
    vol_window = int(rules["volume"]["avg_window"])
    low_lookback = int(rules["stop_loss"]["low_lookback"])

    # 必要な最小行数（最も長い指標に合わせる）
    min_rows = max(ma_window + slope_lookback, rsi_period + 1, vol_window, low_lookback)
    if df is None or len(df) < min_rows:
        return None

    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    low = df["Low"].astype(float)

    ma_series = moving_average(close, ma_window)
    rsi_series = wilder_rsi(close, rsi_period)
    avg_vol_series = volume.rolling(window=vol_window, min_periods=vol_window).mean()

    ma_now = ma_series.iloc[-1]
    ma_prev = ma_series.iloc[-1 - slope_lookback]
    rsi_now = rsi_series.iloc[-1]
    avg_vol = avg_vol_series.iloc[-1]
    last_vol = volume.iloc[-1]
    last_close = close.iloc[-1]
    low_n = low.iloc[-low_lookback:].min()

    # いずれかが NaN なら計算不能としてスキップ
    if any(pd.isna(x) for x in [ma_now, ma_prev, rsi_now, avg_vol, last_vol, last_close, low_n]):
        return None

    slope = float(ma_now - ma_prev)
    volume_ratio = float(last_vol / avg_vol) if avg_vol > 0 else 0.0

    return Indicators(
        close=float(last_close),
        ma=float(ma_now),
        ma_prev=float(ma_prev),
        ma_slope=slope,
        ma_slope_up=slope > 0,
        rsi=float(rsi_now),
        last_volume=float(last_vol),
        avg_volume=float(avg_vol),
        volume_ratio=volume_ratio,
        low_lookback=float(low_n),
    )

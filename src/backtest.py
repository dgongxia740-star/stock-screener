"""バックテスト・ロジック。

スクリーニング3条件でエントリーし、損切り・利確・時間切れで手仕舞いした場合の
成績（トレード数・勝率・平均利益R/損失R・期待値R・最大ドローダウン）を算出する。

先読み回避:
  - シグナルは日 t の終値（およびそれ以前）のみで判定。
  - エントリーは entry 設定に従い、既定では翌営業日 t+1 の始値で約定。
  - 1銘柄1ポジション。手仕舞い後にのみ再エントリー可能（建玉の重複なし）。

R の定義:
  R = stop_pct（損切り幅）。利益/損失をこの R で正規化して評価する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from .indicators import moving_average, wilder_rsi


@dataclass
class Trade:
    code: str
    name: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    exit_reason: str   # "target" / "stop" / "timeout" / "eod"
    ret_pct: float     # 損益率（exit/entry - 1）
    r_multiple: float  # ret_pct / R


def compute_signals(df: pd.DataFrame, rules: dict) -> Optional[pd.Series]:
    """各営業日で3条件ANDが成立したかの真偽 Series を返す。

    ライブのスクリーニングと同じ条件を、系列全体に対して算出する。
    指標ウォームアップ期間（NaN）は False になる。
    """
    ma_window = int(rules["trend"]["ma_window"])
    slope_lookback = int(rules["trend"]["slope_lookback"])
    rsi_period = int(rules["rsi"]["period"])
    rsi_min = float(rules["rsi"]["min"])
    rsi_max = float(rules["rsi"]["max"])
    vol_window = int(rules["volume"]["avg_window"])

    min_rows = max(ma_window + slope_lookback, rsi_period + 1, vol_window)
    if df is None or len(df) < min_rows:
        return None

    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)

    ma = moving_average(close, ma_window)
    rsi = wilder_rsi(close, rsi_period)
    avg_vol = volume.rolling(window=vol_window, min_periods=vol_window).mean()

    cond_trend = ma > ma.shift(slope_lookback)               # 25MAが上向き
    cond_rsi = (rsi >= rsi_min) & (rsi <= rsi_max)           # RSIレンジ
    cond_volume = volume >= avg_vol                          # 出来高
    signal = cond_trend & cond_rsi & cond_volume
    return signal.fillna(False)


def backtest_symbol(df: pd.DataFrame, rules: dict, code: str, name: str) -> List[Trade]:
    """1銘柄のバックテストを実行し、確定したトレードのリストを返す。"""
    signals = compute_signals(df, rules)
    if signals is None:
        return []

    bt = rules["backtest"]
    stop_pct = float(bt["stop_pct"])
    target_pct = float(bt["target_pct"])
    max_hold = int(bt["max_hold_days"])
    entry_mode = str(bt.get("entry", "next_open"))

    open_ = df["Open"].astype(float).to_numpy()
    high = df["High"].astype(float).to_numpy()
    low = df["Low"].astype(float).to_numpy()
    close = df["Close"].astype(float).to_numpy()
    idx = df.index
    sig = signals.to_numpy()
    n = len(df)

    trades: List[Trade] = []
    i = 0
    while i < n:
        if not sig[i]:
            i += 1
            continue

        # --- エントリー位置と価格を決定 ---
        if entry_mode == "signal_close":
            entry_idx = i
            entry_price = close[i]
        else:  # "next_open"（既定）
            entry_idx = i + 1
            if entry_idx >= n:
                break  # 翌日が存在しない（最終日のシグナル）→ エントリー不可
            entry_price = open_[entry_idx]

        if entry_price <= 0:
            i += 1
            continue

        stop_price = entry_price * (1.0 - stop_pct)
        target_price = entry_price * (1.0 + target_pct)

        # --- 手仕舞いを前方シミュレーション ---
        last_day = min(entry_idx + max_hold, n - 1)  # 時間切れ or データ末尾
        exit_idx = last_day
        exit_price = close[last_day]
        exit_reason = "eod" if last_day < entry_idx + max_hold else "timeout"

        for j in range(entry_idx, last_day + 1):
            hit_stop = low[j] <= stop_price
            hit_target = high[j] >= target_price
            if hit_stop and hit_target:
                # 同日に両方ヒット → 損切り優先（保守的）
                exit_idx, exit_price, exit_reason = j, stop_price, "stop"
                break
            if hit_stop:
                exit_idx, exit_price, exit_reason = j, stop_price, "stop"
                break
            if hit_target:
                exit_idx, exit_price, exit_reason = j, target_price, "target"
                break
            if j == entry_idx + max_hold:
                exit_idx, exit_price, exit_reason = j, close[j], "timeout"
                break

        ret_pct = exit_price / entry_price - 1.0
        r_multiple = ret_pct / stop_pct  # R = stop_pct

        trades.append(Trade(
            code=code, name=name,
            entry_date=idx[entry_idx], entry_price=float(entry_price),
            exit_date=idx[exit_idx], exit_price=float(exit_price),
            exit_reason=exit_reason,
            ret_pct=float(ret_pct), r_multiple=float(r_multiple),
        ))

        # 手仕舞いの翌日から再スキャン（建玉の重複なし）
        i = exit_idx + 1

    return trades


@dataclass
class BacktestSummary:
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_win_r: float        # 勝ちトレードの平均R（正）
    avg_loss_r: float       # 負けトレードの平均R（正の絶対値）
    expectancy_r_direct: float   # 全トレードの平均R
    expectancy_r_formula: float  # 勝率×平均利益R −(1−勝率)×平均損失R
    max_drawdown_r: float   # 累積RエクイティカーブのMaxDD（R）
    reason_counts: dict     # 手仕舞い理由の内訳


def summarize(trades: List[Trade]) -> BacktestSummary:
    total = len(trades)
    if total == 0:
        return BacktestSummary(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, {})

    wins = [t for t in trades if t.r_multiple > 0]
    losses = [t for t in trades if t.r_multiple < 0]
    n_win, n_loss = len(wins), len(losses)

    win_rate = n_win / total
    avg_win_r = sum(t.r_multiple for t in wins) / n_win if n_win else 0.0
    avg_loss_r = abs(sum(t.r_multiple for t in losses) / n_loss) if n_loss else 0.0

    expectancy_direct = sum(t.r_multiple for t in trades) / total
    expectancy_formula = win_rate * avg_win_r - (1.0 - win_rate) * avg_loss_r

    # --- 最大ドローダウン: 手仕舞い日順の累積Rエクイティカーブ上で算出 ---
    ordered = sorted(trades, key=lambda t: t.exit_date)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in ordered:
        equity += t.r_multiple
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    reason_counts: dict = {}
    for t in trades:
        reason_counts[t.exit_reason] = reason_counts.get(t.exit_reason, 0) + 1

    return BacktestSummary(
        total_trades=total, wins=n_win, losses=n_loss, win_rate=win_rate,
        avg_win_r=avg_win_r, avg_loss_r=avg_loss_r,
        expectancy_r_direct=expectancy_direct, expectancy_r_formula=expectancy_formula,
        max_drawdown_r=max_dd, reason_counts=reason_counts,
    )

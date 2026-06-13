"""ポートフォリオ単位のイベント駆動バックテスト・エンジン。

src/backtest.py の銘柄ごと独立シミュレーションに対し、こちらは全銘柄を
カレンダー順に1日ずつ処理し、以下を扱える:

  - 同時保有数 / 合計リスクの上限（超過分は優先順位で採用、残りは見送り）
  - 現実寄りの約定（ギャップ抜けは始値約定、手数料＋スリッページの控除）

設計の要点（先読み回避）:
  - シグナルは前営業日の終値ベース。エントリーは当日始値。
  - エントリーを当日始値で行った後に、同じ日の高値・安値で手仕舞いを判定する。
  - 手仕舞いで空いたスロットは「翌日」から使える（当日の建玉割当より後に解放）。
  - 1銘柄1ポジション。手仕舞い後にのみ、その後の新しいシグナルで再エントリー可。

制約なし・楽観モードでは src/backtest.py の結果を再現する（検証用）。
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional

import numpy as np
import pandas as pd

from .backtest import Trade, compute_signals
from .indicators import wilder_rsi


class StockData(NamedTuple):
    code: str
    name: str
    dates: np.ndarray          # pd.Timestamp の配列
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    signal: np.ndarray         # bool。各行で3条件成立か
    rsi: np.ndarray            # 優先順位付け用
    date_to_idx: Dict[pd.Timestamp, int]


def prepare_stock(df: pd.DataFrame, rules: dict, code: str, name: str) -> Optional[StockData]:
    """1銘柄分のデータを、エンジンが使う形に前処理する。データ不足なら None。"""
    signals = compute_signals(df, rules)
    if signals is None:
        return None
    rsi = wilder_rsi(df["Close"].astype(float), int(rules["rsi"]["period"]))
    dates = df.index.to_numpy()
    return StockData(
        code=code, name=name, dates=dates,
        open=df["Open"].astype(float).to_numpy(),
        high=df["High"].astype(float).to_numpy(),
        low=df["Low"].astype(float).to_numpy(),
        close=df["Close"].astype(float).to_numpy(),
        signal=signals.to_numpy(),
        rsi=rsi.to_numpy(),
        date_to_idx={d: i for i, d in enumerate(df.index)},
    )


class _Candidate(NamedTuple):
    sid: int
    code: str
    entry_row: int     # 始値で約定する行
    signal_row: int    # シグナルが立った行（entry_row - 1）
    rsi_prev: float    # signal_row の RSI（優先順位付け用）


def run_portfolio(
    stocks: List[StockData],
    rules: dict,
    max_concurrent: int,
    max_total_risk_r: float,
    gap_fill: bool,
    cost_round_trip: float,
    priority: str = "rsi_asc",
    entry_allowed: Optional[set] = None,
) -> List[Trade]:
    """ポートフォリオ・バックテストを実行し、確定トレードのリストを返す。

    entry_allowed: 新規エントリーを許可する日付(pd.Timestamp)の集合。
                   None なら全日許可。許可外の日は新規建てを行わない
                   （既存建玉の手仕舞いは通常どおり実行）。マクロフィルタに使う。
    """
    bt = rules["backtest"]
    stop_pct = float(bt["stop_pct"])
    target_pct = float(bt["target_pct"])
    max_hold = int(bt["max_hold_days"])

    # --- カレンダー（全銘柄の取引日の和集合） ---
    all_dates = sorted({d for s in stocks for d in s.dates})

    # --- 候補エントリーを日付ごとに用意（entry_row = signal_row + 1） ---
    candidates_by_date: Dict[pd.Timestamp, List[_Candidate]] = {}
    for sid, s in enumerate(stocks):
        n = len(s.close)
        for r in range(1, n):
            if s.signal[r - 1]:
                d = s.dates[r]
                candidates_by_date.setdefault(d, []).append(
                    _Candidate(sid=sid, code=s.code, entry_row=r,
                               signal_row=r - 1, rsi_prev=float(s.rsi[r - 1]))
                )

    # --- 状態 ---
    next_idx = [0] * len(stocks)     # この行以降のシグナルのみ有効（手仕舞い後に前進）
    held = [False] * len(stocks)
    open_positions: Dict[int, dict] = {}   # sid -> ポジション情報
    trades: List[Trade] = []

    half_cost = cost_round_trip / 2.0

    for D in all_dates:
        # === 1) エントリー（当日始値で約定） ===
        cands = candidates_by_date.get(D)
        if cands and (entry_allowed is None or D in entry_allowed):
            elig = [c for c in cands
                    if not held[c.sid] and c.signal_row >= next_idx[c.sid]]
            # 優先順位: RSIが低い順（押し目が深い順）、同値はコードで安定ソート
            if priority == "rsi_asc":
                elig.sort(key=lambda c: (c.rsi_prev, c.code))
            for c in elig:
                if len(open_positions) >= max_concurrent:
                    break
                if (len(open_positions) + 1) > max_total_risk_r + 1e-9:
                    break  # 各建玉1R。合計リスク上限に達したら打ち切り
                s = stocks[c.sid]
                entry_price = s.open[c.entry_row]
                if not (entry_price > 0):
                    continue
                open_positions[c.sid] = dict(
                    entry_row=c.entry_row,
                    entry_price=float(entry_price),
                    stop=entry_price * (1.0 - stop_pct),
                    target=entry_price * (1.0 + target_pct),
                    deadline=min(c.entry_row + max_hold, len(s.close) - 1),
                    is_timeout_deadline=(c.entry_row + max_hold) <= (len(s.close) - 1),
                    entry_date=D,
                )
                held[c.sid] = True

        # === 2) 手仕舞い（当日の高値・安値で判定） ===
        for sid in list(open_positions.keys()):
            pos = open_positions[sid]
            s = stocks[sid]
            r = s.date_to_idx.get(D)
            if r is None or r < pos["entry_row"]:
                continue  # その銘柄が当日非取引、または建玉前

            o, h, l, c_ = s.open[r], s.high[r], s.low[r], s.close[r]
            stop, target = pos["stop"], pos["target"]
            exit_price = None
            reason = None

            if gap_fill and o <= stop:
                exit_price, reason = o, "stop_gap"        # 始値が損切りを下抜け
            elif gap_fill and o >= target:
                exit_price, reason = o, "target_gap"      # 始値が利確を上抜け
            elif l <= stop and h >= target:
                exit_price, reason = stop, "stop"         # 同日両ヒット→損切り優先
            elif l <= stop:
                exit_price, reason = stop, "stop"
            elif h >= target:
                exit_price, reason = target, "target"
            elif r >= pos["deadline"]:
                exit_price = c_
                reason = "timeout" if pos["is_timeout_deadline"] else "eod"

            if exit_price is None:
                continue

            entry_price = pos["entry_price"]
            # 手数料＋スリッページ: 買いは割高、売りは割安に約定
            net_exit = exit_price * (1.0 - half_cost)
            net_entry = entry_price * (1.0 + half_cost)
            net_ret = net_exit / net_entry - 1.0
            r_multiple = net_ret / stop_pct

            trades.append(Trade(
                code=s.code, name=s.name,
                entry_date=pd.Timestamp(pos["entry_date"]),
                entry_price=entry_price,
                exit_date=pd.Timestamp(D), exit_price=float(exit_price),
                exit_reason=reason, ret_pct=float(net_ret), r_multiple=float(r_multiple),
            ))
            next_idx[sid] = r + 1
            held[sid] = False
            del open_positions[sid]

    return trades


def equity_curve(trades: List[Trade]) -> pd.DataFrame:
    """手仕舞い日順の累積R・ドローダウン(R)の時系列を返す。

    同一日に複数手仕舞いがある場合は合算して1点にまとめる。
    返り値の列: date, daily_r, cum_r, peak_r, drawdown_r
    """
    if not trades:
        return pd.DataFrame(columns=["date", "daily_r", "cum_r", "peak_r", "drawdown_r"])

    by_date: Dict[pd.Timestamp, float] = {}
    for t in trades:
        by_date[t.exit_date] = by_date.get(t.exit_date, 0.0) + t.r_multiple

    dates = sorted(by_date.keys())
    rows = []
    cum = 0.0
    peak = 0.0
    for d in dates:
        cum += by_date[d]
        peak = max(peak, cum)
        rows.append({
            "date": d, "daily_r": by_date[d], "cum_r": cum,
            "peak_r": peak, "drawdown_r": peak - cum,
        })
    return pd.DataFrame(rows)

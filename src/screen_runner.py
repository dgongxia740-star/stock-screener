"""ダッシュボード用：スクリーニングを構造化データで返すランナー。

screen.py（CLI）は変更せず、こちらはダッシュボードが使う形（JSON的なdict）で
結果を返す。戦略の3条件は既存 indicators/screener をそのまま利用し、
損切り/利確/株数は紙トレード・ルール（holdings.paper_trade_levels）で計算する。
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from . import config_loader, indicators, macro_filter, screener
from . import holdings as hd
from .data_fetcher import DataFetcher


def _macro_to_dict(macro, rules: dict) -> dict:
    """MacroResult を表示用 dict に変換し、新規建ての総合可否を付ける。"""
    m = rules["macro"]
    vix_warn = float(m.get("vix_warn", 25.0))
    vix_halt = float(m.get("vix_halt", 30.0))

    # 総合判定: VIX警戒(>halt)→停止 / VIX高(>warn) or 日経200日線下→縮小 / それ以外→OK
    new_entry = "ok"
    if macro.vix is not None and macro.vix > vix_halt:
        new_entry = "halt"
    elif (macro.vix is not None and macro.vix > vix_warn) or macro.n225_status == "200日線の下":
        new_entry = "reduce"

    return {
        "warnings": list(macro.warnings),
        "n225_close": macro.n225_close, "n225_ma": macro.n225_ma, "n225_status": macro.n225_status,
        "vix": macro.vix, "vix_status": macro.vix_status,
        "new_entry": new_entry,
    }


def _classify_not_passed(chk: dict, ind, rules: dict) -> str:
    """3条件を満たさない銘柄を、買い時への近さで分類する（表示用ラベル）。

    - 押し目待ち : 上昇トレンド＋出来高OK だが RSIがまだ高い（上限超）＝下げてくれば候補
    - あと一歩   : ちょうど2条件成立（押し目待ち以外）
    - 不通過     : 0〜1条件（主に25MAが下向き＝トレンド外）
    """
    n = int(chk["trend"]) + int(chk["rsi"]) + int(chk["volume"])
    rsi_max = float(rules["rsi"]["max"])
    if chk["trend"] and chk["volume"] and not chk["rsi"] and ind.rsi > rsi_max:
        return "押し目待ち"
    if n == 2:
        return "あと一歩"
    return "不通過"


def _history(df: pd.DataFrame, ma_window: int, days: int = 70) -> list:
    """チャート用に末尾 days 本の 終値・25日線 を返す。"""
    ma = df["Close"].astype(float).rolling(ma_window, min_periods=ma_window).mean()
    tail = df.tail(days)
    ma_tail = ma.tail(days)
    out = []
    for idx, close, m in zip(tail.index, tail["Close"], ma_tail):
        out.append({
            "date": idx.date().isoformat(),
            "close": round(float(close), 1),
            "ma25": (round(float(m), 1) if pd.notna(m) else None),
        })
    return out


def screen_universe(rules: Optional[dict] = None, today: Optional[date] = None) -> dict:
    """日経225全銘柄をスクリーニングし、ダッシュボード用 dict を返す。"""
    rules = rules or config_loader.load_rules()
    today = today or date.today()
    stocks = config_loader.load_universe()
    name_map = config_loader.build_name_map(stocks)
    fetcher = DataFetcher(rules, today=today)
    ma_window = int(rules["trend"]["ma_window"])

    candidates, skipped, failures = [], [], []
    all_rows = []  # 全225銘柄の状況（一覧表用）

    def _row(code, name, status, ind=None, chk=None):
        """一覧表の1行を作る（取得失敗・データ不足は値None）。"""
        if ind is None:
            return {"code": code, "name": name, "status": status,
                    "close": None, "rsi": None, "volume_ratio": None,
                    "ma_slope_up": None, "cond_trend": None, "cond_rsi": None,
                    "cond_volume": None, "n_conditions": None}
        return {"code": code, "name": name, "status": status,
                "close": round(ind.close, 1), "rsi": round(ind.rsi, 1),
                "volume_ratio": round(ind.volume_ratio, 2), "ma_slope_up": ind.ma_slope_up,
                "cond_trend": chk["trend"], "cond_rsi": chk["rsi"], "cond_volume": chk["volume"],
                "n_conditions": int(chk["trend"]) + int(chk["rsi"]) + int(chk["volume"])}

    for st in stocks:
        code, name = st.code, name_map.get(st.code, st.code)
        try:
            df = fetcher.fetch(code)
        except Exception:
            df = None
        if df is None:
            failures.append(code)
            all_rows.append(_row(code, name, "取得失敗"))
            continue

        ind = indicators.compute(df, rules)
        if ind is None:
            skipped.append(code)
            all_rows.append(_row(code, name, "データ不足"))
            continue

        chk = screener.check(ind, rules)
        if not chk["passed"]:
            all_rows.append(_row(code, name, _classify_not_passed(chk, ind, rules), ind, chk))
            continue

        # 3条件成立 → 紙トレード・ルールで建玉レベルを計算
        stop, target, shares, _ = hd.paper_trade_levels(ind.close, rules)
        reason = (f"25日線が上向き ＋ RSI{ind.rsi:.0f}（押し目）"
                  f" ＋ 出来高{ind.volume_ratio:.1f}倍 → 3条件成立")
        candidates.append({
            "code": code, "name": name,
            "close": round(ind.close, 1), "ma25": round(ind.ma, 1),
            "ma_slope_up": ind.ma_slope_up, "rsi": round(ind.rsi, 1),
            "volume_ratio": round(ind.volume_ratio, 2),
            "stop_price": round(stop, 1), "target_price": round(target, 1), "shares": shares,
            "reason": reason,
            "tradeable": shares > 0,  # True=買い時 / False=見送り（資金で建てられない）
            "history": _history(df, ma_window),
        })
        all_rows.append(_row(code, name, "買い時" if shares > 0 else "見送り", ind, chk))

    macro = macro_filter.evaluate(fetcher, rules)
    return {
        "date": today.isoformat(),
        "macro": _macro_to_dict(macro, rules),
        "candidates": candidates,
        "skipped": skipped,
        "failures": failures,
        "all_rows": all_rows,
    }


def get_history(rules: dict, code: str, today: Optional[date] = None, days: int = 80):
    """保有銘柄のチャート＆最新OHLC用。df（末尾days本, MA25付き）を返す。失敗時 None。"""
    fetcher = DataFetcher(rules, today=today or date.today())
    try:
        df = fetcher.fetch(code)
    except Exception:
        df = None
    if df is None or df.empty:
        return None
    df = df.copy()
    df["MA25"] = df["Close"].astype(float).rolling(int(rules["trend"]["ma_window"]),
                                                   min_periods=int(rules["trend"]["ma_window"])).mean()
    return df.tail(days)

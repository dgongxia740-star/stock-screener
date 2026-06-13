"""保有銘柄（紙トレード）の管理。

データは data/holdings.json に保存。1件＝1ポジション。
損切り/利確/期限/株数は紙トレード・ルール（-7%固定・+15%・20営業日・リスク0.5%）で計算する。
※ 実発注機能は持たない（記録のみ）。
"""

from __future__ import annotations

import json
import math
import os
from datetime import date
from typing import List, Optional

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
HOLDINGS_PATH = os.path.join(DATA_DIR, "holdings.json")


# ---------------------------------------------------------------------------
def paper_trade_levels(entry_price: float, rules: dict):
    """紙トレード・ルールで 損切り価格・利確価格・株数 を計算する。

    返り値: (stop_price, target_price, shares, risk_per_share)
    """
    bt = rules["backtest"]
    stop_pct = float(bt["stop_pct"])
    target_pct = float(bt["target_pct"])
    capital = float(rules["risk"]["total_capital"])
    ratio = float(rules["risk"]["risk_ratio"])

    stop_price = entry_price * (1.0 - stop_pct)
    target_price = entry_price * (1.0 + target_pct)
    risk_per_share = entry_price - stop_price
    shares = int(math.floor(capital * ratio / risk_per_share)) if risk_per_share > 0 else 0
    return stop_price, target_price, max(shares, 0), risk_per_share


def r_multiple(entry_price: float, exit_price: float, rules: dict) -> float:
    """損益をR建てで返す（手数料往復 cost_round_trip を控除）。"""
    bt = rules["backtest"]
    stop_pct = float(bt["stop_pct"])
    c = float(bt.get("cost_round_trip", 0.0))
    net_ret = (exit_price * (1.0 - c / 2.0)) / (entry_price * (1.0 + c / 2.0)) - 1.0
    return net_ret / stop_pct


# ---------------------------------------------------------------------------
def load_holdings() -> List[dict]:
    if not os.path.exists(HOLDINGS_PATH):
        return []
    try:
        with open(HOLDINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_holdings(holdings: List[dict]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HOLDINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(holdings, f, ensure_ascii=False, indent=2)


def has_open_position(code: str) -> bool:
    return any(h["code"] == code and h.get("status") == "open" for h in load_holdings())


def add_holding(code: str, name: str, entry_price: float, entry_date: str, rules: dict) -> dict:
    """新規保有を登録して保存。登録した dict を返す。"""
    stop, target, shares, _ = paper_trade_levels(entry_price, rules)
    h = {
        "code": code, "name": name,
        "entry_date": entry_date, "entry_price": round(float(entry_price), 2),
        "stop_price": round(float(stop), 2), "target_price": round(float(target), 2),
        "shares": shares,
        "max_hold_days": int(rules["backtest"]["max_hold_days"]),
        "status": "open",
        "exit_date": None, "exit_price": None, "exit_reason": None, "pnl_r": None,
    }
    holdings = load_holdings()
    holdings.append(h)
    save_holdings(holdings)
    return h


def close_holding(code: str, entry_date: str, exit_price: float, exit_reason: str,
                  exit_date: str, rules: dict) -> None:
    """指定の建玉（code＋entry_dateで一意）を手仕舞い済みにする。"""
    holdings = load_holdings()
    for h in holdings:
        if h["code"] == code and h["entry_date"] == entry_date and h.get("status") == "open":
            h["status"] = "closed"
            h["exit_price"] = round(float(exit_price), 2)
            h["exit_reason"] = exit_reason
            h["exit_date"] = exit_date
            h["pnl_r"] = round(r_multiple(h["entry_price"], exit_price, rules), 3)
            break
    save_holdings(holdings)


def delete_holding(code: str, entry_date: str) -> None:
    """誤登録の削除用。"""
    holdings = [h for h in load_holdings()
                if not (h["code"] == code and h["entry_date"] == entry_date)]
    save_holdings(holdings)


# ---------------------------------------------------------------------------
def business_days_held(entry_date: str, today: date) -> int:
    """エントリー日から today までの営業日数（エントリー日を0日目とする）。"""
    try:
        return int(np.busday_count(entry_date, today.isoformat()))
    except Exception:
        return 0


def judge_holding(h: dict, latest_high: float, latest_low: float, latest_close: float,
                  today: date, rules: dict) -> dict:
    """open の建玉について、今日のデータで売り時/保留を判定する。

    返り値: {"status": "sell"|"hold", "reason": str, "suggested_exit": float,
             "exit_reason": "stop"|"target"|"timeout"|None, "mark_r": float}
    判定優先: 損切り → 利確 → 期限（同日に損切り・利確が両立なら損切り優先）。
    """
    stop = h["stop_price"]
    target = h["target_price"]
    held = business_days_held(h["entry_date"], today)
    max_hold = int(h.get("max_hold_days", rules["backtest"]["max_hold_days"]))
    mark_r = round(r_multiple(h["entry_price"], latest_close, rules), 2)

    if latest_low <= stop:
        return {"status": "sell", "reason": f"損切り価格 {stop:,.0f} に到達",
                "suggested_exit": stop, "exit_reason": "stop", "mark_r": mark_r}
    if latest_high >= target:
        return {"status": "sell", "reason": f"利確 +15%（{target:,.0f}）に到達",
                "suggested_exit": target, "exit_reason": "target", "mark_r": mark_r}
    if held >= max_hold:
        return {"status": "sell", "reason": f"登録から {held} 営業日経過（期限{max_hold}）",
                "suggested_exit": latest_close, "exit_reason": "timeout", "mark_r": mark_r}
    return {"status": "hold",
            "reason": f"損切り/利確/期限いずれも未到達（{held}営業日経過・含み {mark_r:+.2f}R）",
            "suggested_exit": latest_close, "exit_reason": None, "mark_r": mark_r}

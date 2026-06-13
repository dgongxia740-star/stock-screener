"""設定ファイル（rules.yaml / universe.txt）の読み込みと検証。"""

from __future__ import annotations

import os
from typing import Dict, List, NamedTuple

import yaml

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")


class Stock(NamedTuple):
    code: str       # 例: "7203.T"
    name: str       # 例: "トヨタ自動車"（universe.txt のコメント、無ければコードと同じ）


def load_rules(path: str | None = None) -> dict:
    """rules.yaml を読み込んで dict を返す。最低限のキー存在チェックを行う。"""
    path = path or os.path.join(CONFIG_DIR, "rules.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"設定ファイルが見つかりません: {path}")

    with open(path, "r", encoding="utf-8") as f:
        rules = yaml.safe_load(f) or {}

    required_sections = ["data", "trend", "rsi", "volume", "risk", "stop_loss", "macro"]
    missing = [s for s in required_sections if s not in rules]
    if missing:
        raise ValueError(f"rules.yaml に必要なセクションがありません: {', '.join(missing)}")

    return rules


def load_universe(path: str | None = None) -> List[Stock]:
    """universe.txt を読み込み、Stock のリストを返す。

    形式:  "7203.T  # トヨタ自動車"
    - 行頭が "#" の行は無視（ファイル全体のコメント）
    - コード右側の "# 名称" は銘柄名として使用
    """
    path = path or os.path.join(CONFIG_DIR, "universe.txt")
    if not os.path.exists(path):
        raise FileNotFoundError(f"銘柄リストが見つかりません: {path}")

    stocks: List[Stock] = []
    seen: set[str] = set()

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            # コードと（任意の）コメント名を分離
            if "#" in line:
                code_part, _, name_part = line.partition("#")
                code = code_part.strip()
                name = name_part.strip()
            else:
                code = line
                name = ""

            if not code:
                continue
            if code in seen:
                continue  # 重複コードはスキップ
            seen.add(code)

            stocks.append(Stock(code=code, name=name or code))

    if not stocks:
        raise ValueError(f"有効な銘柄が1つもありません: {path}")

    return stocks


def build_name_map(stocks: List[Stock]) -> Dict[str, str]:
    """コード -> 名称 の辞書を作る。"""
    return {s.code: s.name for s in stocks}

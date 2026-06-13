"""yfinance からの日足データ取得。

- バッチ的に1銘柄ずつ取得（銘柄間にウェイトを入れてAPI負荷を軽減）
- 取得失敗は指数バックオフでリトライ
- 当日分のローカルキャッシュがあれば再取得しない
- 取得失敗・データ欠損は例外を投げず None を返し、呼び出し側でスキップ可能にする
"""

from __future__ import annotations

import os
import time
from datetime import date
from typing import Optional

import pandas as pd

try:
    import yfinance as yf
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "yfinance がインストールされていません。`pip install -r requirements.txt` を実行してください。"
    ) from e

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")


class DataFetcher:
    """日足データ取得＋日次キャッシュ。"""

    def __init__(self, rules: dict, cache_dir: str = CACHE_DIR, today: Optional[date] = None,
                 period: Optional[str] = None):
        data_cfg = rules.get("data", {})
        # period 引数があれば data.period を上書き（バックテストの長期取得などで使用）。
        self.period: str = period or data_cfg.get("period", "300d")
        self.max_retries: int = int(data_cfg.get("max_retries", 3))
        self.retry_wait: float = float(data_cfg.get("retry_wait_sec", 2))
        self.request_pause: float = float(data_cfg.get("request_pause_sec", 0.5))
        self.cache_dir = cache_dir
        self.today = today or date.today()
        os.makedirs(self.cache_dir, exist_ok=True)
        self._network_calls = 0  # 実際にネットワーク取得した回数（ペーシング用）

    # ------------------------------------------------------------------
    def _cache_path(self, symbol: str) -> str:
        # 取得期間が異なるとデータ長も変わるため、period をキーに含めて
        # ライブ(300d)とバックテスト(540d)のキャッシュ衝突を防ぐ。
        safe = symbol.replace("^", "_idx_").replace("/", "_")
        return os.path.join(self.cache_dir, f"{safe}_{self.period}_{self.today.isoformat()}.pkl")

    def _load_cache(self, symbol: str) -> Optional[pd.DataFrame]:
        path = self._cache_path(symbol)
        if os.path.exists(path):
            try:
                df = pd.read_pickle(path)
                if isinstance(df, pd.DataFrame) and not df.empty:
                    return df
            except Exception:
                # 壊れたキャッシュは無視して再取得
                return None
        return None

    def _save_cache(self, symbol: str, df: pd.DataFrame) -> None:
        try:
            df.to_pickle(self._cache_path(symbol))
        except Exception:
            # キャッシュ保存失敗は致命的ではないので握りつぶす
            pass

    # ------------------------------------------------------------------
    def fetch(self, symbol: str, use_cache: bool = True) -> Optional[pd.DataFrame]:
        """1銘柄の日足を取得。

        返り値: 列 [Open, High, Low, Close, Volume] を持つ DataFrame、
                取得失敗・空データなら None。
        """
        if use_cache:
            cached = self._load_cache(symbol)
            if cached is not None:
                return cached  # キャッシュヒット時はネットワークを使わないのでウェイト不要

        # ここから先は実ネットワーク取得。API負荷軽減のため、2回目以降の
        # ネットワーク呼び出しの前にだけウェイトを入れる（先頭は待たない）。
        if self._network_calls > 0 and self.request_pause > 0:
            time.sleep(self.request_pause)
        self._network_calls += 1

        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                ticker = yf.Ticker(symbol)
                df = ticker.history(period=self.period, interval="1d", auto_adjust=False)

                df = self._normalize(df)
                if df is not None and not df.empty:
                    self._save_cache(symbol, df)
                    return df
                # 空データ（休場・上場前など）は取得失敗扱いだがリトライしても無駄なので break
                last_err = ValueError("空のデータが返されました")
                break
            except Exception as e:  # ネットワーク・レート制限など
                last_err = e
                if attempt < self.max_retries:
                    wait = self.retry_wait * (2 ** (attempt - 1))  # 指数バックオフ
                    time.sleep(wait)

        # ここに来たら全リトライ失敗
        _ = last_err  # 呼び出し側でログするために None を返すだけにとどめる
        return None

    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """yfinance の戻りを整形し、欠損行を除去する。"""
        if df is None or df.empty:
            return None

        # MultiIndex 列（複数銘柄一括取得時など）の保険
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        needed = ["Open", "High", "Low", "Close", "Volume"]
        for col in needed:
            if col not in df.columns:
                return None

        df = df[needed].copy()

        # 休場日・配信欠損で Close が NaN の行を除去（データ欠損ハンドリング）
        df = df.dropna(subset=["Close"])
        # 出来高 NaN は 0 として扱う（指数などは出来高が無いことがある）
        df["Volume"] = df["Volume"].fillna(0)

        if df.empty:
            return None
        return df

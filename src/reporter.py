"""結果のターミナル表示とCSV保存。

rich が使えれば整形テーブル、無ければ素のテキストにフォールバックする。
"""

from __future__ import annotations

import csv
import os
from datetime import date
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    # 型注釈のみ。実行時に import しないことで、出力層が yfinance 依存の
    # data_fetcher（macro_filter経由）を引き込まないようにする。
    from .macro_filter import MacroResult

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

try:
    from rich.console import Console
    from rich.table import Table
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False


# CSV / 表示に使う列順
CSV_HEADER = [
    "code", "name", "close", "ma25", "ma25_slope", "ma25_slope_up",
    "rsi14", "last_volume", "avg_volume20", "volume_ratio",
    "stop_loss", "risk_per_share", "shares", "note",
]


def _macro_lines(macro: MacroResult) -> List[str]:
    lines: List[str] = []
    if macro.warnings:
        lines.extend(macro.warnings)
    # 参考情報
    if macro.n225_close is not None and macro.n225_ma is not None:
        lines.append(
            f"日経平均: 終値 {macro.n225_close:,.0f} / 200日線 {macro.n225_ma:,.0f}（{macro.n225_status}）"
        )
    else:
        lines.append("日経平均: 取得できず（判定不能）")
    if macro.vix is not None:
        lines.append(f"VIX: {macro.vix:.2f}（{macro.vix_status}）")
    else:
        lines.append("VIX: 取得できず（判定不能）")
    return lines


def render(
    passed: List[dict],
    macro: MacroResult,
    failures: List[dict],
    skipped: List[dict],
    target_date: date,
) -> None:
    """ターミナルへ表示。"""
    macro_lines = _macro_lines(macro)

    if _HAS_RICH:
        console = Console()
        console.rule(f"[bold]スイングトレード・スクリーニング結果  {target_date.isoformat()}[/bold]")
        for line in macro_lines:
            style = "bold red" if line.startswith("⚠") else "dim"
            console.print(line, style=style)
        console.print("")

        if passed:
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("コード")
            table.add_column("名称")
            table.add_column("終値", justify="right")
            table.add_column("25MA", justify="right")
            table.add_column("傾き", justify="center")
            table.add_column("RSI14", justify="right")
            table.add_column("出来高比", justify="right")
            table.add_column("損切り", justify="right")
            table.add_column("株数", justify="right")
            table.add_column("備考")
            for r in passed:
                arrow = "↑" if r["ma25_slope_up"] else "↓"
                table.add_row(
                    r["code"], r["name"],
                    f"{r['close']:,.1f}",
                    f"{r['ma25']:,.1f}",
                    arrow,
                    f"{r['rsi14']:.1f}",
                    f"{r['volume_ratio']:.2f}x",
                    f"{r['stop_loss']:,.1f}",
                    f"{r['shares']:,}",
                    r["note"],
                )
            console.print(table)
        else:
            console.print("条件を通過した銘柄はありませんでした。", style="yellow")

        if skipped:
            console.print(f"\nデータ不足でスキップ: {len(skipped)}件 "
                          + ", ".join(s["code"] for s in skipped), style="dim")
        if failures:
            console.print(f"取得失敗: {len(failures)}件 "
                          + ", ".join(f["code"] for f in failures), style="dim red")
    else:
        # --- フォールバック（rich無し） ---
        print("=" * 70)
        print(f"スイングトレード・スクリーニング結果  {target_date.isoformat()}")
        print("=" * 70)
        for line in macro_lines:
            print(line)
        print("")
        if passed:
            print(f"{'コード':<9}{'終値':>9}{'25MA':>9}{'傾':>3}{'RSI':>6}{'出来高比':>8}{'損切り':>9}{'株数':>8}  備考")
            for r in passed:
                arrow = "↑" if r["ma25_slope_up"] else "↓"
                print(f"{r['code']:<9}{r['close']:>9,.1f}{r['ma25']:>9,.1f}{arrow:>3}"
                      f"{r['rsi14']:>6.1f}{r['volume_ratio']:>7.2f}x{r['stop_loss']:>9,.1f}"
                      f"{r['shares']:>8,}  {r['note']}")
        else:
            print("条件を通過した銘柄はありませんでした。")
        if skipped:
            print(f"\nデータ不足でスキップ: {len(skipped)}件 " + ", ".join(s["code"] for s in skipped))
        if failures:
            print(f"取得失敗: {len(failures)}件 " + ", ".join(f["code"] for f in failures))


def save_csv(passed: List[dict], target_date: date, data_dir: Optional[str] = None) -> str:
    """通過銘柄を data/screen_YYYY-MM-DD.csv に保存。パスを返す。"""
    data_dir = data_dir or DATA_DIR
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"screen_{target_date.isoformat()}.csv")

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for r in passed:
            writer.writerow({
                "code": r["code"],
                "name": r["name"],
                "close": round(r["close"], 2),
                "ma25": round(r["ma25"], 2),
                "ma25_slope": round(r["ma25_slope"], 2),
                "ma25_slope_up": r["ma25_slope_up"],
                "rsi14": round(r["rsi14"], 2),
                "last_volume": int(r["last_volume"]),
                "avg_volume20": int(r["avg_volume"]),
                "volume_ratio": round(r["volume_ratio"], 3),
                "stop_loss": round(r["stop_loss"], 2),
                "risk_per_share": round(r["risk_per_share"], 2),
                "shares": r["shares"],
                "note": r["note"],
            })
    return path

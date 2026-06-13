#!/usr/bin/env python3
"""スイングトレード・スクリーニング ダッシュボード（Streamlit / 紙トレード専用）。

起動:
    streamlit run dashboard.py

戦略ルール（3条件・損切り-7%・利確+15%・20営業日・リスク0.5%）は変更しない。
表示と保有管理（紙トレードの記録）を提供するのみ。実発注機能は持たない。
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from src import config_loader
from src import holdings as hd
from src import screen_runner

st.set_page_config(page_title="スイングトレード ダッシュボード", page_icon="📈", layout="wide")

RULES = config_loader.load_rules()
TODAY = date.today()


# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_screen(day: str) -> dict:
    """日付をキーにスクリーニング結果をキャッシュ（同日は再取得しない）。"""
    return screen_runner.screen_universe(RULES, TODAY)


@st.cache_data(show_spinner=False)
def load_history(code: str, day: str):
    df = screen_runner.get_history(RULES, code, TODAY)
    return df


def mini_chart(history_records, height: int = 140):
    """終値＋25日線の小チャート。history_records は dict のリスト。"""
    if not history_records:
        return
    df = pd.DataFrame(history_records)
    df = df.rename(columns={"close": "終値", "ma25": "25日線"}).set_index("date")
    st.line_chart(df[["終値", "25日線"]], height=height)


def chart_from_df(df, height: int = 140):
    if df is None or df.empty:
        return
    d = pd.DataFrame({"終値": df["Close"], "25日線": df["MA25"]})
    d.index = [i.date().isoformat() for i in df.index]
    st.line_chart(d, height=height)


# ==========================================================================
# ヘッダー
# ==========================================================================
st.title("📈 スイングトレード・スクリーニング ダッシュボード")
st.caption(f"{TODAY.isoformat()} ｜ 紙トレード（記録）専用 ・ これは投資助言ではなく、"
           "事前に定義したルールに合致する銘柄を機械的に抽出するものです。"
           "yfinanceは15〜20分遅延・日足ベース。")

if st.button("🔄 今日のデータを再取得（キャッシュ更新）"):
    load_screen.clear()
    load_history.clear()
    st.rerun()

with st.spinner("225銘柄をスクリーニング中…（初回はデータ取得に数分かかります）"):
    result = load_screen(TODAY.isoformat())

macro = result["macro"]

# ==========================================================================
# ① 今日の相場状況
# ==========================================================================
st.header("① 今日の相場状況")
c1, c2, c3 = st.columns(3)
with c1:
    if macro["n225_close"] is not None:
        st.metric("日経平均（終値）", f"{macro['n225_close']:,.0f}",
                  f"200日線 {macro['n225_ma']:,.0f}（{macro['n225_status']}）")
    else:
        st.metric("日経平均", "取得できず")
with c2:
    if macro["vix"] is not None:
        st.metric("VIX", f"{macro['vix']:.1f}", macro["vix_status"])
    else:
        st.metric("VIX", "取得できず")
with c3:
    label = {"ok": "🟢 新規建て OK", "reduce": "🟡 新規 縮小/半減",
             "halt": "🔴 新規 停止"}[macro["new_entry"]]
    st.metric("新規建ての可否", label)

if macro["new_entry"] == "ok":
    st.success("地合いは新規建てOK（日経が200日線の上・VIX平常）。")
else:
    for w in macro["warnings"]:
        st.warning(w)
    st.warning("⚠ マクロフィルタ：このような日は新規建てを控える方針です（保有の手仕舞いは通常どおり）。")

st.divider()

# ==========================================================================
# ② 保有中（紙トレード）
# ==========================================================================
st.header("② 保有中（紙トレード）")

open_holdings = [h for h in hd.load_holdings() if h.get("status") == "open"]
if not open_holdings:
    st.info("保有中の銘柄はありません。下の「③ 買い時」から登録できます。")
else:
    for h in open_holdings:
        df = load_history(h["code"], TODAY.isoformat())
        if df is None or df.empty:
            st.warning(f"{h['code']} {h['name']}：最新データを取得できず判定できません。")
            continue
        last = df.iloc[-1]
        j = hd.judge_holding(h, float(last["High"]), float(last["Low"]), float(last["Close"]),
                             TODAY, RULES)
        badge = "🔴 売り時" if j["status"] == "sell" else "🟡 保留"
        with st.container(border=True):
            top, chart = st.columns([3, 2])
            with top:
                st.markdown(f"### {badge} ｜ {h['code']} {h['name']}")
                st.write(f"**判定理由**：{j['reason']}")
                st.caption(f"買値 {h['entry_price']:,.0f}（{h['entry_date']}）／ "
                           f"損切り {h['stop_price']:,.0f}（-7%）／ 利確 {h['target_price']:,.0f}（+15%）／ "
                           f"{h['shares']}株 ／ 含み損益 {j['mark_r']:+.2f}R")
                col_a, col_b = st.columns(2)
                with col_a:
                    exit_price = st.number_input(
                        "手仕舞い価格", value=float(round(j["suggested_exit"], 1)),
                        key=f"exit_{h['code']}_{h['entry_date']}", step=1.0)
                with col_b:
                    if st.button("✅ この値で手仕舞い記録",
                                 key=f"close_{h['code']}_{h['entry_date']}",
                                 type="primary" if j["status"] == "sell" else "secondary"):
                        reason = j["exit_reason"] or "manual"
                        hd.close_holding(h["code"], h["entry_date"], exit_price, reason,
                                         TODAY.isoformat(), RULES)
                        load_screen.clear()
                        st.rerun()
            with chart:
                chart_from_df(df)

st.divider()

# ==========================================================================
# ③ 買い時 ／ ④ 見送り
# ==========================================================================
buy = [c for c in result["candidates"] if c["tradeable"]]
skip = [c for c in result["candidates"] if not c["tradeable"]]

st.header(f"③ 買い時（今日の新規候補）　{len(buy)}件")
if macro["new_entry"] == "halt":
    st.error("⚠ 今日は新規停止の地合いです。下の候補は参考表示のみ（登録は推奨しません）。")
elif macro["new_entry"] == "reduce":
    st.warning("⚠ 今日は新規縮小の地合いです。登録は慎重に。")

if not buy:
    st.info("今日、3条件を満たす新規候補（建てられるもの）はありません。")
else:
    for c in buy:
        already = hd.has_open_position(c["code"])
        with st.container(border=True):
            top, chart = st.columns([3, 2])
            with top:
                st.markdown(f"### 🟢 買い時 ｜ {c['code']} {c['name']}")
                st.write(f"**根拠**：{c['reason']}")
                st.caption(f"終値 {c['close']:,.0f}／25日線 {c['ma25']:,.0f}／"
                           f"損切り目安 {c['stop_price']:,.0f}（-7%）／利確 {c['target_price']:,.0f}（+15%）／"
                           f"目安 {c['shares']}株")
                if already:
                    st.success("すでに保有中に登録済みです。")
                else:
                    col_a, col_b = st.columns(2)
                    with col_a:
                        buy_price = st.number_input(
                            "買値（既定=終値。実際の寄り値に直してOK）",
                            value=float(c["close"]), key=f"buy_{c['code']}", step=1.0)
                    with col_b:
                        if st.button("➕ 保有中（紙）に登録", key=f"add_{c['code']}", type="primary"):
                            hd.add_holding(c["code"], c["name"], buy_price, TODAY.isoformat(), RULES)
                            st.rerun()
            with chart:
                mini_chart(c["history"])

st.subheader(f"④ 非推奨・見送り　{len(skip)}件")
st.caption("3条件は成立しているが、資金（総資金×リスク割合）で1株分のリスク許容に届かず建てられない銘柄。")
if not skip:
    st.write("該当なし。")
else:
    for c in skip:
        with st.container(border=True):
            st.markdown(f"**⚪ 見送り ｜ {c['code']} {c['name']}** … {c['reason']}")
            st.caption(f"終値 {c['close']:,.0f}／損切り目安 {c['stop_price']:,.0f}／"
                       f"必要資金が大きく {c['shares']}株（建て見送り）")

# ==========================================================================
# ⑤ 取引履歴・通算成績
# ==========================================================================
st.divider()
st.header("⑤ 取引履歴・通算成績（紙トレード）")
closed = [h for h in hd.load_holdings() if h.get("status") == "closed"]
if not closed:
    st.info("手仕舞い済みの記録はまだありません。")
else:
    rs = [h["pnl_r"] for h in closed if h.get("pnl_r") is not None]
    wins = [r for r in rs if r > 0]
    total_r = sum(rs)
    win_rate = (len(wins) / len(rs) * 100) if rs else 0.0
    m1, m2, m3 = st.columns(3)
    m1.metric("確定トレード数", f"{len(closed)}")
    m2.metric("勝率", f"{win_rate:.0f}%")
    m3.metric("累計損益", f"{total_r:+.2f} R")
    hist = pd.DataFrame([{
        "コード": h["code"], "名称": h["name"],
        "買値": h["entry_price"], "買日": h["entry_date"],
        "手仕舞い": h["exit_price"], "売日": h["exit_date"],
        "理由": h["exit_reason"], "損益R": h["pnl_r"],
    } for h in closed])
    st.dataframe(hist, width="stretch", hide_index=True)

if result["failures"] or result["skipped"]:
    st.caption(f"（参考）取得失敗 {len(result['failures'])}件／データ不足スキップ "
               f"{len(result['skipped'])}件")

# ==========================================================================
# ⑥ 全225銘柄の状況（折りたたみ）
# ==========================================================================
st.divider()
with st.expander("⑥ 全225銘柄の状況を見る（検索・並べ替え可）"):
    st.caption("各銘柄が今日の3条件をどれだけ満たしているか。"
               "列名をクリックで並べ替え、下の検索/フィルタで絞り込めます。"
               "（抽出ルールは変えていません。表示のみ）")

    def mark(v):
        if v is None:
            return "—"
        return "○" if v else "×"

    all_rows = result.get("all_rows")
    if not all_rows:
        st.info("一覧データがまだありません。上部の「🔄 今日のデータを再取得（キャッシュ更新）」を"
                "押すと表示されます（コード更新後はキャッシュ更新が必要です）。")
        st.stop()

    rows = []
    for r in all_rows:
        rows.append({
            "コード": r["code"], "名称": r["name"], "ステータス": r["status"],
            "満たした条件数": r["n_conditions"],
            "25MA上向き": mark(r["ma_slope_up"]),
            "RSI(35-45)": mark(r["cond_rsi"]),
            "出来高(≥20日平均)": mark(r["cond_volume"]),
            "RSI値": r["rsi"], "出来高比": r["volume_ratio"], "終値": r["close"],
        })
    df_all = pd.DataFrame(rows)

    fcol1, fcol2 = st.columns([2, 2])
    with fcol1:
        q = st.text_input("コード・名称で検索", "", placeholder="例: 7203 / トヨタ / Sony")
    with fcol2:
        statuses = ["買い時", "見送り", "押し目待ち", "あと一歩", "不通過", "データ不足", "取得失敗"]
        chosen = st.multiselect("ステータスで絞り込み", statuses, default=statuses)

    view = df_all[df_all["ステータス"].isin(chosen)]
    if q.strip():
        ql = q.strip().lower()
        view = view[view["コード"].str.lower().str.contains(ql)
                    | view["名称"].str.lower().str.contains(ql)]

    st.caption(f"表示 {len(view)} 件 / 全 {len(df_all)} 件")
    st.dataframe(view, width="stretch", hide_index=True)

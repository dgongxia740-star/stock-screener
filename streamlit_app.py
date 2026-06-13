#!/usr/bin/env python3
"""クラウド閲覧用ダッシュボード（Streamlit Community Cloud のエントリポイント）。

特徴:
  - データ取得はしない。GitHub Actions が作った data/latest_screen.json を読むだけ（高速・安定）。
  - パスワード保護（st.secrets["app_password"]）。
  - 閲覧専用（保有管理・紙トレード記録は手元の dashboard.py で行う）。

ローカル確認:
    streamlit run streamlit_app.py
クラウド:
    Streamlit Community Cloud で本ファイルをメインに指定してデプロイ。
"""

from __future__ import annotations

import json
import os

import pandas as pd
import streamlit as st

st.set_page_config(page_title="スクリーニング（閲覧）", page_icon="📈", layout="wide")

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "latest_screen.json")


# --------------------------------------------------------------------------
def check_password() -> bool:
    """st.secrets の app_password と一致したら True。未設定なら警告のうえ通す。"""
    try:
        configured = st.secrets.get("app_password")
    except Exception:
        configured = None

    if not configured:
        st.warning("⚠ パスワード未設定（誰でも閲覧できる状態）。Streamlit Cloud の Settings → Secrets に "
                   "app_password を設定してください。")
        return True

    if st.session_state.get("auth_ok"):
        return True

    def _verify():
        st.session_state["auth_ok"] = (st.session_state.get("pw_input") == configured)

    st.text_input("パスワード", type="password", key="pw_input", on_change=_verify)
    if st.session_state.get("auth_ok") is False:
        st.error("パスワードが違います。")
    return False


@st.cache_data(show_spinner=False)
def load_data():
    if not os.path.exists(DATA_PATH):
        return None
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def mini_chart(history, height: int = 140):
    if not history:
        return
    df = pd.DataFrame(history).rename(columns={"close": "終値", "ma25": "25日線"}).set_index("date")
    st.line_chart(df[["終値", "25日線"]], height=height)


# ==========================================================================
if not check_password():
    st.stop()

st.title("📈 スイングトレード・スクリーニング（閲覧用）")

result = load_data()
if result is None:
    st.info("まだデータがありません。毎営業日の自動更新（GitHub Actions）が走ると表示されます。")
    st.stop()

st.caption(f"データ基準日 {result.get('date','?')} ／ 更新 {result.get('generated_at','?')} ｜ "
           "閲覧専用 ・ 投資助言ではなく事前定義ルールの機械的抽出 ・ yfinanceは15〜20分遅延・日足。"
           "保有管理（紙トレード記録）は手元アプリで行います。")
if st.button("🔄 表示を最新化"):
    load_data.clear()
    st.rerun()

macro = result["macro"]

# ① 相場状況
st.header("① 今日の相場状況")
c1, c2, c3 = st.columns(3)
with c1:
    if macro["n225_close"] is not None:
        st.metric("日経平均（終値）", f"{macro['n225_close']:,.0f}",
                  f"200日線 {macro['n225_ma']:,.0f}（{macro['n225_status']}）")
    else:
        st.metric("日経平均", "取得できず")
with c2:
    st.metric("VIX", f"{macro['vix']:.1f}" if macro["vix"] is not None else "取得できず",
              macro["vix_status"])
with c3:
    label = {"ok": "🟢 新規建て OK", "reduce": "🟡 新規 縮小/半減",
             "halt": "🔴 新規 停止"}[macro["new_entry"]]
    st.metric("新規建ての可否", label)
if macro["new_entry"] == "ok":
    st.success("地合いは新規建てOK（日経が200日線の上・VIX平常）。")
else:
    for w in macro["warnings"]:
        st.warning(w)

st.divider()

# ② 買い時
buy = [c for c in result["candidates"] if c["tradeable"]]
skip = [c for c in result["candidates"] if not c["tradeable"]]
st.header(f"② 買い時（今日の新規候補）　{len(buy)}件")
if not buy:
    st.info("今日、3条件を満たす新規候補（建てられるもの）はありません。")
for c in buy:
    with st.container(border=True):
        top, chart = st.columns([3, 2])
        with top:
            st.markdown(f"### 🟢 買い時 ｜ {c['code']} {c['name']}")
            st.write(f"**根拠**：{c['reason']}")
            st.caption(f"終値 {c['close']:,.0f}／25日線 {c['ma25']:,.0f}／"
                       f"損切り目安 {c['stop_price']:,.0f}（-7%）／利確 {c['target_price']:,.0f}（+15%）／"
                       f"目安 {c['shares']}株")
        with chart:
            mini_chart(c["history"])

if skip:
    st.subheader(f"③ 非推奨・見送り　{len(skip)}件")
    for c in skip:
        st.markdown(f"**⚪ 見送り ｜ {c['code']} {c['name']}** … {c['reason']}")

st.divider()

# ④ 全225銘柄の状況
with st.expander("④ 全225銘柄の状況を見る（検索・並べ替え可）"):
    def mark(v):
        return "—" if v is None else ("○" if v else "×")

    rows = [{
        "コード": r["code"], "名称": r["name"], "ステータス": r["status"],
        "満たした条件数": r["n_conditions"],
        "25MA上向き": mark(r["ma_slope_up"]), "RSI(35-45)": mark(r["cond_rsi"]),
        "出来高(≥20日平均)": mark(r["cond_volume"]),
        "RSI値": r["rsi"], "出来高比": r["volume_ratio"], "終値": r["close"],
    } for r in result.get("all_rows", [])]
    df_all = pd.DataFrame(rows)

    fcol1, fcol2 = st.columns(2)
    with fcol1:
        q = st.text_input("コード・名称で検索", "", placeholder="例: 7203 / トヨタ / Sony")
    with fcol2:
        statuses = ["買い時", "見送り", "押し目待ち", "あと一歩", "不通過", "データ不足", "取得失敗"]
        chosen = st.multiselect("ステータスで絞り込み", statuses, default=statuses)

    view = df_all[df_all["ステータス"].isin(chosen)] if not df_all.empty else df_all
    if q.strip() and not df_all.empty:
        ql = q.strip().lower()
        view = view[view["コード"].str.lower().str.contains(ql)
                    | view["名称"].str.lower().str.contains(ql)]
    st.caption(f"表示 {len(view)} 件 / 全 {len(df_all)} 件")
    st.dataframe(view, width="stretch", hide_index=True)

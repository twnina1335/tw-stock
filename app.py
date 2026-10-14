import random
import time
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# 頁面標題與設定
st.set_page_config(
    page_title="台股多維度買點訊號診斷系統", page_icon="🎯", layout="wide"
)

st.title("🎯 台股買點訊號綜合診斷系統")
st.caption(
    "結合技術面 (站穩季線/多頭排列)、籌碼量能與基本面 (PEG/PE) 自動判定是否為合適買點。"
)

# 初始化 Session State，確保第二區與第三區試算結果各自獨立常駐
if "batch_results_custom21" not in st.session_state:
    st.session_state["batch_results_custom21"] = None

if "batch_results_top20" not in st.session_state:
    st.session_state["batch_results_top20"] = None


# ==========================================
# 輔助函式區
# ==========================================
@st.cache_data(ttl=86400)
def get_twse_stock_names():
    """取得上市股票名稱字典 (快取 24 小時)"""
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    name_dict = {
        "2330": "台積電",
        "2317": "鴻海",
        "2454": "聯發科",
        "0050": "元大台灣50",
        "0056": "元大高股息",
        "00878": "國泰永續高股息",
        "00850": "元大臺灣ESG永續",
        "00896": "中信綠能及電動車",
        "00929": "復華台灣科技優息",
        "00919": "群益台灣精選高息",
        "00940": "元大臺灣價值高息",
    }
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            for item in data:
                name_dict[item["Code"].strip()] = item["Name"].strip()
    except Exception:
        pass
    return name_dict


@st.cache_data(ttl=86400)
def get_tip_top20_tickers():
    """從臺灣指數公司 (TIP) 抓取臺灣 50 指數最新前 20 大成分股 (快取 24 小時防 Ban)"""
    tip_url = "https://www.taiwanindex.com.tw/index/api/constituents/T50"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Referer": "https://www.taiwanindex.com.tw/",
    }

    try:
        res = requests.get(tip_url, headers=headers, timeout=8)
        if res.status_code == 200:
            data = res.json()
            records = data.get("data", []) or data.get("records", [])
            tickers = []
            for item in records:
                code = str(item.get("code") or item.get("stock_id") or item.get("symbol", "")).strip()
                if code and code.isdigit():
                    tickers.append(code)
            if len(tickers) >= 20:
                return tickers[:20]
    except Exception:
        pass

    # 權威保底前 20 大成分股名單
    fallback_top20 = [
        "2330", "2317", "2454", "2308", "2382", "2881", "2882", "2412", "2886", "3711",
        "2891", "3008", "2884", "1216", "2357", "2885", "3037", "3231", "2892", "2379"
    ]
    return fallback_top20


def resolve_stock_code(query: str, name_dict: dict):
    """解析使用者輸入：無論輸入代碼或中文名稱，統一解析出標準股票代碼與名稱"""
    clean_q = query.strip()
    if not clean_q:
        return None, None

    code_candidate = clean_q.upper().replace(".TW", "").replace(".TWO", "")
    if code_candidate in name_dict:
        return code_candidate, name_dict[code_candidate]

    for code, name in name_dict.items():
        if clean_q == name:
            return code, name

    for code, name in name_dict.items():
        if clean_q in name:
            return code, name

    if code_candidate.isdigit():
        return code_candidate, code_candidate

    return None, None


@st.cache_data(ttl=3600, show_spinner=False)
def get_yfinance_ticker_data(symbol: str):
    """為 yfinance 建立偽裝 Session，避免 401 錯誤"""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        }
    )

    ticker = yf.Ticker(symbol, session=session)
    df = ticker.history(period="1y")

    info = {}
    try:
        info_data = ticker.info
        if isinstance(info_data, dict):
            info = info_data
    except Exception:
        info = {}

    return df, info


def compute_score_at_index(df: pd.DataFrame, idx: int, info: dict, stock_code: str):
    """計算特定交易日的分數"""
    curr = df.iloc[idx]
    prev = df.iloc[idx - 1]

    curr_price = float(curr["Close"])
    curr_ma20 = float(curr["MA20"])
    curr_ma60 = float(curr["MA60"])
    prev_ma60 = float(prev["MA60"])

    pe_ratio = info.get("trailingPE", None) or info.get("forwardPE", None) if info else None
    earnings_growth = info.get("earningsGrowth", None) if info else None

    score = 0
    details = []

    # 1. 技術面 (滿分 45)
    if curr_price > curr_ma60:
        score += 20
        details.append("✅ 股價高於 60 日季線 (站穩季線，+20分)")
    else:
        details.append("❌ 股價低於 60 日季線 (+0分)")

    if curr_ma60 > prev_ma60:
        score += 15
        details.append("✅ 60 日季線斜率向上 (長線趨勢向上，+15分)")
    else:
        details.append("❌ 60 日季線斜率向下 (+0分)")

    if curr_ma20 > curr_ma60:
        score += 10
        details.append("✅ 月線高於季線 (均線多頭排列，+10分)")
    else:
        details.append("⚠️ 月線低於季線 (屬盤整震盪期，+0分)")

    # 2. 量能與動能 (滿分 25)
    vol = float(curr["Volume"])
    vol_ma20 = float(curr["Volume_MA20"]) if curr["Volume_MA20"] > 0 else 1.0

    if vol > vol_ma20 * 1.3:
        score += 15
        details.append("✅ 成交量顯著放大 (攻擊動能，+15分)")
    elif vol < vol_ma20 * 0.7:
        score += 10
        details.append("✅ 橫盤縮量沉澱 (籌碼鎖定，+10分)")
    else:
        score += 5
        details.append("ℹ️ 成交量維持正常水準 (+5分)")

    base_price = float(df.iloc[idx - 5]["Close"])
    return_5d = (curr_price - base_price) / base_price if base_price > 0 else 0
    if 0.01 <= return_5d <= 0.08:
        score += 10
        details.append("✅ 近 5 日溫和上漲 (+10分)")
    elif return_5d > 0.08:
        score += 5
        details.append("⚠️ 近 5 日漲幅較大 (防短線追高，+5分)")
    else:
        details.append("ℹ️ 近 5 日呈拉回或橫盤整理 (+0分)")

    # 3. 估值與 PEG (滿分 30)
    peg = None
    if pe_ratio and earnings_growth and earnings_growth > 0:
        try:
            peg = float(pe_ratio) / (float(earnings_growth) * 100)
        except Exception:
            peg = None

    is_etf = stock_code.startswith("00")
    if is_etf:
        score += 20
        details.append("ℹ️ ETF 標的，給予指數估值基準分 (+20分)")
    elif peg is not None:
        if peg <= 1.0:
            score += 30
            details.append(f"✅ PEG 為 {peg:.2f} (<= 1.0，安全邊際高，+30分)")
        elif 1.0 < peg <= 1.5:
            score += 20
            details.append(f"✅ PEG 為 {peg:.2f} (1.0~1.5，屬合理區間，+20分)")
        else:
            score += 5
            details.append(f"⚠️ PEG 為 {peg:.2f} (> 1.5，價格偏貴，+5分)")
    elif pe_ratio is not None:
        try:
            pe_val = float(pe_ratio)
            if pe_val < 25:
                score += 20
                details.append(f"ℹ️ 本益比為 {pe_val:.1f} (合理區間，+20分)")
            else:
                score += 10
                details.append("ℹ️ 基本面本益比偏高 (+10分)")
        except Exception:
            score += 15
            details.append("ℹ️ 估值參考基準分 (+15分)")
    else:
        score += 15
        details.append("ℹ️ 估值資料暫時受限，以標準中間基準分評定 (+15分)")

    return score, details, round(curr_price, 2), round(curr_ma60, 2), peg, pe_ratio


def calculate_comprehensive_rank_score(today_score: float, avg_5d: float, latest_price: float, ma60: float, trend_status: str):
    """
    全方位加權排序核心演算法：
    1. 今日即時評分 (35%)
    2. 近 5 日均分穩定度 (35%)
    3. 季線安全邊際 (15%): 回測季線附近 1%~5% 最優
    4. 多日動能趨勢 (15%): 連續上升給予最高評分
    """
    bias = (latest_price - ma60) / ma60 if ma60 > 0 else 0
    if 0.01 <= bias <= 0.05:
        margin_score = 100.0
    elif 0.05 < bias <= 0.10:
        margin_score = 80.0
    elif 0 <= bias < 0.01:
        margin_score = 75.0
    elif bias > 0.10:
        margin_score = 50.0
    else:
        margin_score = 20.0

    if "步步高升" in trend_status:
        trend_score = 100.0
    elif "向上增溫" in trend_status:
        trend_score = 80.0
    elif "震盪持平" in trend_status:
        trend_score = 60.0
    else:
        trend_score = 40.0

    comp_score = (today_score * 0.35) + (avg_5d * 0.35) + (margin_score * 0.15) + (trend_score * 0.15)
    return round(comp_score, 1)


def analyze_buy_signal(stock_code: str, calc_5days_history: bool = True):
    """綜合評估買點訊號"""
    symbol = (
        stock_code
        if stock_code.endswith(".TW") or stock_code.endswith(".TWO")
        else f"{stock_code}.TW"
    )

    try:
        df, info = get_yfinance_ticker_data(symbol)

        if df is None or len(df) < 70:
            return None, "❌ 歷史資料不足或代碼有誤 (至少需 70 天資料)", [], {}, None, None, None

        df["MA20"] = df["Close"].rolling(window=20).mean()
        df["MA60"] = df["Close"].rolling(window=60).mean()
        df["Volume_MA20"] = df["Volume"].rolling(window=20).mean()

        score, details, latest_price, ma60, peg, pe = compute_score_at_index(
            df, -1, info, stock_code
        )

        if score >= 75 and latest_price > ma60:
            conclusion = "🚀 強烈買點訊號 (站穩季線、估值合理、動能充沛)"
        elif score >= 60:
            conclusion = "⏳ 觀望 / 逢低布局 (接近買點，建議注意回測支撐)"
        else:
            conclusion = "❌ 暫非買點 (弱勢整理、跌破季線或估值偏貴)"

        summary_info = {
            "latest_price": latest_price,
            "ma60": ma60,
            "pe": round(float(pe), 2) if pe else "N/A",
            "peg": round(float(peg), 2) if peg else "N/A",
        }

        history_df = None
        avg_5d_score = score
        trend_status = "➡️ 震盪持平"

        if calc_5days_history and len(df) >= 70:
            records = []
            scores_5d = []
            for i in range(-5, 0):
                d_score, _, d_price, d_ma60, _, _ = compute_score_at_index(
                    df, i, info, stock_code
                )
                date_str = df.index[i].strftime("%m/%d")
                records.append({
                    "日期": date_str,
                    "收盤價": d_price,
                    "60日季線": d_ma60,
                    "評分": d_score
                })
                scores_5d.append(d_score)

            history_df = pd.DataFrame(records)
            avg_5d_score = round(sum(scores_5d) / len(scores_5d), 1)

            if len(scores_5d) >= 3 and (scores_5d[-1] > scores_5d[-2] > scores_5d[-3]):
                trend_status = "🔥 步步高升 (連升)"
            elif scores_5d[-1] > scores_5d[0]:
                trend_status = "📈 向上增溫"
            elif scores_5d[-1] < scores_5d[0]:
                trend_status = "📉 轉弱修正"
            else:
                trend_status = "➡️ 震盪持平"

        return score, conclusion, details, summary_info, history_df, avg_5d_score, trend_status

    except Exception as e:
        return None, str(e), [], {}, None, None, None


# ==========================================
# 主畫面區塊
# ==========================================
try:
    # --------------------------------------------------
    # 第一區塊：單檔個股買點訊號診斷器
    # --------------------------------------------------
    st.subheader("🔍 第一區塊：單檔股票即時診斷")
    user_stock_query = st.text_input(
        "請輸入股票代碼或中文名稱",
        placeholder="例如: 2330、台積電、聯發科、元大高股息",
        key="analyze_input",
    )

    if user_stock_query:
        all_names = get_twse_stock_names()
        stock_code_clean, stock_name = resolve_stock_code(user_stock_query, all_names)

        if not stock_code_clean:
            st.error(f"❌ 查無相符的股票名稱或代碼：'{user_stock_query}'，請嘗試輸入完整 4 碼代碼。")
        else:
            with st.spinner(f"正在分析 {stock_code_clean} {stock_name} 及其近 5 日評分趨勢..."):
                res = analyze_buy_signal(stock_code_clean, calc_5days_history=True)

                if res[0] is not None:
                    score, conclusion, details, summary_info, history_df, avg_5d, trend_status = res
                    st.markdown(f"### 📊 分析股票：{stock_code_clean} {stock_name}")

                    delta_score_str = None
                    if history_df is not None and len(history_df) >= 5:
                        score_5d_ago = history_df.iloc[0]["評分"]
                        diff = score - score_5d_ago
                        delta_score_str = f"{'+' if diff > 0 else ''}{diff} 分 (較5日前) | 趨勢: {trend_status}"

                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("今日綜合評分", f"{score} 分", delta=delta_score_str)
                    col2.metric("最新收盤價", f"{summary_info['latest_price']} 元")
                    col3.metric("60日季線 (MA60)", f"{summary_info['ma60']} 元")
                    col4.metric("PEG / PE", f"{summary_info['peg']} / {summary_info['pe']}")

                    if score >= 75:
                        st.success(f"### {conclusion}")
                    elif score >= 60:
                        st.warning(f"### {conclusion}")
                    else:
                        st.error(f"### {conclusion}")

                    if history_df is not None:
                        st.markdown("#### 📅 近 5 個交易日評分走勢")
                        chart_col, table_col = st.columns([2, 1])
                        with chart_col:
                            chart_data = history_df.set_index("日期")[["評分"]]
                            st.line_chart(chart_data, height=220)
                        with table_col:
                            st.dataframe(history_df, use_container_width=True, hide_index=True)

                    with st.expander("🔍 點擊查看今日各項詳細評分指標", expanded=False):
                        for detail in details:
                            st.write(f"- {detail}")
                else:
                    st.error(f"分析失敗: {res[1]}")

    st.divider()

    # --------------------------------------------------
    # 第二區塊：保持原樣的 21 檔自選股票批次試算
    # --------------------------------------------------
    st.subheader("📋 第二區塊：自選 21 檔股票批次試算 (保持原樣)")
    st.caption(
        "包含你指定的 21 檔標的，計算「今日評分」、「近 5 日平均分」與「動能趨勢」，依今日分數排序並常駐保留。"
    )

    custom_21_tickers = [
        "2330", "0050", "0056", "2317", "2301", "2308", "3008", "00896",
        "2395", "2885", "2890", "00878", "3231", "00850", "2454", "2379",
        "2327", "2880", "2884", "2881", "2882"
    ]

    st.info(f"📌 **自選 21 檔標的名單：**\n\n" + "、".join(custom_21_tickers))

    btn21_col1, btn21_col2 = st.columns([1, 5])
    with btn21_col1:
        start_custom21 = st.button("🚀 開始 21 檔試算", type="primary", key="btn_custom21")
    with btn21_col2:
        if st.session_state["batch_results_custom21"] is not None:
            if st.button("🗑️ 清除 21 檔結果", key="clr_custom21"):
                st.session_state["batch_results_custom21"] = None
                st.rerun()

    prog_21 = st.empty()
    status_21 = st.empty()
    table_21 = st.empty()

    if start_custom21:
        all_names = get_twse_stock_names()
        results_21 = []
        bar_21 = prog_21.progress(0)
        total_21 = len(custom_21_tickers)

        for idx, code in enumerate(custom_21_tickers):
            stock_name = all_names.get(code, code)
            status_21.markdown(f"⏳ **正在分析 21 檔標的 ({idx+1}/{total_21}):** `{code} {stock_name}`...")

            res = analyze_buy_signal(code, calc_5days_history=True)
            if res[0] is not None:
                score, conclusion, _, summary_info, _, avg_5d, trend_status = res
                results_21.append(
                    {
                        "排名": 0,
                        "股票代碼": code,
                        "名稱": stock_name,
                        "今日綜合評分": score,
                        "近5日平均分": avg_5d,
                        "評分趨勢": trend_status,
                        "買點訊號判定": conclusion,
                        "最新收盤價": summary_info["latest_price"],
                        "60日季線 (MA60)": summary_info["ma60"],
                        "PEG": summary_info["peg"],
                        "PE": summary_info["pe"],
                    }
                )

                df_curr21 = pd.DataFrame(results_21).sort_values(
                    by="今日綜合評分", ascending=False
                ).reset_index(drop=True)
                df_curr21["排名"] = df_curr21.index + 1
                table_21.dataframe(df_curr21, use_container_width=True, hide_index=True)

            bar_21.progress((idx + 1) / total_21)
            if idx < total_21 - 1:
                time.sleep(random.uniform(1.2, 2.0))

        df_final21 = pd.DataFrame(results_21).sort_values(
            by="今日綜合評分", ascending=False
        ).reset_index(drop=True)
        df_final21["排名"] = df_final21.index + 1
        st.session_state["batch_results_custom21"] = df_final21

        prog_21.empty()
        status_21.success("🎉 21 檔標的試算完成！已依「今日綜合評分」由高至低排列如下表。")
        table_21.dataframe(st.session_state["batch_results_custom21"], use_container_width=True, hide_index=True)

    elif st.session_state["batch_results_custom21"] is not None:
        status_21.success("📌 以下為先前 21 檔試算的排序結果（已常駐保留）：")
        table_21.dataframe(st.session_state["batch_results_custom21"], use_container_width=True, hide_index=True)

    st.divider()

    # --------------------------------------------------
    # 第三區塊：臺灣指數公司 (TIP) 前 20 大市值成分股全方位綜合排名
    # --------------------------------------------------
    st.subheader("🏆 第三區塊：臺灣指數公司 (TIP) 前 20 大市值成分股綜合排名")
    st.caption(
        "自動連線 TIP 抓取最新前 20 大市值成分股（24 小時快取與防 Ban 延遲機制），並依 **全方位加權綜合評分**（即時分 35% + 5日均分 35% + 季線安全邊際 15% + 動能趨勢 15%）由高至低排序。"
    )

    top20_tickers = get_tip_top20_tickers()
    st.info(f"📌 **目前 TIP 前 20 大市值成分股名單：**\n\n" + "、".join(top20_tickers))

    btn20_col1, btn20_col2 = st.columns([1, 5])
    with btn20_col1:
        start_top20 = st.button("🚀 開始前 20 大市值試算", type="primary", key="btn_top20")
    with btn20_col2:
        if st.session_state["batch_results_top20"] is not None:
            if st.button("🗑️ 清除前 20 大結果", key="clr_top20"):
                st.session_state["batch_results_top20"] = None
                st.rerun()

    prog_20 = st.empty()
    status_20 = st.empty()
    table_20 = st.empty()

    if start_top20:
        all_names = get_twse_stock_names()
        results_20 = []
        bar_20 = prog_20.progress(0)
        total_20 = len(top20_tickers)

        for idx, code in enumerate(top20_tickers):
            stock_name = all_names.get(code, code)
            status_20.markdown(f"⏳ **正在全方位分析前 20 大標的 ({idx+1}/{total_20}):** `{code} {stock_name}`...")

            res = analyze_buy_signal(code, calc_5days_history=True)

            if res[0] is not None:
                score, conclusion, _, summary_info, _, avg_5d, trend_status = res

                composite_rank_score = calculate_comprehensive_rank_score(
                    today_score=score,
                    avg_5d=avg_5d,
                    latest_price=summary_info["latest_price"],
                    ma60=summary_info["ma60"],
                    trend_status=trend_status
                )

                results_20.append(
                    {
                        "綜合排名": 0,
                        "股票代碼": code,
                        "名稱": stock_name,
                        "全方位綜合分": composite_rank_score,
                        "今日綜合分": score,
                        "近5日均分": avg_5d,
                        "動能趨勢": trend_status,
                        "買點訊號判定": conclusion,
                        "最新收盤價": summary_info["latest_price"],
                        "60日季線": summary_info["ma60"],
                        "PEG": summary_info["peg"],
                        "PE": summary_info["pe"],
                    }
                )

                df_curr20 = pd.DataFrame(results_20).sort_values(
                    by="全方位綜合分", ascending=False
                ).reset_index(drop=True)
                df_curr20["綜合排名"] = df_curr20.index + 1

                table_20.dataframe(df_curr20, use_container_width=True, hide_index=True)

            bar_20.progress((idx + 1) / total_20)

            # 防 Ban 間隔
            if idx < total_20 - 1:
                time.sleep(random.uniform(1.5, 2.3))

        df_final20 = pd.DataFrame(results_20).sort_values(
            by="全方位綜合分", ascending=False
        ).reset_index(drop=True)
        df_final20["綜合排名"] = df_final20.index + 1
        st.session_state["batch_results_top20"] = df_final20

        prog_20.empty()
        status_20.success("🎉 前 20 大市值成分股全方位評估完成！已依「全方位綜合分」由高至低排列如下表。")
        table_20.dataframe(st.session_state["batch_results_top20"], use_container_width=True, hide_index=True)

    elif st.session_state["batch_results_top20"] is not None:
        status_20.success("📌 以下為先前前 20 大市值試算的綜合排序結果（已常駐保留）：")
        table_20.dataframe(st.session_state["batch_results_top20"], use_container_width=True, hide_index=True)

except Exception as err:
    st.error(f"❌ 畫面載入發生非預期錯誤: {err}")

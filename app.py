import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# 頁面標題與設定
st.set_page_config(
    page_title="台股個股買點訊號診斷器", page_icon="🎯", layout="wide"
)

st.title("🎯 台股個股買點訊號綜合診斷器")
st.caption(
    "輸入單檔股票代碼，結合技術面 (站穩季線/多頭排列)、籌碼量能與基本面 (PEG/PE) 自動判定是否為合適買點。"
)


# ==========================================
# 輔助函式區
# ==========================================
@st.cache_data(ttl=86400)  # 快取時間延長至 24 小時，避免頻繁請求證交所 API
def get_twse_stock_names():
    """取得上市股票名稱字典 (快取 24 小時)"""
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        return {item["Code"].strip(): item["Name"].strip() for item in data}
    except Exception:
        return {}


def analyze_buy_signal(stock_code: str):
    """綜合技術面、基本面、量能評估買點訊號與分數"""
    # 判斷是否帶有字尾，若無預設加上 .TW
    symbol = (
        stock_code
        if stock_code.endswith(".TW") or stock_code.endswith(".TWO")
        else f"{stock_code}.TW"
    )
    ticker = yf.Ticker(symbol)

    # 抓取近 1 年歷史股價數據
    df = ticker.history(period="1y")

    if df.empty:
        return None, "❌ 無法取得股價數據，請確認代碼是否正確。", [], {}

    # 計算技術指標
    df["MA20"] = df["Close"].rolling(window=20).mean()
    df["MA60"] = df["Close"].rolling(window=60).mean()  # 60日季線
    df["Volume_MA20"] = df["Volume"].rolling(window=20).mean()

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    latest_price = latest["Close"]
    ma20 = latest["MA20"]
    ma60 = latest["MA60"]
    ma60_prev = prev["MA60"]

    # 基本面數據
    info = ticker.info
    pe_ratio = info.get("trailingPE", None) or info.get("forwardPE", None)
    earnings_growth = info.get("earningsGrowth", None)

    score = 0
    details = []

    # --- 1. 技術面評分 (滿分 45) ---
    if latest_price > ma60:
        score += 20
        details.append("✅ 股價高於 60 日季線 (站穩季線，+20分)")
    else:
        details.append("❌ 股價低於 60 日季線 (+0分)")

    if ma60 > ma60_prev:
        score += 15
        details.append("✅ 60 日季線斜率向上 (長線趨勢向上，+15分)")
    else:
        details.append("❌ 60 日季線斜率向下 (+0分)")

    if ma20 > ma60:
        score += 10
        details.append("✅ 月線高於季線 (均線多頭排列，+10分)")
    else:
        details.append("⚠️ 月線低於季線 (屬盤整震盪期，+0分)")

    # --- 2. 量能與動能面評分 (滿分 25) ---
    if latest["Volume"] > latest["Volume_MA20"] * 1.3:
        score += 15
        details.append(
            "✅ 今日成交量顯著放大 (大於20日均量1.3倍，有攻擊動能，+15分)"
        )
    elif latest["Volume"] < latest["Volume_MA20"] * 0.7:
        score += 10
        details.append("✅ 橫盤縮量沉澱 (利於籌碼鎖定，+10分)")
    else:
        score += 5
        details.append("ℹ️ 成交量維持正常水準 (+5分)")

    five_day_return = (latest_price - df.iloc[-6]["Close"]) / df.iloc[-6][
        "Close"
    ]
    if 0.01 <= five_day_return <= 0.08:
        score += 10
        details.append("✅ 近 5 日溫和上漲 (處於漲勢發動初期，+10分)")
    elif five_day_return > 0.08:
        score += 5
        details.append("⚠️ 近 5 日漲幅較大 (宜注意短線過熱追高風險，+5分)")
    else:
        details.append("ℹ️ 近 5 日呈拉回或橫盤整理 (+0分)")

    # --- 3. 估值與 PEG 評分 (滿分 30) ---
    peg = None
    if pe_ratio and earnings_growth and earnings_growth > 0:
        peg = pe_ratio / (earnings_growth * 100)

    if peg is not None:
        if peg <= 1.0:
            score += 30
            details.append(
                f"✅ PEG 為 {peg:.2f} (<= 1.0，相對於成長性極具安全邊際，+30分)"
            )
        elif 1.0 < peg <= 1.5:
            score += 20
            details.append(
                f"✅ PEG 為 {peg:.2f} (1.0~1.5，屬市場正常合理區間，+20分)"
            )
        else:
            score += 5
            details.append(
                f"⚠️ PEG 為 {peg:.2f} (> 1.5，目前價格偏貴或成長動能稍緩，+5分)"
            )
    else:
        if pe_ratio and pe_ratio < 25:
            score += 20
            details.append(
                f"ℹ️ (備用估值) 本益比為 {pe_ratio:.1f} (屬於合理區間，+20分)"
            )
        else:
            score += 10
            details.append("ℹ️ 基本面數據不足或本益比偏高 (+10分)")

    # 結論判定
    if score >= 75 and latest_price > ma60:
        conclusion = "🚀【強烈買點訊號】：符合多頭架構！(站穩季線、估值合理且動能充足)"
    elif score >= 60:
        conclusion = (
            "⏳【觀望/逢低布局】：接近買點條件，可關注拉回季線支撐時機。"
        )
    else:
        conclusion = (
            "❌【暫非買點】：尚未滿足條件 (股價偏弱、跌破季線或價格偏貴)。"
        )

    summary_info = {
        "latest_price": round(latest_price, 2),
        "ma60": round(ma60, 2),
        "pe": round(pe_ratio, 2) if pe_ratio else "N/A",
        "peg": round(peg, 2) if peg else "N/A",
    }

    return score, conclusion, details, summary_info


# ==========================================
# 主介面：個股買點訊號綜合診斷器
# ==========================================
stock_to_analyze = st.text_input(
    "請輸入要進行買點評估的股票代碼",
    placeholder="例如: 2330 或 2454",
    key="analyze_input",
)

if stock_to_analyze:
    stock_code_clean = stock_to_analyze.strip()
    with st.spinner(f"正在對 {stock_code_clean} 進行買點綜合指標分析..."):
        all_names = get_twse_stock_names()
        stock_name = all_names.get(stock_code_clean, stock_code_clean)

        res = analyze_buy_signal(stock_code_clean)

        if res[0] is not None:
            score, conclusion, details, summary_info = res

            # 顯示綜合評估結果卡片
            st.markdown(f"### 📊 分析股票：{stock_code_clean} {stock_name}")

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("綜合評分", f"{score} / 100 分")
            col2.metric("最新收盤價", f"{summary_info['latest_price']} 元")
            col3.metric("60日季線 (MA60)", f"{summary_info['ma60']} 元")
            col4.metric(
                "PEG / PE",
                f"{summary_info['peg']} / {summary_info['pe']}",
            )

            # 顯示結論訊息框
            if score >= 75:
                st.success(f"### {conclusion}")
            elif score >= 60:
                st.warning(f"### {conclusion}")
            else:
                st.error(f"### {conclusion}")

            # 顯示評分明細
            with st.expander("🔍 點擊查看詳細評分項目細節", expanded=True):
                for detail in details:
                    st.write(f"- {detail}")
        else:
            st.error(f"無法分析股票代碼：{stock_code_clean}")

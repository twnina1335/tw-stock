import random
import time
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# 頁面標題與設定
st.set_page_config(
    page_title="台股個股買點訊號診斷器", page_icon="🎯", layout="wide"
)

st.title("🎯 台股買點訊號綜合診斷器")
st.caption(
    "結合技術面 (站穩季線/多頭排列)、籌碼量能與基本面 (PEG/PE) 自動判定是否為合適買點。"
)

# 初始化 Session State，確保批次試算結果常駐
if "batch_results" not in st.session_state:
    st.session_state.batch_results = None


# ==========================================
# 輔助函式區
# ==========================================
@st.cache_data(ttl=86400)
def get_twse_stock_names():
    """取得上市股票名稱字典 (快取 24 小時)"""
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        name_dict = {item["Code"].strip(): item["Name"].strip() for item in data}
        
        # 補充常見 ETF 名稱
        etf_map = {
            "0050": "元大台灣50",
            "0056": "元大高股息",
            "00878": "國泰永續高股息",
            "00850": "元大臺灣ESG永續",
            "00896": "中信綠能及電動車",
            "00929": "復華台灣科技優息",
            "00919": "群益台灣精選高息",
            "00940": "元大臺灣價值高息",
        }
        name_dict.update(etf_map)
        return name_dict
    except Exception:
        return {
            "2330": "台積電",
            "2317": "鴻海",
            "2454": "聯發科",
            "0050": "元大台灣50",
            "0056": "元大高股息",
            "00878": "國泰永續高股息",
            "00850": "元大臺灣ESG永續",
            "00896": "中信綠能及電動車",
        }


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
    """為 yfinance 建立偽裝的 Request Session，避開 401/429 限制"""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }
    )

    ticker = yf.Ticker(symbol, session=session)
    df = ticker.history(period="1y")

    info = {}
    try:
        info = ticker.info
        if not isinstance(info, dict):
            info = {}
    except Exception:
        info = {}

    return df, info


def compute_score_at_index(df: pd.DataFrame, idx: int, info: dict, stock_code: str):
    """計算特定交易日的分數"""
    curr = df.iloc[idx]
    prev = df.iloc[idx - 1]

    curr_price = curr["Close"]
    curr_ma20 = curr["MA20"]
    curr_ma60 = curr["MA60"]
    prev_ma60 = prev["MA60"]

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
    if curr["Volume"] > curr["Volume_MA20"] * 1.3:
        score += 15
        details.append("✅ 成交量顯著放大 (攻擊動能，+15分)")
    elif curr["Volume"] < curr["Volume_MA20"] * 0.7:
        score += 10
        details.append("✅ 橫盤縮量沉澱 (籌碼鎖定，+10分)")
    else:
        score += 5
        details.append("ℹ️ 成交量維持正常水準 (+5分)")

    base_price = df.iloc[idx - 5]["Close"]
    return_5d = (curr_price - base_price) / base_price
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
            peg = pe_ratio / (earnings_growth * 100)
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

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# 頁面標題與設定
st.set_page_config(
    page_title="台股動態估值計算器", page_icon="📈", layout="wide"
)

st.title("📈 台股每日動態估值計算器")
st.caption("自動追蹤最新前 10 大權值股與自訂個股之 PE / PB 估值狀態")


@st.cache_data(ttl=3600)
def get_twse_stock_names():
    """取得上市股票名稱字典 (快取 1 小時)"""
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        return {item["Code"].strip(): item["Name"].strip() for item in data}
    except Exception:
        return {}


@st.cache_data(ttl=3600)
def get_top10_tw_tickers():
    """抓取市值前 10 大個股 (快取 1 小時)"""
    url = "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&type=ALL"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()

        rows, fields = [], []
        for table in data.get("tables", []):
            if "每日收盤行情" in table.get("title", "") or "Stock No." in str(
                table
            ):
                fields = table["fields"]
                rows = table["data"]
                break

        df_raw = pd.DataFrame(rows, columns=fields)
        df_raw["SecurityCode"] = df_raw["證券代號"].str.strip()
        df_raw = df_raw[df_raw["SecurityCode"].str.match(r"^\d{4}$")]

        df_raw["ClosePrice"] = pd.to_numeric(
            df_raw["收盤價"].str.replace(",", ""), errors="coerce"
        )
        df_raw["Shares"] = pd.to_numeric(
            df_raw["發行股數"].str.replace(",", ""), errors="coerce"
        )
        df_raw["MarketCap"] = df_raw["ClosePrice"] * df_raw["Shares"]

        top10_df = df_raw.sort_values(by="MarketCap", ascending=False).head(10)
        ticker_name_map = dict(
            zip(
                top10_df["SecurityCode"], top10_df["證券名稱"].str.strip()
            )
        )
        return list(ticker_name_map.keys()), ticker_name_map

    except Exception:
        fallback_map = {
            "2330": "台積電",
            "2317": "鴻海",
            "2308": "台達電",
            "2454": "聯發科",
            "3711": "日月光投控",
            "2881": "富邦金",
            "2382": "廣達",
            "2882": "國泰金",
            "2412": "中華電",
            "2891": "中信金",
        }
        return list(fallback_map.keys()), fallback_map


def fetch_valuation_data(stock_codes, name_map):
    """計算估值"""
    results = []
    for code in stock_codes:
        symbol = f"{code}.TW"
        chinese_name = name_map.get(code, code)

        ticker = yf.Ticker(symbol)
        info = ticker.info

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        pe_ratio = info.get("trailingPE")
        pb_ratio = info.get("priceToBook")
        eps = info.get("trailingEps")

        is_financial = code.startswith("28")

        if is_financial:
            if pb_ratio and pb_ratio < 1.2:
                valuation_status = "🟢 便宜 (PB < 1.2)"
            elif pb_ratio and 1.2 <= pb_ratio <= 1.6:
                valuation_status = "🟡 合理 (PB 1.2-1.6)"
            elif pb_ratio:
                valuation_status = "🔴 偏貴 (PB > 1.6)"
            else:
                valuation_status = "⚪ 資料不足"
        else:
            if pe_ratio and pe_ratio < 15:
                valuation_status = "🟢 便宜 (PE < 15)"
            elif pe_ratio and 15 <= pe_ratio <= 20:
                valuation_status = "🟡 合理 (PE 15-20)"
            elif pe_ratio:
                valuation_status = "🔴 偏貴 (PE > 20)"
            else:
                valuation_status = "⚪ 資料不足"

        results.append(
            {
                "股票代碼": code,
                "公司簡稱": chinese_name,
                "最新收盤價": price if price else "N/A",
                "EPS (TTM)": round(eps, 2) if eps else "N/A",
                "本益比 (PE)": round(pe_ratio, 2) if pe_ratio else "N/A",
                "股價淨值比 (PB)": round(pb_ratio, 2) if pb_ratio else "N/A",
                "估值狀態參考": valuation_status,
            }
        )
    return pd.DataFrame(results)


# ---------------- 介面設計 ----------------
st.subheader("🔥 今日上市前 10 大權值股估值表")

with st.spinner("正在連線證交所與 Yahoo Finance 抓取即時數據..."):
    top10_codes, top10_name_map = get_top10_tw_tickers()
    df_top10 = fetch_valuation_data(top10_codes, top10_name_map)
    st.dataframe(df_top10, use_container_width=True, hide_index=True)

st.divider()

st.subheader("🔍 自訂個股估值查詢")
user_input = st.text_input(
    "請輸入股票代碼 (多檔請用逗號隔開)",
    placeholder="例如: 2603, 3037, 2303",
)

if user_input:
    raw_codes = [
        code.strip() for code in user_input.replace("，", ",").split(",")
    ]
    custom_codes = [c for c in raw_codes if c]

    if custom_codes:
        with st.spinner("正在查詢自訂個股估值..."):
            all_names = get_twse_stock_names()
            custom_name_map = {c: all_names.get(c, c) for c in custom_codes}
            df_custom = fetch_valuation_data(custom_codes, custom_name_map)
            st.dataframe(df_custom, use_container_width=True, hide_index=True)
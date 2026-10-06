import time
from datetime import datetime, timedelta
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# 頁面標題與設定
st.set_page_config(
    page_title="台股動態估值評分系統", page_icon="📈", layout="wide"
)

st.title("📈 台股動態估值評分系統")
st.caption(
    "估值分數區間 0~100 分：分數越高代表股價相對估值越便宜、安全邊際越高。"
)


@st.cache_data(ttl=3600)
def get_twse_stock_names():
    """取得上市股票與 ETF 名稱字典 (快取 1 小時)"""
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


def calculate_valuation_score(price, eps, pb_ratio, is_financial, is_etf=False):
    """計算估值分數 (0 ~ 100 分，越便宜分數越高)"""
    if not price or price <= 0:
        return None

    if is_etf:
        # ETF 以淨值偏離度/折溢價評估
        if pb_ratio:
            premium_discount = (pb_ratio - 1.0) * 100
            score = 50 - (premium_discount * 10)
            return max(0, min(100, round(score, 1)))
        return 50.0

    if is_financial:
        # 金融股看 PB：PB <= 1.0 (100分)，PB >= 1.8 (0分)
        if not pb_ratio or pb_ratio <= 0:
            return None
        min_pb, max_pb = 1.0, 1.8
        score = (max_pb - pb_ratio) / (max_pb - min_pb) * 100
    else:
        # 一般股看 PE：PE <= 12 (100分)，PE >= 25 (0分)
        if not eps or eps <= 0:
            return None
        pe = price / eps
        min_pe, max_pe = 12.0, 25.0
        score = (max_pe - pe) / (max_pe - min_pe) * 100

    return max(0, min(100, round(score, 1)))


def get_status_text(score):
    """依照分數給予評價標籤"""
    if score is None:
        return "⚪ 資料不足"
    if score >= 70:
        return f"🟢 便宜 ({score}分)"
    elif score >= 40:
        return f"🟡 合理 ({score}分)"
    else:
        return f"🔴 偏貴 ({score}分)"


def fetch_valuation_with_weekly_scores(stock_codes, name_map, delay=0.0):
    """批次取得個股最新指標與近一週分數，支援間隔防封鎖"""
    summary_results = []
    weekly_history = {}

    for code in stock_codes:
        symbol = f"{code}.TW"
        chinese_name = name_map.get(code, code)
        is_financial = code.startswith("28")
        is_etf = code.startswith("00")

        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            hist = ticker.history(period="15d")
            hist_closes = (
                hist["Close"].dropna().tail(5) if not hist.empty else None
            )

            current_price = info.get("currentPrice") or info.get(
                "regularMarketPrice"
            )
            pe_ratio = info.get("trailingPE")
            pb_ratio = info.get("priceToBook")
            eps = info.get("trailingEps")

            today_score = calculate_valuation_score(
                current_price, eps, pb_ratio, is_financial, is_etf
            )

            summary_results.append(
                {
                    "股票代碼": code,
                    "公司簡稱": chinese_name,
                    "最新收盤價": current_price if current_price else "N/A",
                    "本益比 (PE)": (
                        round(pe_ratio, 2)
                        if pe_ratio
                        else ("ETF無" if is_etf else "N/A")
                    ),
                    "股價淨值比 (PB)": (
                        round(pb_ratio, 2) if pb_ratio else "N/A"
                    ),
                    "今日估值評分": (
                        today_score if today_score is not None else -1
                    ),
                    "評估狀態": get_status_text(today_score),
                }
            )

            if hist_closes is not None and not hist_closes.empty:
                book_value = (
                    (current_price / pb_ratio)
                    if (pb_ratio and current_price)
                    else None
                )
                daily_scores = {}
                for date, close_p in hist_closes.items():
                    date_str = date.strftime("%m/%d")
                    day_pb = (close_p / book_value) if book_value else None
                    score = calculate_valuation_score(
                        close_p, eps, day_pb, is_financial, is_etf
                    )
                    daily_scores[date_str] = score
                weekly_history[f"{code} {chinese_name}"] = daily_scores

        except Exception:
            continue

        if delay > 0:
            time.sleep(delay)

    df_summary = pd.DataFrame(summary_results)
    df_weekly = (
        pd.DataFrame(weekly_history).T if weekly_history else pd.DataFrame()
    )
    return df_summary, df_weekly


# ==================== 頁面佈局 ====================

# ---------------- PART 1: Top 10 權值股 ----------------
st.subheader("🔥 今日上市前 10 大權值股估值評分")

with st.spinner("正在連線證交所與計算前 10 大數據..."):
    top10_codes, top10_name_map = get_top10_tw_tickers()
    df_top10, df_top10_weekly = fetch_valuation_with_weekly_scores(
        top10_codes, top10_name_map
    )

    if not df_top10.empty:
        df_top10_display = df_top10.copy()
        df_top10_display["今日估值評分"] = df_top10_display[
            "今日估值評分"
        ].apply(lambda x: x if x >= 0 else "N/A")
        st.dataframe(df_top10_display, use_container_width=True, hide_index=True)

        with st.expander("📅 查看前 10 大權值股「近一週分數走勢」", expanded=False):
            st.dataframe(df_top10_weekly, use_container_width=True)

st.divider()

# ---------------- PART 2: 自訂個股查詢 ----------------
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
        with st.spinner("正在計算自訂個股估值..."):
            all_names = get_twse_stock_names()
            custom_name_map = {c: all_names.get(c, c) for c in custom_codes}
            df_custom, df_custom_weekly = fetch_valuation_with_weekly_scores(
                custom_codes, custom_name_map
            )

            df_custom_display = df_custom.copy()
            df_custom_display["今日估值評分"] = df_custom_display[
                "今日估值評分"
            ].apply(lambda x: x if x >= 0 else "N/A")
            st.dataframe(
                df_custom_display, use_container_width=True, hide_index=True
            )

            with st.expander("📅 查看自訂個股「近一週分數走勢」", expanded=True):
                st.dataframe(df_custom_weekly, use_container_width=True)

st.divider()

# ---------------- PART 3: 精選股票慢速安全試算 (擴充至 21 檔) ----------------
st.subheader("⭐ 精選觀察清單 (手動安全試算)")

# 擴充後的 21 檔觀察名單
preset_stocks = [
    "2330",
    "0050",
    "0056",
    "2317",
    "2301",
    "2308",
    "3008",
    "00896",
    "2395",
    "2885",
    "2890",
    "00878",
    "3231",
    "00850",
    "2454",
    "2379",
    "2327",
    "2880",
    "2884",
    "2881",
    "2882",
]

# 備援中文名稱字典
preset_names = {
    "2330": "台積電",
    "0050": "元大台灣50",
    "0056": "元大高股息",
    "2317": "鴻海",
    "2301": "光寶科",
    "2308": "台達電",
    "3008": "大立光",
    "00896": "中信綠能及電動車",
    "2395": "研華",
    "2885": "元大金",
    "2890": "永豐金",
    "00878": "國泰永續高股息",
    "3231": "緯創",
    "00850": "元大臺灣ESG永續",
    "2454": "聯發科",
    "2379": "瑞昱",
    "2327": "國巨",
    "2880": "華南金",
    "2884": "玉山金",
    "2881": "富邦金",
    "2882": "國泰金",
}

st.caption(
    "觀察清單包含 21 檔標的：" + "、".join([f"{c} ({preset_names[c]})" for c in preset_stocks])
)

if st.button("🚀 開始計算精選股票分數 (防封鎖慢速模式)", type="primary"):
    progress_bar = st.progress(0)
    status_text = st.empty()

    all_names = get_twse_stock_names()
    for c in preset_stocks:
        if c not in all_names:
            all_names[c] = preset_names.get(c, c)

    results_list = []
    weekly_dict = {}

    for idx, code in enumerate(preset_stocks):
        status_text.text(
            f"正在抓取並計算 ({idx + 1}/{len(preset_stocks)}): {code} {all_names.get(code, '')}..."
        )

        # 慢速模式：每檔抓取後間隔 0.8 秒防封鎖
        sub_df, sub_weekly = fetch_valuation_with_weekly_scores(
            [code], all_names, delay=0.8
        )

        if not sub_df.empty:
            results_list.append(sub_df)
        if not sub_weekly.empty:
            weekly_dict.update(sub_weekly.to_dict(orient="index"))

        progress_bar.progress((idx + 1) / len(preset_stocks))

    status_text.text("✅ 所有精選股票試算完成！")
    time.sleep(0.5)

    if results_list:
        df_preset = pd.concat(results_list, ignore_index=True)

        # 依照分數由高至低排列 (高分 = 便宜 / 安全邊際高)
        df_preset = df_preset.sort_values(
            by="今日估值評分", ascending=False
        ).reset_index(drop=True)

        df_preset_display = df_preset.copy()
        df_preset_display["今日估值評分"] = df_preset_display[
            "今日估值評分"
        ].apply(lambda x: x if x >= 0 else "N/A")

        st.success("試算結果（已依分數由高至低排列，越上方越便宜／安全邊際越高）：")
        st.dataframe(
            df_preset_display, use_container_width=True, hide_index=True
        )

        # 近一週分數走勢
        if weekly_dict:
            df_preset_weekly = pd.DataFrame.from_dict(
                weekly_dict, orient="index"
            )
            ordered_keys = [
                f"{row['股票代碼']} {row['公司簡稱']}"
                for _, row in df_preset.iterrows()
                if f"{row['股票代碼']} {row['公司簡稱']}" in df_preset_weekly.index
            ]
            df_preset_weekly = df_preset_weekly.reindex(ordered_keys)

            with st.expander("📅 查看精選清單「近一週分數走勢」", expanded=True):
                st.dataframe(df_preset_weekly, use_container_width=True)

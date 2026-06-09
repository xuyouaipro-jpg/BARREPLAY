# app.py
# BARREPLAY：類 TradingView 裸 K 闖關復盤系統
# Deployment version：Cloud-ready + per-user settings via st.session_state / browser localStorage

import base64
import json
import random
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf

try:
    yf.set_tz_cache_location("/tmp/yfinance_tz_cache")
except Exception:
    pass

# =========================================================
# 0. App Config
# =========================================================
st.set_page_config(
    page_title="BARREPLAY 裸K闖關復盤",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🎮 BARREPLAY 裸 K TradingView 風格闖關復盤")
st.caption("類 TradingView 畫線工具、均線、MACD、斐波那契、隨機股票、買入賣出；設定以每位使用者的 session / browser localStorage 保存。")
st.markdown("---")

# =========================================================
# 1. Per-user Settings: st.session_state + browser localStorage
# =========================================================
SETTINGS_KEY = "barreplay_user_settings_v1"
INDICATOR_OPTIONS = ["MA5", "MA10", "MA20", "MA60", "MA120", "EMA20", "EMA60", "VWAP"]

DEFAULT_SETTINGS = {
    "mode": "闖關模式",
    "stock_pool_text": "2330,2317,2454,2303,3037,3481,2603,2615,2002,2881,2882,2891,3711,2382,3231,2379,6669,2357,2368,2409",
    "stock_code": "3037",
    "interval_label": "日線",
    "challenge_bars": 120,
    "target_return_pct": 8.0,
    "initial_cash": 1000000.0,
    "lookback_bars": 300,
    "blind_mode": True,
    "show_volume": True,
    "chart_height": 920,
    "selected_indicators": ["MA20", "MA60"],
    "show_macd": True,
}

SETTING_SESSION_KEYS = {
    "mode": "setting_mode",
    "stock_pool_text": "setting_stock_pool_text",
    "stock_code": "stock_code",
    "interval_label": "setting_interval_label",
    "challenge_bars": "setting_challenge_bars",
    "target_return_pct": "setting_target_return_pct",
    "initial_cash": "setting_initial_cash",
    "lookback_bars": "setting_lookback_bars",
    "blind_mode": "setting_blind_mode",
    "show_volume": "setting_show_volume",
    "chart_height": "setting_chart_height",
    "show_macd": "setting_show_macd",
}


def _decode_settings_from_query() -> dict[str, Any]:
    """讀取瀏覽器 localStorage 透過 query string 傳回來的設定。"""
    raw = st.query_params.get("br_settings")
    if not raw:
        return {}

    try:
        padding = "=" * (-len(raw) % 4)
        data = base64.urlsafe_b64decode((raw + padding).encode("utf-8")).decode("utf-8")
        loaded = json.loads(data)
        if isinstance(loaded, dict):
            return loaded
    except Exception:
        return {}

    return {}


def _normalize_loaded_settings(loaded: dict[str, Any]) -> dict[str, Any]:
    settings = DEFAULT_SETTINGS.copy()
    for key, value in loaded.items():
        if key in settings:
            settings[key] = value

    if settings["mode"] not in ["闖關模式", "自選練習"]:
        settings["mode"] = DEFAULT_SETTINGS["mode"]

    if settings["interval_label"] not in ["日線", "60 分線", "30 分線", "15 分線", "5 分線"]:
        settings["interval_label"] = DEFAULT_SETTINGS["interval_label"]

    cleaned_indicators = []
    for name in settings.get("selected_indicators", []):
        if name in INDICATOR_OPTIONS and name not in cleaned_indicators:
            cleaned_indicators.append(name)
    settings["selected_indicators"] = cleaned_indicators or DEFAULT_SETTINGS["selected_indicators"]

    settings["challenge_bars"] = int(np.clip(int(settings["challenge_bars"]), 20, 300))
    settings["target_return_pct"] = float(np.clip(float(settings["target_return_pct"]), 1.0, 50.0))
    settings["initial_cash"] = float(np.clip(float(settings["initial_cash"]), 100000.0, 10000000.0))
    settings["lookback_bars"] = int(np.clip(int(settings["lookback_bars"]), 50, 800))
    settings["chart_height"] = int(np.clip(int(settings["chart_height"]), 600, 1200))
    settings["blind_mode"] = bool(settings["blind_mode"])
    settings["show_volume"] = bool(settings["show_volume"])
    settings["show_macd"] = bool(settings["show_macd"])
    settings["stock_code"] = str(settings["stock_code"]).strip() or DEFAULT_SETTINGS["stock_code"]
    settings["stock_pool_text"] = str(settings["stock_pool_text"]).strip() or DEFAULT_SETTINGS["stock_pool_text"]
    return settings


def install_browser_settings_restore() -> None:
    """若本頁第一次開啟且 localStorage 有設定，將設定帶回 Streamlit query params 後重新整理。"""
    components.html(
        f"""
        <script>
        (function() {{
            const key = {json.dumps(SETTINGS_KEY)};
            const parentWindow = window.parent;
            const params = new URLSearchParams(parentWindow.location.search);
            const hasSettings = params.has("br_settings");
            const stored = parentWindow.localStorage.getItem(key);

            if (stored && !hasSettings) {{
                try {{
                    const encoded = btoa(unescape(encodeURIComponent(stored)))
                        .replace(/\+/g, "-")
                        .replace(/\//g, "_")
                        .replace(/=+$/g, "");
                    params.set("br_settings", encoded);
                    params.set("br_settings_loaded", "1");
                    const newUrl = parentWindow.location.pathname + "?" + params.toString() + parentWindow.location.hash;
                    parentWindow.location.replace(newUrl);
                }} catch (e) {{
                    console.log("Failed to restore BARREPLAY settings:", e);
                }}
            }}
        }})();
        </script>
        """,
        height=0,
    )


def init_session_state() -> None:
    core_defaults = {
        "pending_stock_code": None,
        "current_idx": 80,
        "challenge_start_idx": 80,
        "challenge_end_idx": 200,
        "show_answer": False,
        "trade_log": [],
        "last_config_key": "",
        "cash": 1000000.0,
        "shares": 0,
        "avg_cost": 0.0,
        "realized_pnl": 0.0,
        "pending_new_challenge": False,
        "last_loaded_ticker": "",
        "last_yfinance_error": "",
    }

    for key, value in core_defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    # 第一次 session 才把 query/localStorage 設定套進 widget keys。
    if "settings_initialized" not in st.session_state:
        loaded = _normalize_loaded_settings(_decode_settings_from_query())

        for setting_name, session_key in SETTING_SESSION_KEYS.items():
            if session_key not in st.session_state:
                st.session_state[session_key] = loaded[setting_name]

        selected = set(loaded.get("selected_indicators", DEFAULT_SETTINGS["selected_indicators"]))
        for indicator in INDICATOR_OPTIONS:
            key = f"setting_indicator_{indicator}"
            if key not in st.session_state:
                st.session_state[key] = indicator in selected

        st.session_state.settings_initialized = True


def collect_current_settings() -> dict[str, Any]:
    selected_indicators = [name for name in INDICATOR_OPTIONS if st.session_state.get(f"setting_indicator_{name}", False)]

    return {
        "mode": st.session_state.get("setting_mode", DEFAULT_SETTINGS["mode"]),
        "stock_pool_text": st.session_state.get("setting_stock_pool_text", DEFAULT_SETTINGS["stock_pool_text"]),
        "stock_code": st.session_state.get("stock_code", DEFAULT_SETTINGS["stock_code"]),
        "interval_label": st.session_state.get("setting_interval_label", DEFAULT_SETTINGS["interval_label"]),
        "challenge_bars": int(st.session_state.get("setting_challenge_bars", DEFAULT_SETTINGS["challenge_bars"])),
        "target_return_pct": float(st.session_state.get("setting_target_return_pct", DEFAULT_SETTINGS["target_return_pct"])),
        "initial_cash": float(st.session_state.get("setting_initial_cash", DEFAULT_SETTINGS["initial_cash"])),
        "lookback_bars": int(st.session_state.get("setting_lookback_bars", DEFAULT_SETTINGS["lookback_bars"])),
        "blind_mode": bool(st.session_state.get("setting_blind_mode", DEFAULT_SETTINGS["blind_mode"])),
        "show_volume": bool(st.session_state.get("setting_show_volume", DEFAULT_SETTINGS["show_volume"])),
        "chart_height": int(st.session_state.get("setting_chart_height", DEFAULT_SETTINGS["chart_height"])),
        "selected_indicators": selected_indicators,
        "show_macd": bool(st.session_state.get("setting_show_macd", DEFAULT_SETTINGS["show_macd"])),
    }


def persist_settings_to_browser(settings: dict[str, Any]) -> None:
    settings_json = json.dumps(settings, ensure_ascii=False)

    components.html(
        f"""
        <script>
        (function() {{
            const key = {json.dumps(SETTINGS_KEY)};
            const settingsText = {json.dumps(settings_json, ensure_ascii=False)};
            const parentWindow = window.parent;

            try {{
                parentWindow.localStorage.setItem(key, settingsText);

                const params = new URLSearchParams(parentWindow.location.search);
                const encoded = btoa(unescape(encodeURIComponent(settingsText)))
                    .replace(/\+/g, "-")
                    .replace(/\//g, "_")
                    .replace(/=+$/g, "");

                params.set("br_settings", encoded);
                params.set("br_settings_loaded", "1");

                const newUrl = parentWindow.location.pathname + "?" + params.toString() + parentWindow.location.hash;
                parentWindow.history.replaceState(null, "", newUrl);
            }} catch (e) {{
                console.log("Failed to save BARREPLAY settings:", e);
            }}
        }})();
        </script>
        """,
        height=0,
    )


init_session_state()
install_browser_settings_restore()

# =========================================================
# 2. Keyboard Shortcut for Streamlit Parent Page
# =========================================================
def install_keyboard_shortcuts() -> None:
    components.html(
        """
        <script>
        (function () {
            const parentWindow = window.parent;
            const parentDoc = parentWindow.document;
            if (parentWindow.__tvReplayHotkeyInstalledV7) return;
            parentWindow.__tvReplayHotkeyInstalledV7 = true;

            function clickButtonByText(keyword) {
                const buttons = Array.from(parentDoc.querySelectorAll("button"));
                const target = buttons.find((btn) => btn.innerText.includes(keyword));
                if (target) target.click();
            }

            parentDoc.addEventListener("keydown", function (e) {
                const tag = (e.target.tagName || "").toLowerCase();
                if (tag === "input" || tag === "textarea" || e.target.isContentEditable) return;
                if (e.key === "ArrowLeft") { e.preventDefault(); clickButtonByText("上一根"); }
                if (e.key === "ArrowRight") { e.preventDefault(); clickButtonByText("下一根"); }
            }, true);

            parentWindow.addEventListener("message", function (e) {
                const data = e.data || {};
                if (data.type === "tv_replay_action" && data.keyword) clickButtonByText(data.keyword);
            });
        })();
        </script>
        """,
        height=0,
    )


install_keyboard_shortcuts()

# =========================================================
# 3. Data Loading
# =========================================================
@st.cache_data(show_spinner="正在下載 K 線資料...", ttl=600)
def load_data(stock_ticker: str, stock_period: str, stock_interval: str) -> pd.DataFrame:
    """Cloud-friendly yfinance loader：台股會自動嘗試 .TW / .TWO。"""
    clean_ticker = str(stock_ticker).strip().upper()
    tickers_to_try: list[str] = []

    if clean_ticker.endswith(".TW"):
        base = clean_ticker[:-3]
        tickers_to_try = [f"{base}.TW", f"{base}.TWO"]
    elif clean_ticker.endswith(".TWO"):
        base = clean_ticker[:-4]
        tickers_to_try = [f"{base}.TWO", f"{base}.TW"]
    elif clean_ticker.isdigit():
        tickers_to_try = [f"{clean_ticker}.TW", f"{clean_ticker}.TWO"]
    else:
        tickers_to_try = [clean_ticker]

    # 去重但保留順序。
    tickers_to_try = list(dict.fromkeys(tickers_to_try))
    last_error = ""

    for tk in tickers_to_try:
        try:
            data = yf.download(
                tk,
                period=stock_period,
                interval=stock_interval,
                auto_adjust=True,
                progress=False,
                threads=False,
            )

            if data is None or data.empty:
                data = yf.Ticker(tk).history(
                    period=stock_period,
                    interval=stock_interval,
                    auto_adjust=True,
                )

            if data is None or data.empty:
                last_error = f"{tk} 回傳空資料"
                continue

            if isinstance(data.columns, pd.MultiIndex):
                data.columns = [col[0] for col in data.columns]

            df = data.reset_index()
            time_col = "Datetime" if "Datetime" in df.columns else "Date"

            if time_col not in df.columns:
                last_error = f"{tk} 找不到時間欄位：{list(df.columns)}"
                continue

            df[time_col] = pd.to_datetime(df[time_col])
            df = df.rename(columns={time_col: "Time"})

            rename_map = {}
            for col in df.columns:
                lower = str(col).lower()
                if lower == "open":
                    rename_map[col] = "Open"
                elif lower == "high":
                    rename_map[col] = "High"
                elif lower == "low":
                    rename_map[col] = "Low"
                elif lower == "close":
                    rename_map[col] = "Close"
                elif lower == "volume":
                    rename_map[col] = "Volume"

            df = df.rename(columns=rename_map)
            required_cols = ["Time", "Open", "High", "Low", "Close", "Volume"]

            if not all(col in df.columns for col in required_cols):
                last_error = f"{tk} 欄位不完整：{list(df.columns)}"
                continue

            df = df[required_cols].dropna().reset_index(drop=True)

            if df.empty:
                last_error = f"{tk} 清洗後資料為空"
                continue

            df["Bar"] = np.arange(len(df))
            df["TimeStr"] = df["Time"].dt.strftime("%Y-%m-%d %H:%M")

            st.session_state["last_loaded_ticker"] = tk
            st.session_state["last_yfinance_error"] = ""
            return df

        except Exception as e:
            last_error = f"{tk} 抓取失敗：{repr(e)}"

    st.session_state["last_loaded_ticker"] = ""
    st.session_state["last_yfinance_error"] = last_error
    return pd.DataFrame()


def get_stock_name(stock_ticker: str) -> str:
    try:
        info = yf.Ticker(stock_ticker).info
        return info.get("longName", info.get("shortName", ""))
    except Exception:
        return ""

# =========================================================
# 4. Indicators and Data Conversion
# =========================================================
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"].replace(0, np.nan)

    df["MA5"] = close.rolling(5).mean()
    df["MA10"] = close.rolling(10).mean()
    df["MA20"] = close.rolling(20).mean()
    df["MA60"] = close.rolling(60).mean()
    df["MA120"] = close.rolling(120).mean()
    df["EMA20"] = close.ewm(span=20, adjust=False).mean()
    df["EMA60"] = close.ewm(span=60, adjust=False).mean()

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["DIF"] = ema12 - ema26
    df["DEA"] = df["DIF"].ewm(span=9, adjust=False).mean()
    df["MACD_HIST"] = (df["DIF"] - df["DEA"]) * 2

    typical_price = (high + low + close) / 3.0
    date_key = df["Time"].dt.date
    date_counts = pd.Series(date_key).value_counts()

    if len(date_counts) > 0 and date_counts.max() > 1:
        df["VWAP"] = (typical_price * volume).groupby(date_key).cumsum() / volume.groupby(date_key).cumsum()
    else:
        df["VWAP"] = (typical_price * volume).cumsum() / volume.cumsum()

    return df


def timestamp_seconds(value) -> int:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return int(ts.timestamp())


def make_candle_data(df: pd.DataFrame, blind_mode: bool, challenge_start_idx: int) -> list[dict]:
    out = []
    for _, row in df.iterrows():
        bar = int(row["Bar"])
        out.append(
            {
                "time": timestamp_seconds(row["Time"]),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "bar": bar,
                "label": f"D{bar - challenge_start_idx:+d}" if blind_mode else str(row["TimeStr"]),
            }
        )
    return out


def make_volume_data(df: pd.DataFrame) -> list[dict]:
    out = []
    for _, row in df.iterrows():
        color = "rgba(239,83,80,0.45)" if row["Close"] >= row["Open"] else "rgba(38,166,154,0.45)"
        out.append({"time": timestamp_seconds(row["Time"]), "value": float(row["Volume"]), "color": color})
    return out


def make_line_data(df: pd.DataFrame, col: str) -> list[dict]:
    out = []
    for _, row in df.iterrows():
        value = row.get(col, np.nan)
        if pd.notna(value):
            out.append({"time": timestamp_seconds(row["Time"]), "value": float(value)})
    return out


def build_indicator_payload(df: pd.DataFrame, selected_indicators: list[str]) -> list[dict]:
    colors = {
        "MA5": "#ffeb3b",
        "MA10": "#42a5f5",
        "MA20": "#ef5350",
        "MA60": "#ab47bc",
        "MA120": "#ffa726",
        "EMA20": "#26c6da",
        "EMA60": "#f06292",
        "VWAP": "#ffffff",
    }

    payload = []
    for name, color in colors.items():
        if name in selected_indicators:
            payload.append({"name": name, "color": color, "lineWidth": 2, "data": make_line_data(df, name)})
    return payload


def make_macd_payload(df: pd.DataFrame) -> dict:
    dif_data, dea_data, hist_data, zero_data = [], [], [], []

    for _, row in df.iterrows():
        t = timestamp_seconds(row["Time"])
        zero_data.append({"time": t, "value": 0.0})

        if pd.notna(row.get("DIF", np.nan)):
            dif_data.append({"time": t, "value": float(row["DIF"])})

        if pd.notna(row.get("DEA", np.nan)):
            dea_data.append({"time": t, "value": float(row["DEA"])})

        if pd.notna(row.get("MACD_HIST", np.nan)):
            v = float(row["MACD_HIST"])
            c = "rgba(239,83,80,0.65)" if v >= 0 else "rgba(38,166,154,0.65)"
            hist_data.append({"time": t, "value": v, "color": c})

    return {"dif": dif_data, "dea": dea_data, "hist": hist_data, "zero": zero_data}


def build_trade_markers(df: pd.DataFrame, trade_log: list[dict]) -> list[dict]:
    markers = []
    for trade in trade_log:
        bar = int(trade["bar"])
        if bar < 0 or bar >= len(df):
            continue

        row = df.iloc[bar]
        if trade["side"] == "買入":
            markers.append({"time": timestamp_seconds(row["Time"]), "position": "belowBar", "color": "#ff5252", "shape": "arrowUp", "text": "買"})
        else:
            markers.append({"time": timestamp_seconds(row["Time"]), "position": "aboveBar", "color": "#00e676", "shape": "arrowDown", "text": "賣"})
    return markers

# =========================================================
# 5. Account / Challenge / Trading
# =========================================================
def reset_account(initial_cash: float) -> None:
    st.session_state.cash = float(initial_cash)
    st.session_state.shares = 0
    st.session_state.avg_cost = 0.0
    st.session_state.realized_pnl = 0.0
    st.session_state.trade_log = []


def setup_challenge(df: pd.DataFrame, challenge_bars: int, initial_cash: float, random_start: bool) -> None:
    min_start = max(120, min(300, len(df) // 5))

    if len(df) < challenge_bars + min_start + 5:
        start_idx = min(max(60, len(df) // 3), len(df) - 2)
        end_idx = len(df) - 1
    else:
        max_start = len(df) - challenge_bars - 1
        start_idx = random.randint(min_start, max_start) if random_start else min_start
        end_idx = min(start_idx + challenge_bars, len(df) - 1)

    st.session_state.challenge_start_idx = int(start_idx)
    st.session_state.challenge_end_idx = int(end_idx)
    st.session_state.current_idx = int(start_idx)
    st.session_state.show_answer = False
    reset_account(initial_cash)


def record_trade(row: pd.Series, side: str, shares: int, price: float, reason: str) -> None:
    market_value = st.session_state.shares * price
    total_equity = st.session_state.cash + market_value
    unrealized_pnl = (price - st.session_state.avg_cost) * st.session_state.shares if st.session_state.shares > 0 else 0.0

    st.session_state.trade_log.append(
        {
            "bar": int(row["Bar"]),
            "time": row["TimeStr"],
            "relative_bar": int(row["Bar"] - st.session_state.challenge_start_idx),
            "side": side,
            "shares": int(shares),
            "price": round(float(price), 4),
            "cash_after": round(float(st.session_state.cash), 2),
            "position_after": int(st.session_state.shares),
            "avg_cost_after": round(float(st.session_state.avg_cost), 4),
            "realized_pnl": round(float(st.session_state.realized_pnl), 2),
            "unrealized_pnl": round(float(unrealized_pnl), 2),
            "total_equity": round(float(total_equity), 2),
            "reason": reason,
        }
    )


def buy_shares(row: pd.Series, lot_count: int, reason: str) -> tuple[bool, str]:
    price = float(row["Close"])
    shares_to_buy = int(lot_count) * 1000
    cost = shares_to_buy * price

    if st.session_state.cash < cost:
        return False, "現金不足，無法買入。"

    old_position_cost = st.session_state.avg_cost * st.session_state.shares
    st.session_state.shares += shares_to_buy
    st.session_state.avg_cost = (old_position_cost + cost) / st.session_state.shares
    st.session_state.cash -= cost
    record_trade(row, "買入", shares_to_buy, price, reason)
    return True, f"成功買入 {lot_count} 張，成交價 {price:.2f}"


def sell_shares(row: pd.Series, lot_count: int, reason: str) -> tuple[bool, str]:
    price = float(row["Close"])
    shares_to_sell = int(lot_count) * 1000

    if st.session_state.shares < shares_to_sell:
        return False, "庫存不足，無法賣出。"

    revenue = shares_to_sell * price
    pnl = (price - st.session_state.avg_cost) * shares_to_sell
    st.session_state.cash += revenue
    st.session_state.shares -= shares_to_sell
    st.session_state.realized_pnl += pnl

    if st.session_state.shares == 0:
        st.session_state.avg_cost = 0.0

    record_trade(row, "賣出", shares_to_sell, price, reason)
    return True, f"成功賣出 {lot_count} 張，成交價 {price:.2f}，本次損益 {pnl:.1f}"


def sell_all(row: pd.Series, reason: str) -> tuple[bool, str]:
    if st.session_state.shares <= 0:
        return False, "目前沒有庫存。"

    price = float(row["Close"])
    shares_to_sell = st.session_state.shares
    revenue = shares_to_sell * price
    pnl = (price - st.session_state.avg_cost) * shares_to_sell
    st.session_state.cash += revenue
    st.session_state.shares = 0
    st.session_state.avg_cost = 0.0
    st.session_state.realized_pnl += pnl
    record_trade(row, "全部平倉", shares_to_sell, price, reason)
    return True, f"成功全部平倉，成交價 {price:.2f}，本次損益 {pnl:.1f}"

# =========================================================
# 6. TradingView-like Chart HTML
# =========================================================
TV_HTML = r'''
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
html,body{margin:0;padding:0;overflow:hidden;background:#0f131a;color:#d1d4dc;font-family:Arial,"Microsoft JhengHei",sans-serif;}
#wrap{width:100%;height:__HEIGHT__px;background:#0f131a;border:1px solid rgba(255,255,255,0.08);box-sizing:border-box;}
#toolbar{height:44px;display:flex;align-items:center;gap:6px;padding:5px 8px;box-sizing:border-box;background:#151a23;border-bottom:1px solid rgba(255,255,255,0.08);user-select:none;}
.tool-btn{height:30px;padding:0 10px;background:#202736;color:#d1d4dc;border:1px solid #343b4a;border-radius:6px;cursor:pointer;font-size:13px;}
.tool-btn:hover{background:#2d3547;}.tool-btn.active{background:#2962ff;border-color:#2962ff;color:white;}
#status{margin-left:auto;font-size:13px;color:#9aa4b2;}
#chartBox{position:relative;width:100%;height:__MAIN_CHART_HEIGHT__px;}#mainChart{position:absolute;inset:0;}#drawCanvas{position:absolute;inset:0;z-index:10;pointer-events:none;}
#macdBox{position:relative;width:100%;height:__MACD_CHART_HEIGHT__px;display:__MACD_DISPLAY__;border-top:1px solid rgba(255,255,255,0.08);}#macdChart{position:absolute;inset:0;}
</style>
</head>
<body>
<div id="wrap">
<div id="toolbar">
<button class="tool-btn active" data-tool="cursor">游標</button><button class="tool-btn" data-tool="trend">趨勢線</button><button class="tool-btn" data-tool="hline">水平線</button><button class="tool-btn" data-tool="vline">垂直線</button><button class="tool-btn" data-tool="rect">矩形</button><button class="tool-btn" data-tool="fib">斐波</button><button class="tool-btn" data-tool="text">文字</button><button class="tool-btn" data-tool="delete">刪除</button><button class="tool-btn" id="clearAll">全清</button><button class="tool-btn" id="exportDrawings">匯出</button><button class="tool-btn" id="importDrawings">匯入</button><span id="status">模式：游標</span>
</div>
<div id="chartBox"><div id="mainChart"></div><canvas id="drawCanvas"></canvas></div><div id="macdBox"><div id="macdChart"></div></div>
</div>
<script>
const candleData=__CANDLES__;const volumeData=__VOLUMES__;const indicatorPayload=__INDICATORS__;const markers=__MARKERS__;const macdPayload=__MACD__;const showMacd=__SHOW_MACD__;const drawingsKey=__DRAWINGS_KEY__;
const chartBox=document.getElementById("chartBox");const canvas=document.getElementById("drawCanvas");const ctx=canvas.getContext("2d");const statusEl=document.getElementById("status");
const timeLabelMap={};candleData.forEach(d=>{timeLabelMap[d.time]=d.label;});
let tool="cursor";let firstPoint=null;let drawings=[];try{drawings=JSON.parse(localStorage.getItem(drawingsKey)||"[]");if(!Array.isArray(drawings))drawings=[];}catch(e){drawings=[];}
const chart=LightweightCharts.createChart(document.getElementById("mainChart"),{layout:{background:{color:"#0f131a"},textColor:"#d1d4dc"},localization:{timeFormatter:(time)=>timeLabelMap[time]||String(time)},grid:{vertLines:{color:"rgba(255,255,255,0.08)"},horzLines:{color:"rgba(255,255,255,0.08)"}},rightPriceScale:{borderColor:"rgba(255,255,255,0.15)"},timeScale:{borderColor:"rgba(255,255,255,0.15)",timeVisible:true,secondsVisible:false,tickMarkFormatter:(time)=>timeLabelMap[time]||""},crosshair:{mode:LightweightCharts.CrosshairMode.Normal},handleScale:true,handleScroll:true});
const candleSeries=chart.addCandlestickSeries({upColor:"#ef5350",downColor:"#26a69a",borderUpColor:"#ef5350",borderDownColor:"#26a69a",wickUpColor:"#ef5350",wickDownColor:"#26a69a"});candleSeries.setData(candleData);if(markers.length>0)candleSeries.setMarkers(markers);
if(volumeData.length>0){const volumeSeries=chart.addHistogramSeries({priceFormat:{type:"volume"},priceScaleId:""});volumeSeries.priceScale().applyOptions({scaleMargins:{top:0.80,bottom:0}});volumeSeries.setData(volumeData);}
indicatorPayload.forEach(item=>{const s=chart.addLineSeries({color:item.color,lineWidth:item.lineWidth,priceLineVisible:false,lastValueVisible:false,title:""});s.setData(item.data);});chart.timeScale().fitContent();
let macdChart=null;let syncingRange=false;if(showMacd){macdChart=LightweightCharts.createChart(document.getElementById("macdChart"),{layout:{background:{color:"#0f131a"},textColor:"#d1d4dc"},localization:{timeFormatter:(time)=>timeLabelMap[time]||String(time)},grid:{vertLines:{color:"rgba(255,255,255,0.08)"},horzLines:{color:"rgba(255,255,255,0.08)"}},rightPriceScale:{borderColor:"rgba(255,255,255,0.15)"},timeScale:{borderColor:"rgba(255,255,255,0.15)",timeVisible:true,secondsVisible:false,tickMarkFormatter:(time)=>timeLabelMap[time]||""},crosshair:{mode:LightweightCharts.CrosshairMode.Normal},handleScale:true,handleScroll:true});
const histSeries=macdChart.addHistogramSeries({priceFormat:{type:"price",precision:2,minMove:0.01},priceLineVisible:false,lastValueVisible:false,title:""});histSeries.setData(macdPayload.hist);const zeroSeries=macdChart.addLineSeries({color:"rgba(255,255,255,0.35)",lineWidth:1,priceLineVisible:false,lastValueVisible:false,title:""});zeroSeries.setData(macdPayload.zero);const difSeries=macdChart.addLineSeries({color:"#ffca28",lineWidth:2,title:"",priceLineVisible:false,lastValueVisible:false});difSeries.setData(macdPayload.dif);const deaSeries=macdChart.addLineSeries({color:"#42a5f5",lineWidth:2,title:"",priceLineVisible:false,lastValueVisible:false});deaSeries.setData(macdPayload.dea);macdChart.timeScale().fitContent();chart.timeScale().subscribeVisibleLogicalRangeChange(range=>{if(!range||syncingRange||!macdChart)return;syncingRange=true;macdChart.timeScale().setVisibleLogicalRange(range);syncingRange=false;});macdChart.timeScale().subscribeVisibleLogicalRangeChange(range=>{if(!range||syncingRange)return;syncingRange=true;chart.timeScale().setVisibleLogicalRange(range);syncingRange=false;});}
function resizeChart(){const w=chartBox.clientWidth;const h=chartBox.clientHeight;chart.applyOptions({width:w,height:h});canvas.width=w;canvas.height=h;if(showMacd&&macdChart){const macdBox=document.getElementById("macdBox");macdChart.applyOptions({width:macdBox.clientWidth,height:macdBox.clientHeight});}drawAll();}new ResizeObserver(resizeChart).observe(chartBox);resizeChart();
function saveDrawings(){localStorage.setItem(drawingsKey,JSON.stringify(drawings));drawAll();}
function setTool(newTool){tool=newTool;firstPoint=null;document.querySelectorAll(".tool-btn[data-tool]").forEach(btn=>{btn.classList.toggle("active",btn.dataset.tool===tool);});if(tool==="cursor"){canvas.style.pointerEvents="none";statusEl.innerText="模式：游標，可拖曳縮放圖表";}else{canvas.style.pointerEvents="auto";statusEl.innerText="模式："+({trend:"趨勢線，點第一下起點，第二下終點",hline:"水平線，點一下價格位置",vline:"垂直線，點一下時間位置",rect:"矩形，點第一下左上/左下，第二下右下/右上",fib:"斐波那契，點第一下高/低點，第二下低/高點",text:"文字，點一下要標註的位置",delete:"刪除，點擊線段或物件"}[tool]||tool);}}
document.querySelectorAll(".tool-btn[data-tool]").forEach(btn=>btn.addEventListener("click",()=>setTool(btn.dataset.tool)));
document.getElementById("clearAll").addEventListener("click",()=>{if(confirm("確定清除所有畫線？")){drawings=[];saveDrawings();}});
document.getElementById("exportDrawings").addEventListener("click",()=>{const text=JSON.stringify(drawings,null,2);const blob=new Blob([text],{type:"application/json"});const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download="tv_drawings.json";a.click();URL.revokeObjectURL(url);});
document.getElementById("importDrawings").addEventListener("click",()=>{const text=prompt("請貼上畫線 JSON：");if(!text)return;try{const imported=JSON.parse(text);if(!Array.isArray(imported)){alert("JSON 必須是陣列格式。");return;}drawings=imported;saveDrawings();}catch(e){alert("匯入失敗："+e.message);}});
function getMousePoint(e){const rect=canvas.getBoundingClientRect();const x=e.clientX-rect.left;const y=e.clientY-rect.top;const time=chart.timeScale().coordinateToTime(x);const price=candleSeries.coordinateToPrice(y);if(time===null||price===null||isNaN(price))return null;return{x,y,time,price};}
function toX(time){return chart.timeScale().timeToCoordinate(time);}function toY(price){return candleSeries.priceToCoordinate(price);}
function drawAll(){ctx.clearRect(0,0,canvas.width,canvas.height);drawings.forEach(d=>{ctx.save();ctx.lineWidth=d.width||2;ctx.strokeStyle=d.color||"#ffca28";ctx.fillStyle=d.color||"#ffca28";ctx.font="13px Arial";
if(d.type==="hline"){const y=toY(d.price);if(y===null){ctx.restore();return;}ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(canvas.width,y);ctx.stroke();if(d.text)ctx.fillText(d.text,10,y-6);}
if(d.type==="vline"){const x=toX(d.time);if(x===null){ctx.restore();return;}ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,canvas.height);ctx.stroke();if(d.text)ctx.fillText(d.text,x+5,18);}
if(d.type==="trend"){const x1=toX(d.time1),y1=toY(d.price1),x2=toX(d.time2),y2=toY(d.price2);if([x1,y1,x2,y2].some(v=>v===null)){ctx.restore();return;}ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();}
if(d.type==="rect"){const x1=toX(d.time1),y1=toY(d.price1),x2=toX(d.time2),y2=toY(d.price2);if([x1,y1,x2,y2].some(v=>v===null)){ctx.restore();return;}const left=Math.min(x1,x2),top=Math.min(y1,y2),w=Math.abs(x2-x1),h=Math.abs(y2-y1);ctx.globalAlpha=0.16;ctx.fillRect(left,top,w,h);ctx.globalAlpha=1;ctx.strokeRect(left,top,w,h);if(d.text)ctx.fillText(d.text,left+5,top+16);}
if(d.type==="fib"){const x1=toX(d.time1),x2=toX(d.time2);if(x1===null||x2===null){ctx.restore();return;}const left=Math.min(x1,x2),right=Math.max(x1,x2),high=d.high,low=d.low,range=high-low;[{r:0,label:"0"},{r:.236,label:"0.236"},{r:.382,label:"0.382"},{r:.5,label:"0.5"},{r:.618,label:"0.618"},{r:.786,label:"0.786"},{r:1,label:"1"}].forEach(lv=>{const price=high-range*lv.r;const y=toY(price);if(y===null)return;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillText(`${lv.label} ${price.toFixed(2)}`,right+6,y-4);});}
if(d.type==="text"){const x=toX(d.time),y=toY(d.price);if(x===null||y===null){ctx.restore();return;}ctx.fillText(d.text||"文字",x+5,y-5);ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fill();}ctx.restore();});}
function distanceToSegment(px,py,x1,y1,x2,y2){const A=px-x1,B=py-y1,C=x2-x1,D=y2-y1;const dot=A*C+B*D,lenSq=C*C+D*D;let param=-1;if(lenSq!==0)param=dot/lenSq;let xx,yy;if(param<0){xx=x1;yy=y1;}else if(param>1){xx=x2;yy=y2;}else{xx=x1+param*C;yy=y1+param*D;}const dx=px-xx,dy=py-yy;return Math.sqrt(dx*dx+dy*dy);}
function hitTest(px,py){for(let i=drawings.length-1;i>=0;i--){const d=drawings[i];if(d.type==="hline"){const y=toY(d.price);if(y!==null&&Math.abs(py-y)<8)return i;}if(d.type==="vline"){const x=toX(d.time);if(x!==null&&Math.abs(px-x)<8)return i;}if(d.type==="trend"){const x1=toX(d.time1),y1=toY(d.price1),x2=toX(d.time2),y2=toY(d.price2);if([x1,y1,x2,y2].some(v=>v===null))continue;if(distanceToSegment(px,py,x1,y1,x2,y2)<8)return i;}if(d.type==="rect"){const x1=toX(d.time1),y1=toY(d.price1),x2=toX(d.time2),y2=toY(d.price2);if([x1,y1,x2,y2].some(v=>v===null))continue;const left=Math.min(x1,x2),right=Math.max(x1,x2),top=Math.min(y1,y2),bottom=Math.max(y1,y2);if(px>=left&&px<=right&&py>=top&&py<=bottom)return i;}if(d.type==="fib"){const x1=toX(d.time1),x2=toX(d.time2);if(x1===null||x2===null)continue;const left=Math.min(x1,x2),right=Math.max(x1,x2);if(px<left-10||px>right+10)continue;for(const r of [0,.236,.382,.5,.618,.786,1]){const y=toY(d.high-(d.high-d.low)*r);if(y!==null&&Math.abs(py-y)<8)return i;}}if(d.type==="text"){const x=toX(d.time),y=toY(d.price);if(x!==null&&y!==null&&Math.abs(px-x)<60&&Math.abs(py-y)<20)return i;}}return -1;}
canvas.addEventListener("click",e=>{const p=getMousePoint(e);if(!p)return;if(tool==="delete"){const idx=hitTest(p.x,p.y);if(idx>=0){drawings.splice(idx,1);saveDrawings();}return;}if(tool==="hline"){drawings.push({type:"hline",price:p.price,color:"#ffca28",width:2,text:"水平線"});saveDrawings();return;}if(tool==="vline"){drawings.push({type:"vline",time:p.time,color:"#64b5f6",width:2,text:"關鍵K"});saveDrawings();return;}if(tool==="text"){const text=prompt("輸入標註文字：","關鍵位置");if(text===null)return;drawings.push({type:"text",time:p.time,price:p.price,color:"#ffca28",text:text});saveDrawings();return;}if(tool==="trend"){if(!firstPoint){firstPoint=p;statusEl.innerText="趨勢線：已設定起點，請點終點";return;}drawings.push({type:"trend",time1:firstPoint.time,price1:firstPoint.price,time2:p.time,price2:p.price,color:"#ffca28",width:2,text:""});firstPoint=null;saveDrawings();return;}if(tool==="rect"){if(!firstPoint){firstPoint=p;statusEl.innerText="矩形：已設定第一點，請點第二點";return;}drawings.push({type:"rect",time1:firstPoint.time,price1:firstPoint.price,time2:p.time,price2:p.price,color:"#ffca28",width:2,text:"區間"});firstPoint=null;saveDrawings();return;}if(tool==="fib"){if(!firstPoint){firstPoint=p;statusEl.innerText="斐波那契：已設定第一點，請點第二點";return;}drawings.push({type:"fib",time1:firstPoint.time,price1:firstPoint.price,time2:p.time,price2:p.price,high:Math.max(firstPoint.price,p.price),low:Math.min(firstPoint.price,p.price),color:"#ffca28",width:1.5,text:"Fib"});firstPoint=null;saveDrawings();return;}});
function clickParentButtonByText(keyword){try{const buttons=Array.from(window.parent.document.querySelectorAll("button"));const target=buttons.find(btn=>btn.innerText.includes(keyword));if(target){target.click();return;}}catch(e){console.log("Direct parent click failed:",e);}try{window.parent.postMessage({type:"tv_replay_action",keyword:keyword},"*");}catch(e){console.log("postMessage failed:",e);}}
document.addEventListener("keydown",e=>{const tag=(e.target.tagName||"").toLowerCase();if(tag==="input"||tag==="textarea"||e.target.isContentEditable)return;if(e.key==="ArrowLeft"){e.preventDefault();clickParentButtonByText("上一根");}if(e.key==="ArrowRight"){e.preventDefault();clickParentButtonByText("下一根");}},true);
window.addEventListener("keydown",e=>{if(e.key==="ArrowLeft"){e.preventDefault();clickParentButtonByText("上一根");}if(e.key==="ArrowRight"){e.preventDefault();clickParentButtonByText("下一根");}},true);
chart.timeScale().subscribeVisibleLogicalRangeChange(()=>drawAll());chart.subscribeCrosshairMove(()=>drawAll());setInterval(drawAll,400);setTool("cursor");drawAll();
</script>
</body>
</html>
'''


def render_tv_chart(visible_df, indicator_df, selected_indicators, show_volume, show_macd, trade_log, df_all, blind_mode, ticker, interval, challenge_start_idx, challenge_id, height) -> None:
    candles = make_candle_data(visible_df, blind_mode=blind_mode, challenge_start_idx=challenge_start_idx)
    volumes = make_volume_data(visible_df) if show_volume else []
    indicators = build_indicator_payload(indicator_df, selected_indicators)
    markers = build_trade_markers(df_all, trade_log)
    macd_payload = make_macd_payload(indicator_df) if show_macd else {"dif": [], "dea": [], "hist": [], "zero": []}

    indicator_signature = "-".join(selected_indicators) if selected_indicators else "none"
    drawings_key = f"tv_drawings_{ticker}_{interval}_{challenge_id}"
    chart_signature = f"{ticker}_{interval}_{challenge_id}_{indicator_signature}_{show_macd}_{show_volume}_{len(visible_df)}_{float(visible_df.iloc[-1]['Close']) if len(visible_df) else 0}"

    inner_height = max(300, height - 44)
    if show_macd:
        main_chart_height = int(inner_height * 0.74)
        macd_chart_height = inner_height - main_chart_height
        macd_display = "block"
    else:
        main_chart_height = inner_height
        macd_chart_height = 0
        macd_display = "none"

    html_code = (TV_HTML
        .replace("__HEIGHT__", str(height))
        .replace("__MAIN_CHART_HEIGHT__", str(main_chart_height))
        .replace("__MACD_CHART_HEIGHT__", str(macd_chart_height))
        .replace("__MACD_DISPLAY__", macd_display)
        .replace("__CANDLES__", json.dumps(candles, ensure_ascii=False))
        .replace("__VOLUMES__", json.dumps(volumes, ensure_ascii=False))
        .replace("__INDICATORS__", json.dumps(indicators, ensure_ascii=False))
        .replace("__MARKERS__", json.dumps(markers, ensure_ascii=False))
        .replace("__MACD__", json.dumps(macd_payload, ensure_ascii=False))
        .replace("__SHOW_MACD__", json.dumps(show_macd))
        .replace("__DRAWINGS_KEY__", json.dumps(drawings_key, ensure_ascii=False)))

    html_code = f"<!-- BARREPLAY_CHART_SIGNATURE:{chart_signature} -->\n" + html_code
    components.html(html_code, height=height + 10, scrolling=False)

# =========================================================
# 7. Sidebar Settings
# =========================================================
if st.session_state.get("pending_stock_code") is not None:
    st.session_state.stock_code = st.session_state.pending_stock_code
    st.session_state.pending_stock_code = None

with st.sidebar:
    st.header("⚙️ 闖關設定")
    st.caption("目前版本：V7-cloud（每位使用者用 session/localStorage 保存設定，不寫伺服器 JSON）")

    mode = st.radio("模式", ["闖關模式", "自選練習"], key="setting_mode")

    stock_pool_text = st.text_area("隨機股票池", height=90, key="setting_stock_pool_text")
    stock_pool = [code.strip() for code in stock_pool_text.replace("\n", ",").split(",") if code.strip()]

    if st.button("🎲 隨機開新關卡", use_container_width=True):
        if stock_pool:
            st.session_state.pending_stock_code = random.choice(stock_pool)
            st.session_state.pending_new_challenge = True
            st.rerun()

    raw_code_input = st.text_input("目前股票代號", key="stock_code")

    interval_map = {"日線": "1d", "60 分線": "60m", "30 分線": "30m", "15 分線": "15m", "5 分線": "5m"}
    interval_label = st.selectbox("K 線週期", list(interval_map.keys()), key="setting_interval_label")
    interval = interval_map[interval_label]
    period = "5y" if interval == "1d" else "60d"

    challenge_bars = st.slider("限定 K 數（日線 = 天數）", min_value=20, max_value=300, step=10, key="setting_challenge_bars")
    target_return_pct = st.slider("過關目標報酬率 %", min_value=1.0, max_value=50.0, step=0.5, key="setting_target_return_pct")
    initial_cash = st.number_input("初始資金", min_value=100000.0, max_value=10000000.0, step=100000.0, key="setting_initial_cash")
    lookback_bars = st.slider("起始回看 K 數", min_value=50, max_value=800, step=10, key="setting_lookback_bars")
    blind_mode = st.checkbox("盲測模式：隱藏股票與日期", key="setting_blind_mode")
    show_volume = st.checkbox("顯示成交量", key="setting_show_volume")
    chart_height = st.slider("圖表高度", min_value=600, max_value=1200, step=20, key="setting_chart_height")

    st.markdown("---")
    if st.button("🔄 重設本關交易帳戶", use_container_width=True):
        reset_account(initial_cash)
        st.rerun()

# =========================================================
# 8. Load / Setup Challenge
# =========================================================
settings_now = collect_current_settings()
persist_settings_to_browser(settings_now)

raw_code = str(st.session_state.stock_code).strip()
# load_data 會負責嘗試 .TW / .TWO，這裡只先組出常用顯示 ticker。
ticker = raw_code if raw_code.upper().endswith(".TW") or raw_code.upper().endswith(".TWO") else f"{raw_code}.TW"

df = load_data(ticker, period, interval)

if df.empty:
    st.error("抓不到資料。可能是 Yahoo Finance 在 Streamlit Cloud 上暫時抓不到台股資料，或代號/週期不支援。")
    st.write("目前嘗試參數：")
    st.code(
        f"ticker = {ticker}\nperiod = {period}\ninterval = {interval}\n"
        f"last_error = {st.session_state.get('last_yfinance_error', '')}"
    )
    st.info("請先測試 2330、2317、2454；若美股 AAPL 可抓但台股抓不到，多半是 Yahoo 台股資料在雲端端點不穩。")
    st.stop()

df = add_indicators(df)
actual_loaded_ticker = st.session_state.get("last_loaded_ticker", ticker)
config_key = f"{actual_loaded_ticker}_{period}_{interval}_{challenge_bars}_{target_return_pct}_{initial_cash}_{mode}"

if st.session_state.last_config_key != config_key or st.session_state.pending_new_challenge:
    st.session_state.last_config_key = config_key
    setup_challenge(df=df, challenge_bars=challenge_bars, initial_cash=initial_cash, random_start=(mode == "闖關模式"))
    st.session_state.pending_new_challenge = False

if mode == "闖關模式":
    min_idx = st.session_state.challenge_start_idx
    max_idx = st.session_state.challenge_end_idx
else:
    min_idx = 5
    max_idx = len(df) - 1

st.session_state.current_idx = int(np.clip(st.session_state.current_idx, min_idx, max_idx))
current_row = df.iloc[st.session_state.current_idx]
current_price = float(current_row["Close"])
position_value = st.session_state.shares * current_price
unrealized_pnl = (current_price - st.session_state.avg_cost) * st.session_state.shares if st.session_state.shares > 0 else 0.0
total_equity = st.session_state.cash + position_value
return_pct = (total_equity - initial_cash) / initial_cash * 100.0
bars_passed = int(st.session_state.current_idx - st.session_state.challenge_start_idx)
bars_total = int(st.session_state.challenge_end_idx - st.session_state.challenge_start_idx)
bars_left = max(0, bars_total - bars_passed)
stock_name = "" if blind_mode else get_stock_name(actual_loaded_ticker)
show_title = "隨機盲測標的" if blind_mode else f"{actual_loaded_ticker} {stock_name}"
show_time = f"D+{bars_passed}" if blind_mode else current_row["TimeStr"]

# =========================================================
# 9. Top Info
# =========================================================
col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
col1.subheader(f"📊 {show_title}")
col2.metric("目前價格", f"{current_price:.2f}")
col3.metric("關卡進度", f"{bars_passed} / {bars_total}")
col4.metric("剩餘 K 數", f"{bars_left}")

g1, g2, g3, g4 = st.columns(4)
g1.metric("目前報酬率", f"{return_pct:.2f}%", delta=f"{return_pct - target_return_pct:.2f}%")
g2.metric("過關目標", f"{target_return_pct:.2f}%")
g3.metric("總資產", f"{total_equity:,.0f}")
g4.metric("目前時間", show_time)
st.progress(min(max(bars_passed / max(bars_total, 1), 0), 1))

# =========================================================
# 10. Replay Control
# =========================================================
st.markdown("### 🕹️ 重播控制")
replay_col1, replay_col2, replay_col3, replay_col4, replay_col5, replay_col6 = st.columns([1, 1.3, 1.8, 1.8, 1.3, 1])

with replay_col1:
    if st.button("⏮️ -10", use_container_width=True):
        st.session_state.current_idx = max(min_idx, st.session_state.current_idx - 10)
        st.session_state.show_answer = False
        st.rerun()

with replay_col2:
    if st.button("⬅️ 上一根", use_container_width=True):
        st.session_state.current_idx = max(min_idx, st.session_state.current_idx - 1)
        st.session_state.show_answer = False
        st.rerun()

with replay_col3:
    if st.button("➡️ 下一根", type="primary", use_container_width=True):
        st.session_state.current_idx = min(max_idx, st.session_state.current_idx + 1)
        st.session_state.show_answer = False
        st.rerun()

with replay_col4:
    if st.button("👁️ 對答案 / 顯示終點", use_container_width=True):
        st.session_state.show_answer = not st.session_state.show_answer
        st.rerun()

with replay_col5:
    if st.button("⏭️ +10", use_container_width=True):
        st.session_state.current_idx = min(max_idx, st.session_state.current_idx + 10)
        st.session_state.show_answer = False
        st.rerun()

with replay_col6:
    if st.button("🎲 下一關", use_container_width=True):
        if stock_pool:
            st.session_state.pending_stock_code = random.choice(stock_pool)
        st.session_state.pending_new_challenge = True
        st.rerun()

st.caption("快捷鍵：`←` 上一根，`→` 下一根。即使滑鼠點在圖表內，也可以使用左右鍵。")

# =========================================================
# 11. Account / Trade Panel
# =========================================================
st.markdown("#### 💰 模擬交易帳戶")
a1, a2, a3, a4, a5 = st.columns(5)
a1.metric("現金", f"{st.session_state.cash:,.0f}")
a2.metric("持股", f"{st.session_state.shares:,} 股")
a3.metric("平均成本", f"{st.session_state.avg_cost:.2f}")
a4.metric("未實現損益", f"{unrealized_pnl:,.0f}")
a5.metric("已實現損益", f"{st.session_state.realized_pnl:,.0f}")

st.markdown("#### 🧾 買入 / 賣出")
t1, t2, t3, t4, t5 = st.columns([2, 1, 1, 1, 1])
trade_note = t1.text_input("買賣原因", placeholder="例：突破箱頂買入 / 跌破支撐停損 / 爆量長黑出場")
lot_count = t2.number_input("交易張數", min_value=1, max_value=100, value=1, step=1)

with t3:
    if st.button("🔴 買入", type="primary", use_container_width=True):
        ok, msg = buy_shares(current_row, lot_count, trade_note)
        if ok:
            st.toast(msg)
        else:
            st.error(msg)
        st.rerun()

with t4:
    if st.button("🟢 賣出", use_container_width=True):
        ok, msg = sell_shares(current_row, lot_count, trade_note)
        if ok:
            st.toast(msg)
        else:
            st.error(msg)
        st.rerun()

with t5:
    if st.button("⚪ 全部平倉", use_container_width=True):
        ok, msg = sell_all(current_row, trade_note)
        if ok:
            st.toast(msg)
        else:
            st.error(msg)
        st.rerun()

# =========================================================
# 12. Indicator Checkboxes
# =========================================================
st.markdown("#### 📊 指標設定")
st.caption("每位使用者的勾選會儲存在自己的瀏覽器 localStorage；不再寫入伺服器 JSON，所以多人部署時不會互相覆蓋。")

indicator_cols = st.columns(8)
for idx, indicator in enumerate(INDICATOR_OPTIONS):
    with indicator_cols[idx]:
        st.checkbox(indicator, key=f"setting_indicator_{indicator}")

show_macd = st.checkbox("顯示 MACD", key="setting_show_macd")
selected_indicators = [name for name in INDICATOR_OPTIONS if st.session_state.get(f"setting_indicator_{name}", False)]

settings_now = collect_current_settings()
persist_settings_to_browser(settings_now)

# =========================================================
# 13. Chart
# =========================================================
visible_start = max(0, st.session_state.challenge_start_idx - lookback_bars + 1)
visible_end = st.session_state.challenge_end_idx if st.session_state.show_answer else st.session_state.current_idx
visible_df = df.iloc[visible_start: visible_end + 1]
challenge_id = f"{st.session_state.challenge_start_idx}_{st.session_state.challenge_end_idx}"

render_tv_chart(
    visible_df=visible_df,
    indicator_df=visible_df,
    selected_indicators=selected_indicators,
    show_volume=show_volume,
    show_macd=show_macd,
    trade_log=st.session_state.trade_log,
    df_all=df,
    blind_mode=blind_mode,
    ticker=actual_loaded_ticker,
    interval=interval,
    challenge_start_idx=st.session_state.challenge_start_idx,
    challenge_id=challenge_id,
    height=chart_height,
)

if selected_indicators:
    current_indicator_values = []
    for name in selected_indicators:
        val = current_row.get(name, np.nan)
        if pd.notna(val):
            current_indicator_values.append(f"{name}: {float(val):.2f}")
    if current_indicator_values:
        st.caption("目前指標數值：" + "　".join(current_indicator_values))

st.info("畫圖方式：圖表上方選「趨勢線 / 水平線 / 矩形 / 斐波 / 文字」後直接點圖。刪除請切到「刪除」後點物件。")

# =========================================================
# 14. Results / Logs
# =========================================================
if st.session_state.current_idx >= st.session_state.challenge_end_idx:
    st.markdown("### 🏁 闖關結果")
    if return_pct >= target_return_pct:
        st.success(f"過關！目標 {target_return_pct:.2f}%，你的報酬率 {return_pct:.2f}%。")
    else:
        st.error(f"未過關。目標 {target_return_pct:.2f}%，你的報酬率 {return_pct:.2f}%。")

    level = "S 級" if return_pct >= 20 else "A 級" if return_pct >= 10 else "B 級" if return_pct >= 5 else "C 級" if return_pct >= 0 else "D 級"
    st.metric("本關評級", level)

st.markdown("#### 📒 買賣紀錄")
if st.session_state.trade_log:
    trade_df = pd.DataFrame(st.session_state.trade_log)
    cols = ["relative_bar" if blind_mode else "time", "side", "shares", "price", "position_after", "avg_cost_after", "realized_pnl", "unrealized_pnl", "total_equity", "reason"]
    rename = {
        "relative_bar": "相對K數",
        "time": "時間",
        "side": "動作",
        "shares": "股數",
        "price": "價格",
        "position_after": "交易後持股",
        "avg_cost_after": "交易後均價",
        "realized_pnl": "已實現損益",
        "unrealized_pnl": "未實現損益",
        "total_equity": "總資產",
        "reason": "原因",
    }
    st.dataframe(trade_df[cols].rename(columns=rename), use_container_width=True, hide_index=True)
    st.download_button(
        "下載買賣紀錄 CSV",
        data=trade_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="naked_k_tv_trade_log.csv",
        mime="text/csv",
        use_container_width=True,
    )
else:
    st.caption("尚未有買賣紀錄。")

with st.expander("目前 K 棒資料"):
    if blind_mode:
        k_info = {"相對K數": f"D+{bars_passed}", "Open": current_row["Open"], "High": current_row["High"], "Low": current_row["Low"], "Close": current_row["Close"], "Volume": current_row["Volume"]}
    else:
        k_info = {"時間": current_row["TimeStr"], "Open": current_row["Open"], "High": current_row["High"], "Low": current_row["Low"], "Close": current_row["Close"], "Volume": current_row["Volume"]}
    st.dataframe(pd.DataFrame([k_info]), use_container_width=True, hide_index=True)


# tw_naked_k_tv_challenge.py
# 類 TradingView 裸 K 闖關復盤系統 V4
# 功能：隨機股票闖關、買入賣出、MA/EMA/VWAP、MACD、斐波那契、畫線工具、左右鍵控制

import json
import random
from pathlib import Path
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf


# =========================================================
# 0.1 Persistent User Settings
# =========================================================
# 使用者每次改勾選指標 / MACD / 顯示設定時，都會覆蓋寫入這個檔案。
# 下次開啟程式或按下一關時，就會沿用最後一次設定。
INDICATOR_OPTIONS = ["MA5", "MA10", "MA20", "MA60", "MA120", "EMA20", "EMA60", "VWAP"]
SETTINGS_FILE = Path(__file__).with_name("tw_replay_user_settings.json")

DEFAULT_USER_SETTINGS = {
    "mode": "闖關模式",
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


def load_user_settings() -> dict:
    """讀取上一次使用者設定；如果檔案不存在或損壞，就用預設值。"""
    settings = DEFAULT_USER_SETTINGS.copy()

    try:
        if SETTINGS_FILE.exists():
            loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings.update(loaded)
    except Exception:
        pass

    valid_indicators = []
    for item in settings.get("selected_indicators", DEFAULT_USER_SETTINGS["selected_indicators"]):
        if item in INDICATOR_OPTIONS and item not in valid_indicators:
            valid_indicators.append(item)

    settings["selected_indicators"] = valid_indicators or DEFAULT_USER_SETTINGS["selected_indicators"]
    return settings


def save_user_settings(settings: dict) -> None:
    """覆蓋寫入目前設定。"""
    try:
        SETTINGS_FILE.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        st.warning(f"設定檔寫入失敗：{exc}")


def option_index(options: list[str], value: str, default: int = 0) -> int:
    """安全取得 selectbox/radio index。"""
    return options.index(value) if value in options else default


def clamp_number(value, min_value, max_value):
    """避免設定檔中的舊值超過 widget 範圍。"""
    try:
        return min(max(value, min_value), max_value)
    except Exception:
        return min_value

st.set_page_config(page_title="裸K TradingView 闖關復盤", layout="wide", initial_sidebar_state="expanded")
st.title("🎮 裸 K TradingView 風格闖關復盤 V6-settings")
st.caption("類 TradingView 畫線工具、均線、MACD、斐波那契；指標改用勾選並自動儲存設定。")
st.markdown("---")


def install_keyboard_shortcuts() -> None:
    components.html(
        """
        <script>
        (function () {
            const parentWindow = window.parent;
            const parentDoc = parentWindow.document;
            if (parentWindow.__tvReplayHotkeyInstalledV4) return;
            parentWindow.__tvReplayHotkeyInstalledV4 = true;
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


@st.cache_data(show_spinner="正在下載 K 線資料...")
def load_data(stock_ticker: str, stock_period: str, stock_interval: str) -> pd.DataFrame:
    data = yf.download(stock_ticker, period=stock_period, interval=stock_interval, auto_adjust=True, progress=False)
    if data is None or data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [col[0] for col in data.columns]
    df = data.reset_index()
    time_col = "Datetime" if "Datetime" in df.columns else "Date"
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.rename(columns={time_col: "Time"})
    rename_map = {}
    for col in df.columns:
        lower = str(col).lower()
        if lower == "open": rename_map[col] = "Open"
        elif lower == "high": rename_map[col] = "High"
        elif lower == "low": rename_map[col] = "Low"
        elif lower == "close": rename_map[col] = "Close"
        elif lower == "volume": rename_map[col] = "Volume"
    df = df.rename(columns=rename_map)
    df = df[["Time", "Open", "High", "Low", "Close", "Volume"]].dropna().reset_index(drop=True)
    df["Bar"] = np.arange(len(df))
    df["TimeStr"] = df["Time"].dt.strftime("%Y-%m-%d %H:%M")
    return df


def get_stock_name(stock_ticker: str) -> str:
    try:
        info = yf.Ticker(stock_ticker).info
        return info.get("longName", info.get("shortName", ""))
    except Exception:
        return ""


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
    return int(pd.Timestamp(value).timestamp())


def make_candle_data(df: pd.DataFrame, blind_mode: bool, challenge_start_idx: int) -> list[dict]:
    out = []
    for _, row in df.iterrows():
        bar = int(row["Bar"])
        out.append({
            "time": timestamp_seconds(row["Time"]),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "bar": bar,
            "label": f"D{bar - challenge_start_idx:+d}" if blind_mode else str(row["TimeStr"]),
        })
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
    colors = {"MA5":"#ffeb3b", "MA10":"#42a5f5", "MA20":"#ef5350", "MA60":"#ab47bc", "MA120":"#ffa726", "EMA20":"#26c6da", "EMA60":"#f06292", "VWAP":"#ffffff"}
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


def init_session_state() -> None:
    defaults = {
        "stock_code": "3037", "pending_stock_code": None, "current_idx": 80,
        "challenge_start_idx": 80, "challenge_end_idx": 200, "show_answer": False,
        "trade_log": [], "last_config_key": "", "cash": 1000000.0,
        "shares": 0, "avg_cost": 0.0, "realized_pnl": 0.0, "pending_new_challenge": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


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
    st.session_state.trade_log.append({
        "bar": int(row["Bar"]), "time": row["TimeStr"], "relative_bar": int(row["Bar"] - st.session_state.challenge_start_idx),
        "side": side, "shares": int(shares), "price": round(float(price), 4), "cash_after": round(float(st.session_state.cash), 2),
        "position_after": int(st.session_state.shares), "avg_cost_after": round(float(st.session_state.avg_cost), 4),
        "realized_pnl": round(float(st.session_state.realized_pnl), 2), "unrealized_pnl": round(float(unrealized_pnl), 2),
        "total_equity": round(float(total_equity), 2), "reason": reason,
    })


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
const candleData=__CANDLES__;const volumeData=__VOLUMES__;const indicatorPayload=__INDICATORS__;const markers=__MARKERS__;const macdPayload=__MACD__;const showMacd=__SHOW_MACD__;const drawingsKey=__DRAWINGS_KEY__;const chartSignature=__CHART_SIGNATURE__;
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
    drawings_key = f"tv_drawings_{ticker}_{interval}_{challenge_id}"
    inner_height = max(300, height - 44)
    if show_macd:
        main_chart_height = int(inner_height * 0.74)
        macd_chart_height = inner_height - main_chart_height
        macd_display = "block"
    else:
        main_chart_height = inner_height
        macd_chart_height = 0
        macd_display = "none"

    chart_signature = {
        "ticker": ticker,
        "interval": interval,
        "challenge_id": challenge_id,
        "selected_indicators": list(selected_indicators),
        "show_macd": bool(show_macd),
        "last_bar": int(visible_df["Bar"].iloc[-1]) if len(visible_df) else -1,
        "visible_len": int(len(visible_df)),
    }
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
        .replace("__DRAWINGS_KEY__", json.dumps(drawings_key, ensure_ascii=False))
        .replace("__CHART_SIGNATURE__", json.dumps(chart_signature, ensure_ascii=False)))
    components.html(html_code, height=height + 10, scrolling=False)


init_session_state()

saved_settings = load_user_settings()

# 將上次儲存的指標設定套進 session_state。
# 使用 checkbox key，下一關與重新開啟程式都會沿用最後一次勾選。
for indicator_name in INDICATOR_OPTIONS:
    checkbox_key = f"indicator_{indicator_name}"
    if checkbox_key not in st.session_state:
        st.session_state[checkbox_key] = indicator_name in saved_settings.get("selected_indicators", DEFAULT_USER_SETTINGS["selected_indicators"])

if "main_macd_checkbox" not in st.session_state:
    st.session_state.main_macd_checkbox = bool(saved_settings.get("show_macd", True))

if st.session_state.get("pending_stock_code") is not None:
    st.session_state.stock_code = st.session_state.pending_stock_code
    st.session_state.pending_stock_code = None

with st.sidebar:
    st.header("⚙️ 闖關設定")
    st.caption("目前版本：V6-settings（打勾指標 + 自動儲存設定）")
    mode_options = ["闖關模式", "自選練習"]
    mode = st.radio("模式", mode_options, index=option_index(mode_options, saved_settings.get("mode", "闖關模式")))
    stock_pool_text = st.text_area("隨機股票池", value="2330,2317,2454,2303,3037,3481,2603,2615,2002,2881,2882,2891,3711,2382,3231,2379,6669,2357,2368,2409", height=90)
    stock_pool = [code.strip() for code in stock_pool_text.replace("\n", ",").split(",") if code.strip()]
    if st.button("🎲 隨機開新關卡", use_container_width=True):
        if stock_pool:
            st.session_state.pending_stock_code = random.choice(stock_pool)
            st.session_state.pending_new_challenge = True
            st.rerun()
    raw_code = st.text_input("目前股票代號", key="stock_code")
    interval_map = {"日線": "1d", "60 分線": "60m", "30 分線": "30m", "15 分線": "15m", "5 分線": "5m"}
    interval_options = list(interval_map.keys())
    interval_label = st.selectbox(
        "K 線週期",
        interval_options,
        index=option_index(interval_options, saved_settings.get("interval_label", "日線")),
    )
    interval = interval_map[interval_label]
    period = "5y" if interval == "1d" else "60d"
    challenge_bars = st.slider("限定 K 數（日線 = 天數）", min_value=20, max_value=300, value=int(clamp_number(saved_settings.get("challenge_bars", 120), 20, 300)), step=10)
    target_return_pct = st.slider("過關目標報酬率 %", min_value=1.0, max_value=50.0, value=float(clamp_number(saved_settings.get("target_return_pct", 8.0), 1.0, 50.0)), step=0.5)
    initial_cash = st.number_input("初始資金", min_value=100000.0, max_value=10000000.0, value=float(clamp_number(saved_settings.get("initial_cash", 1000000.0), 100000.0, 10000000.0)), step=100000.0)
    lookback_bars = st.slider("起始回看 K 數", min_value=50, max_value=800, value=int(clamp_number(saved_settings.get("lookback_bars", 300), 50, 800)), step=10)
    blind_mode = st.checkbox("盲測模式：隱藏股票與日期", value=bool(saved_settings.get("blind_mode", True)))
    show_volume = st.checkbox("顯示成交量", value=bool(saved_settings.get("show_volume", True)))
    chart_height = st.slider("圖表高度", min_value=600, max_value=1200, value=int(clamp_number(saved_settings.get("chart_height", 920), 600, 1200)), step=20)
    st.markdown("---")
    if st.button("🔄 重設本關交易帳戶", use_container_width=True):
        reset_account(initial_cash)
        st.rerun()

raw_code = st.session_state.stock_code.strip()
ticker = raw_code if raw_code.endswith(".TW") or raw_code.endswith(".TWO") else f"{raw_code}.TW"
df = load_data(ticker, period, interval)
if df.empty:
    st.error("抓不到資料。請換股票代號、週期或資料範圍。")
    st.stop()
df = add_indicators(df)
config_key = f"{ticker}_{period}_{interval}_{challenge_bars}_{target_return_pct}_{initial_cash}_{mode}"
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
stock_name = "" if blind_mode else get_stock_name(ticker)
show_title = "隨機盲測標的" if blind_mode else f"{ticker.replace('.TW', '').replace('.TWO', '')} {stock_name}"
show_time = f"D+{bars_passed}" if blind_mode else current_row["TimeStr"]

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

st.markdown("### 🕹️ 重播控制")
replay_col1, replay_col2, replay_col3, replay_col4, replay_col5, replay_col6 = st.columns([1, 1.3, 1.8, 1.8, 1.3, 1])
with replay_col1:
    if st.button("⏮️ -10", use_container_width=True):
        st.session_state.current_idx = max(min_idx, st.session_state.current_idx - 10); st.session_state.show_answer = False; st.rerun()
with replay_col2:
    if st.button("⬅️ 上一根", use_container_width=True):
        st.session_state.current_idx = max(min_idx, st.session_state.current_idx - 1); st.session_state.show_answer = False; st.rerun()
with replay_col3:
    if st.button("➡️ 下一根", type="primary", use_container_width=True):
        st.session_state.current_idx = min(max_idx, st.session_state.current_idx + 1); st.session_state.show_answer = False; st.rerun()
with replay_col4:
    if st.button("👁️ 對答案 / 顯示終點", use_container_width=True):
        st.session_state.show_answer = not st.session_state.show_answer; st.rerun()
with replay_col5:
    if st.button("⏭️ +10", use_container_width=True):
        st.session_state.current_idx = min(max_idx, st.session_state.current_idx + 10); st.session_state.show_answer = False; st.rerun()
with replay_col6:
    if st.button("🎲 下一關", use_container_width=True):
        if stock_pool:
            st.session_state.pending_stock_code = random.choice(stock_pool)
        st.session_state.pending_new_challenge = True; st.rerun()
st.caption("快捷鍵：`←` 上一根，`→` 下一根。即使滑鼠點在圖表內，也可以使用左右鍵。")

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
        if ok: st.toast(msg)
        else: st.error(msg)
        st.rerun()
with t4:
    if st.button("🟢 賣出", use_container_width=True):
        ok, msg = sell_shares(current_row, lot_count, trade_note)
        if ok: st.toast(msg)
        else: st.error(msg)
        st.rerun()
with t5:
    if st.button("⚪ 全部平倉", use_container_width=True):
        ok, msg = sell_all(current_row, trade_note)
        if ok: st.toast(msg)
        else: st.error(msg)
        st.rerun()

st.markdown("#### 📊 指標設定")
st.caption(f"設定會自動覆蓋儲存到：`{SETTINGS_FILE.name}`；下一關與下次開啟會沿用。")

indicator_cols = st.columns(4)
selected_indicators = []
for idx, indicator_name in enumerate(INDICATOR_OPTIONS):
    checkbox_key = f"indicator_{indicator_name}"
    if indicator_cols[idx % 4].checkbox(indicator_name, key=checkbox_key):
        selected_indicators.append(indicator_name)

show_macd = st.checkbox("顯示 MACD", key="main_macd_checkbox")

current_user_settings = {
    "mode": mode,
    "interval_label": interval_label,
    "challenge_bars": int(challenge_bars),
    "target_return_pct": float(target_return_pct),
    "initial_cash": float(initial_cash),
    "lookback_bars": int(lookback_bars),
    "blind_mode": bool(blind_mode),
    "show_volume": bool(show_volume),
    "chart_height": int(chart_height),
    "selected_indicators": selected_indicators,
    "show_macd": bool(show_macd),
}
save_user_settings(current_user_settings)

visible_start = max(0, st.session_state.challenge_start_idx - lookback_bars + 1)
visible_end = st.session_state.challenge_end_idx if st.session_state.show_answer else st.session_state.current_idx
visible_df = df.iloc[visible_start: visible_end + 1]
challenge_id = f"{st.session_state.challenge_start_idx}_{st.session_state.challenge_end_idx}"
render_tv_chart(visible_df, visible_df, selected_indicators, show_volume, show_macd, st.session_state.trade_log, df, blind_mode, ticker, interval, st.session_state.challenge_start_idx, challenge_id, chart_height)

if selected_indicators:
    indicator_value_text = []
    for indicator_name in selected_indicators:
        value = current_row.get(indicator_name, np.nan)
        if pd.notna(value):
            indicator_value_text.append(f"{indicator_name}: {float(value):.2f}")
    if indicator_value_text:
        st.caption("目前指標數值｜" + "　".join(indicator_value_text))

st.info("畫圖方式：圖表上方選「趨勢線 / 水平線 / 矩形 / 斐波 / 文字」後直接點圖。刪除請切到「刪除」後點物件。")

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
    rename = {"relative_bar": "相對K數", "time": "時間", "side": "動作", "shares": "股數", "price": "價格", "position_after": "交易後持股", "avg_cost_after": "交易後均價", "realized_pnl": "已實現損益", "unrealized_pnl": "未實現損益", "total_equity": "總資產", "reason": "原因"}
    st.dataframe(trade_df[cols].rename(columns=rename), use_container_width=True, hide_index=True)
    st.download_button("下載買賣紀錄 CSV", data=trade_df.to_csv(index=False).encode("utf-8-sig"), file_name="naked_k_tv_trade_log.csv", mime="text/csv", use_container_width=True)
else:
    st.caption("尚未有買賣紀錄。")

with st.expander("目前 K 棒資料"):
    if blind_mode:
        k_info = {"相對K數": f"D+{bars_passed}", "Open": current_row["Open"], "High": current_row["High"], "Low": current_row["Low"], "Close": current_row["Close"], "Volume": current_row["Volume"]}
    else:
        k_info = {"時間": current_row["TimeStr"], "Open": current_row["Open"], "High": current_row["High"], "Low": current_row["Low"], "Close": current_row["Close"], "Volume": current_row["Volume"]}
    st.dataframe(pd.DataFrame([k_info]), use_container_width=True, hide_index=True)

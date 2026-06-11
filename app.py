# app.py
# BARREPLAY：類 TradingView 裸 K 闖關復盤系統
# Deployment version：V22 battle join no-refresh + host kick

import base64
import hashlib
import json
import os
import random
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
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
st.caption("V21：對戰房間新增手動刷新自動離房、防重複加入、房主踢人；保留即時同步與全房同步倒數。")
st.markdown("---")

# =========================================================
# 1. Per-user Settings: st.session_state + browser localStorage
# =========================================================
SETTINGS_KEY = "barreplay_user_settings_v1"
INDICATOR_OPTIONS = ["MA5", "MA10", "MA20", "MA60", "MA120", "EMA20", "EMA60", "VWAP"]

# 台股融券模擬參數：一般情境以融券保證金 90%、最低整戶擔保維持率 130% 作為訓練限制。
# 注意：實際交易仍會受個股是否可融券、停券、融券餘額、券源、處置股與券商規定影響。
TW_SHORT_MARGIN_RATE = 0.90
TW_MIN_MAINTENANCE_RATE = 1.30
TW_SAFE_MAINTENANCE_RATE = 1.66

# 對戰模式參數：同一房間號碼會產生相同題目。房主可在遊戲開始前設定關卡數。
BATTLE_DEFAULT_QUESTION_COUNT = 5
BATTLE_MIN_QUESTION_COUNT = 1
BATTLE_MAX_QUESTION_COUNT = 10
BATTLE_DEFAULT_TIME_LIMIT_MINUTES = 8
BATTLE_MIN_TIME_LIMIT_MINUTES = 1
BATTLE_MAX_TIME_LIMIT_MINUTES = 30
BATTLE_STATE_FILE = os.path.join(tempfile.gettempdir(), "barreplay_battle_rooms_v22.json")
BATTLE_MEMBERSHIP_KEY = "barreplay_battle_membership_v22"
BATTLE_INTERNAL_RELOAD_KEY = "barreplay_internal_reload_v22"
BATTLE_DEFAULT_POOL_TEXT = "2330,2317,2454,2303,3037,3481,2603,2615,2002,2881,2882,2891,3711,2382,3231,2379,6669,2357,2368,2409"

DEFAULT_SETTINGS = {
    "mode": "闖關模式",
    "stock_pool_text": BATTLE_DEFAULT_POOL_TEXT,
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

    if settings["mode"] not in ["闖關模式", "自選練習", "對戰模式"]:
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


def install_battle_live_sync(enabled: bool, seconds: float = 1.0) -> None:
    """用瀏覽器定時重新載入 Streamlit 頁面，讓等待室、開始狀態、玩家在線與倒數時間自動同步。"""
    if not enabled:
        return
    seconds = max(0.8, float(seconds))
    components.html(
        f"""
        <script>
        (function() {{
            const delayMs = {int(seconds * 1000)};
            const parentWindow = window.parent;
            const timerKey = "__barreplay_battle_live_sync_timer_v22";
            if (parentWindow[timerKey]) {{
                clearTimeout(parentWindow[timerKey]);
            }}
            parentWindow[timerKey] = setTimeout(function() {{
                try {{
                    const url = new URL(parentWindow.location.href);
                    try {{ parentWindow.localStorage.setItem("barreplay_internal_reload_v22", String(Date.now())); }} catch (e) {{}}
                    url.searchParams.set("battle_live_tick", String(Date.now()));
                    parentWindow.location.replace(url.toString());
                }} catch (e) {{
                    parentWindow.location.reload();
                }}
            }}, delayMs);
        }})();
        </script>
        """,
        height=0,
    )


def install_battle_focus_mode(enabled: bool) -> None:
    """對戰開始後把畫面壓成圖表優先的專注模式，並自動捲到 K 線圖。"""
    if not enabled:
        return
    components.html(
        """
        <script>
        (function(){
            const doc = window.parent.document;
            if (!doc.getElementById('barreplay-battle-focus-style-v22')) {
                const style = doc.createElement('style');
                style.id = 'barreplay-battle-focus-style-v22';
                style.innerHTML = `
                    header[data-testid="stHeader"]{display:none!important;}
                    div[data-testid="stToolbar"]{display:none!important;}
                    div.block-container{padding-top:0.25rem!important;padding-left:0.45rem!important;padding-right:0.45rem!important;max-width:100%!important;}
                    #MainMenu, footer{display:none!important;}
                `;
                doc.head.appendChild(style);
            }
        })();
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
        "short_sale_proceeds": 0.0,  # 融券賣出價款，凍結為擔保品，不可拿來繼續無限放空。
        "short_margin": 0.0,  # 融券保證金，放空時由可用現金扣除。
        "pending_new_challenge": False,
        "persisted_selected_indicators": DEFAULT_SETTINGS["selected_indicators"].copy(),
        "persisted_show_macd": DEFAULT_SETTINGS["show_macd"],
        "last_loaded_ticker": "",
        "last_yfinance_error": "",
        "battle_room_code": "ROOM001",
        "battle_player_name": "",
        "battle_question_no": 1,
        "battle_room_question_count": BATTLE_DEFAULT_QUESTION_COUNT,
        "battle_time_limit_minutes": BATTLE_DEFAULT_TIME_LIMIT_MINUTES,
        "battle_last_submit_message": "",
        "battle_room_joined": False,
        "battle_joined_room_code": "",
        "battle_room_owner": False,
        "battle_room_notice": "",
        "battle_session_id": str(uuid.uuid4()),
        "battle_kicked_notice": "",
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

        st.session_state.persisted_selected_indicators = [name for name in INDICATOR_OPTIONS if name in selected]
        st.session_state.persisted_show_macd = bool(loaded.get("show_macd", DEFAULT_SETTINGS["show_macd"]))
        st.session_state.settings_initialized = True


def collect_current_settings() -> dict[str, Any]:
    indicator_keys = [f"setting_indicator_{name}" for name in INDICATOR_OPTIONS]
    has_any_indicator_key = any(key in st.session_state for key in indicator_keys)

    if has_any_indicator_key:
        selected_indicators = [
            name for name in INDICATOR_OPTIONS
            if st.session_state.get(f"setting_indicator_{name}", False)
        ]
    else:
        selected_indicators = st.session_state.get(
            "persisted_selected_indicators",
            DEFAULT_SETTINGS["selected_indicators"],
        )

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



# =========================================================
# 1.5 Battle Mode Helpers
# =========================================================
def parse_stock_codes(text_value: str) -> list[str]:
    codes = [code.strip() for code in str(text_value).replace("\n", ",").split(",") if code.strip()]
    return list(dict.fromkeys(codes))


def normalize_room_code(room_code: str) -> str:
    cleaned = "".join(ch for ch in str(room_code).strip().upper() if ch.isalnum() or ch in ["-", "_"])
    return cleaned or "ROOM001"


def normalize_player_name(player_name: str) -> str:
    # 玩家名稱不能再自動變成 Player；對戰模式會檢查必填。
    cleaned = str(player_name).strip()
    return cleaned[:24]


def is_valid_player_name(player_name: str) -> bool:
    name = normalize_player_name(player_name)
    return len(name) >= 1 and name.lower() not in {"player", "guest", "匿名", "未命名"}


def stable_hash_int(text_value: str) -> int:
    digest = hashlib.sha256(str(text_value).encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def get_room_question_count_from_data(room_data: dict[str, Any] | None) -> int:
    if not isinstance(room_data, dict):
        return BATTLE_DEFAULT_QUESTION_COUNT
    try:
        return int(np.clip(int(room_data.get("question_count", BATTLE_DEFAULT_QUESTION_COUNT)), BATTLE_MIN_QUESTION_COUNT, BATTLE_MAX_QUESTION_COUNT))
    except Exception:
        return BATTLE_DEFAULT_QUESTION_COUNT


def get_room_time_limit_from_data(room_data: dict[str, Any] | None) -> int:
    if not isinstance(room_data, dict):
        return BATTLE_DEFAULT_TIME_LIMIT_MINUTES
    try:
        return int(np.clip(int(room_data.get("time_limit_minutes", BATTLE_DEFAULT_TIME_LIMIT_MINUTES)), BATTLE_MIN_TIME_LIMIT_MINUTES, BATTLE_MAX_TIME_LIMIT_MINUTES))
    except Exception:
        return BATTLE_DEFAULT_TIME_LIMIT_MINUTES


def build_battle_questions(room_code: str, interval_label: str, challenge_bars: int, question_count: int | None = None) -> list[dict[str, Any]]:
    """用房間號碼與房間設定決定題目；同房號、同設定會拿到相同題目。"""
    room = normalize_room_code(room_code)
    q_count = int(np.clip(int(question_count or BATTLE_DEFAULT_QUESTION_COUNT), BATTLE_MIN_QUESTION_COUNT, BATTLE_MAX_QUESTION_COUNT))
    base_pool = parse_stock_codes(BATTLE_DEFAULT_POOL_TEXT)
    rng = random.Random(stable_hash_int(f"BARREPLAY_BATTLE|{room}|{interval_label}|{challenge_bars}|QCOUNT{q_count}"))

    if len(base_pool) >= q_count:
        selected_codes = rng.sample(base_pool, q_count)
    else:
        selected_codes = [rng.choice(base_pool) for _ in range(q_count)]

    questions = []
    for idx, code in enumerate(selected_codes, start=1):
        questions.append(
            {
                "question_no": idx,
                "stock_code": code,
                "seed": f"BARREPLAY_BATTLE|{room}|Q{idx}|{code}|{interval_label}|{challenge_bars}|QCOUNT{q_count}",
            }
        )
    return questions


def load_battle_state() -> dict[str, Any]:
    try:
        if os.path.exists(BATTLE_STATE_FILE):
            with open(BATTLE_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("rooms", {})
                return data
    except Exception:
        pass
    return {"rooms": {}}


def save_battle_state(state: dict[str, Any]) -> None:
    try:
        tmp_file = BATTLE_STATE_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, BATTLE_STATE_FILE)
    except Exception as e:
        st.warning(f"對戰成績儲存失敗：{e}")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_iso_localish(iso_text: str) -> str:
    if not iso_text:
        return ""
    return str(iso_text).replace("T", " ")[:19]


def get_battle_room_meta(room_code: str) -> dict[str, Any]:
    room = normalize_room_code(room_code)
    state = load_battle_state()
    data = state.get("rooms", {}).get(room, {})
    return data if isinstance(data, dict) else {}


def is_battle_room_started(room_data: dict[str, Any] | None) -> bool:
    if not isinstance(room_data, dict):
        return False
    return str(room_data.get("status", "waiting")) in ["started", "ended"]


def is_battle_room_waiting(room_data: dict[str, Any] | None) -> bool:
    if not isinstance(room_data, dict):
        return False
    return str(room_data.get("status", "waiting")) == "waiting"


def submit_battle_score(
    room_code: str,
    player_name: str,
    question_no: int,
    final_equity: float,
    return_pct: float,
    ticker: str,
    interval_label: str,
    challenge_bars: int,
    trade_count: int,
) -> None:
    room = normalize_room_code(room_code)
    player = normalize_player_name(player_name)
    q_key = str(int(question_no))
    state = load_battle_state()
    room_data = state.setdefault("rooms", {}).setdefault(
        room,
        {
            "room_code": room,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "waiting",
            "question_count": BATTLE_DEFAULT_QUESTION_COUNT,
            "time_limit_minutes": BATTLE_DEFAULT_TIME_LIMIT_MINUTES,
            "players": {},
            "active_players": {},
        },
    )
    room_question_count = get_room_question_count_from_data(room_data)
    room_data.setdefault("active_players", {})
    room_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    room_data.setdefault("interval_label", interval_label)
    room_data.setdefault("challenge_bars", int(challenge_bars))
    room_data.setdefault("question_count", room_question_count)

    player_data = room_data.setdefault("players", {}).setdefault(
        player,
        {
            "player_name": player,
            "scores": {},
            "joined_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    now_submit = datetime.now(timezone.utc).isoformat()
    room_data.setdefault("active_players", {})[player] = {
        "player_name": player,
        "session_id": str(st.session_state.get("battle_session_id", "")),
        "last_seen": now_submit,
    }
    player_data["updated_at"] = now_submit
    player_data["last_seen"] = now_submit
    player_data.setdefault("scores", {})[q_key] = {
        "question_no": int(question_no),
        "ticker": str(ticker),
        "final_equity": round(float(final_equity), 2),
        "return_pct": round(float(return_pct), 4),
        "trade_count": int(trade_count),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    save_battle_state(state)


def build_battle_leaderboard(room_code: str) -> pd.DataFrame:
    room = normalize_room_code(room_code)
    state = load_battle_state()
    room_data = state.get("rooms", {}).get(room, {})
    players = room_data.get("players", {}) if isinstance(room_data, dict) else {}
    question_count = get_room_question_count_from_data(room_data)
    rows = []

    for player, pdata in players.items():
        scores = pdata.get("scores", {}) if isinstance(pdata, dict) else {}
        completed = len(scores)
        total_equity_sum = sum(float(item.get("final_equity", 0.0)) for item in scores.values())
        avg_return = np.mean([float(item.get("return_pct", 0.0)) for item in scores.values()]) if scores else 0.0
        latest_submit = max([str(item.get("submitted_at", "")) for item in scores.values()], default="")
        rows.append(
            {
                "玩家": player,
                "完成題數": completed,
                "總金額": total_equity_sum if completed >= question_count else np.nan,
                "目前累計金額": total_equity_sum,
                "平均報酬率%": round(float(avg_return), 2),
                "最後提交": latest_submit.replace("T", " ")[:19] if latest_submit else "",
            }
        )

    if not rows:
        return pd.DataFrame(columns=["排名", "玩家", "完成題數", "總金額", "目前累計金額", "平均報酬率%", "最後提交"])

    df_rank = pd.DataFrame(rows)
    df_rank["已完成全部"] = df_rank["完成題數"] >= question_count
    df_rank = df_rank.sort_values(
        by=["已完成全部", "總金額", "完成題數", "目前累計金額"],
        ascending=[False, False, False, False],
        na_position="last",
    ).drop(columns=["已完成全部"]).reset_index(drop=True)
    df_rank.insert(0, "排名", np.arange(1, len(df_rank) + 1))
    return df_rank


def get_player_battle_scores(room_code: str, player_name: str) -> dict[str, Any]:
    state = load_battle_state()
    room = normalize_room_code(room_code)
    player = normalize_player_name(player_name)
    return state.get("rooms", {}).get(room, {}).get("players", {}).get(player, {}).get("scores", {})


def create_battle_room(
    room_code: str,
    player_name: str,
    interval_label: str,
    challenge_bars: int,
    question_count: int,
    time_limit_minutes: int,
    initial_cash: float,
    target_return_pct: float,
) -> tuple[bool, str]:
    room = normalize_room_code(room_code)
    player = normalize_player_name(player_name)
    if not is_valid_player_name(player):
        return False, "請先輸入有效玩家名稱，不能空白，也不要使用 Player / Guest / 匿名。"

    now = _utc_now_iso()
    state = load_battle_state()
    rooms = state.setdefault("rooms", {})

    if room in rooms:
        return False, f"房間 {room} 已存在，請改按『加入房間』。"

    rooms[room] = {
        "room_code": room,
        "created_by": player,
        "created_at": now,
        "updated_at": now,
        "status": "waiting",
        "started_at": "",
        "started_by": "",
        "interval_label": interval_label,
        "challenge_bars": int(challenge_bars),
        "question_count": int(np.clip(int(question_count), BATTLE_MIN_QUESTION_COUNT, BATTLE_MAX_QUESTION_COUNT)),
        "time_limit_minutes": int(np.clip(int(time_limit_minutes), BATTLE_MIN_TIME_LIMIT_MINUTES, BATTLE_MAX_TIME_LIMIT_MINUTES)),
        "initial_cash": float(initial_cash),
        "target_return_pct": float(target_return_pct),
        "players": {},
        "active_players": {},
    }
    save_battle_state(state)
    register_battle_presence(room, player, interval_label, challenge_bars)
    return True, f"已建立並加入房間 {room}。房主可在開始前調整關卡數與時間。"


def join_battle_room(room_code: str, player_name: str, interval_label: str, challenge_bars: int) -> tuple[bool, str]:
    room = normalize_room_code(room_code)
    player = normalize_player_name(player_name)
    if not is_valid_player_name(player):
        return False, "請先輸入有效玩家名稱，不能空白，也不要使用 Player / Guest / 匿名。"

    state = load_battle_state()
    rooms = state.setdefault("rooms", {})

    if room not in rooms:
        return False, f"找不到房間 {room}，請確認房號或先建立房間。"

    room_data = rooms[room]
    kicked = room_data.get("kicked_players", {}) if isinstance(room_data.get("kicked_players", {}), dict) else {}
    if player in kicked:
        return False, f"你已被房主移出房間 {room}，不能重新加入。"
    if not is_battle_room_waiting(room_data):
        return False, f"房間 {room} 已經開始，為了公平不能中途加入。"

    register_battle_presence(room, player, interval_label, challenge_bars)
    return True, f"已加入房間 {room}。等待房主按開始。"


def update_battle_room_settings(
    room_code: str,
    player_name: str,
    question_count: int,
    time_limit_minutes: int,
    interval_label: str,
    challenge_bars: int,
    initial_cash: float,
    target_return_pct: float,
) -> tuple[bool, str]:
    room = normalize_room_code(room_code)
    player = normalize_player_name(player_name)
    state = load_battle_state()
    room_data = state.get("rooms", {}).get(room)
    if not isinstance(room_data, dict):
        return False, "找不到房間，請先建立房間。"
    if room_data.get("created_by", "") != player:
        return False, "只有房主可以修改房間設定。"
    if not is_battle_room_waiting(room_data):
        return False, "遊戲已開始，不能再修改關卡數或時間。"

    room_data["question_count"] = int(np.clip(int(question_count), BATTLE_MIN_QUESTION_COUNT, BATTLE_MAX_QUESTION_COUNT))
    room_data["time_limit_minutes"] = int(np.clip(int(time_limit_minutes), BATTLE_MIN_TIME_LIMIT_MINUTES, BATTLE_MAX_TIME_LIMIT_MINUTES))
    room_data["interval_label"] = interval_label
    room_data["challenge_bars"] = int(challenge_bars)
    room_data["initial_cash"] = float(initial_cash)
    room_data["target_return_pct"] = float(target_return_pct)
    room_data["updated_at"] = _utc_now_iso()
    save_battle_state(state)
    return True, "房間設定已儲存。"


def start_battle_room(room_code: str, player_name: str) -> tuple[bool, str]:
    room = normalize_room_code(room_code)
    player = normalize_player_name(player_name)
    state = load_battle_state()
    room_data = state.get("rooms", {}).get(room)
    if not isinstance(room_data, dict):
        return False, "找不到房間，請先建立房間。"
    if room_data.get("created_by", "") != player:
        return False, "只有房主可以開始遊戲。"
    if not is_battle_room_waiting(room_data):
        return False, "遊戲已經開始。"

    players = room_data.get("players", {}) if isinstance(room_data.get("players", {}), dict) else {}
    if len(players) < 1:
        return False, "房間內沒有玩家，無法開始。"

    now = _utc_now_iso()
    room_data["status"] = "started"
    room_data["started_at"] = now
    room_data["started_by"] = player
    room_data["updated_at"] = now
    room_data["question_count"] = get_room_question_count_from_data(room_data)
    room_data["time_limit_minutes"] = get_room_time_limit_from_data(room_data)
    room_data["sync_mode"] = "global_started_at_v20"
    save_battle_state(state)
    return True, "遊戲已開始！所有玩家從第 1 關開始。"


def register_battle_presence(room_code: str, player_name: str, interval_label: str, challenge_bars: int) -> None:
    room = normalize_room_code(room_code)
    player = normalize_player_name(player_name)
    if not is_valid_player_name(player):
        return
    if is_player_kicked(room, player):
        st.session_state.battle_room_joined = False
        st.session_state.battle_joined_room_code = ""
        st.session_state.battle_room_owner = False
        st.session_state.battle_kicked_notice = f"你已被房主移出房間 {room}。"
        return
    now = _utc_now_iso()
    session_id = str(st.session_state.get("battle_session_id", "")) or str(uuid.uuid4())
    st.session_state.battle_session_id = session_id

    state = load_battle_state()
    rooms = state.setdefault("rooms", {})
    room_data = rooms.setdefault(
        room,
        {
            "room_code": room,
            "created_by": player,
            "created_at": now,
            "status": "waiting",
            "question_count": BATTLE_DEFAULT_QUESTION_COUNT,
            "time_limit_minutes": BATTLE_DEFAULT_TIME_LIMIT_MINUTES,
            "players": {},
            "active_players": {},
        },
    )
    room_data["updated_at"] = now
    room_data.setdefault("interval_label", interval_label)
    room_data.setdefault("challenge_bars", int(challenge_bars))
    room_data.setdefault("question_count", BATTLE_DEFAULT_QUESTION_COUNT)
    room_data.setdefault("time_limit_minutes", BATTLE_DEFAULT_TIME_LIMIT_MINUTES)
    room_data.setdefault("status", "waiting")
    room_data.setdefault("active_players", {})[player] = {
        "player_name": player,
        "session_id": session_id,
        "last_seen": now,
    }
    player_data = room_data.setdefault("players", {}).setdefault(
        player,
        {
            "player_name": player,
            "scores": {},
            "joined_at": now,
        },
    )
    player_data["last_seen"] = now
    player_data.setdefault("scores", {})
    save_battle_state(state)


def is_player_joined_room(room_code: str, player_name: str) -> bool:
    room = normalize_room_code(room_code)
    player = normalize_player_name(player_name)
    if not (
        bool(st.session_state.get("battle_room_joined", False))
        and st.session_state.get("battle_joined_room_code", "") == room
        and normalize_player_name(st.session_state.get("battle_player_name", player)) == player
        and is_valid_player_name(player)
    ):
        return False
    room_data = get_battle_room_meta(room)
    players = room_data.get("players", {}) if isinstance(room_data, dict) else {}
    kicked = room_data.get("kicked_players", {}) if isinstance(room_data, dict) else {}
    return player in players and player not in kicked


def build_battle_room_players_df(room_code: str) -> pd.DataFrame:
    room_data = get_battle_room_meta(room_code)
    players = room_data.get("players", {}) if isinstance(room_data, dict) else {}
    active_players = room_data.get("active_players", {}) if isinstance(room_data, dict) else {}
    owner = room_data.get("created_by", "")
    question_count = get_room_question_count_from_data(room_data)
    now_ts = datetime.now(timezone.utc).timestamp()
    rows = []

    for player, pdata in players.items():
        if not isinstance(pdata, dict):
            continue
        active_data = active_players.get(player, {}) if isinstance(active_players, dict) else {}
        last_seen = str(active_data.get("last_seen") or pdata.get("last_seen") or pdata.get("updated_at") or pdata.get("joined_at") or "")
        online = False
        try:
            online = (now_ts - datetime.fromisoformat(last_seen).timestamp()) <= 180
        except Exception:
            online = False
        scores = pdata.get("scores", {}) if isinstance(pdata.get("scores", {}), dict) else {}
        rows.append(
            {
                "狀態": "在線" if online else "離線",
                "玩家": player,
                "角色": "房主" if player == owner else "玩家",
                "完成題數": f"{len(scores)} / {question_count}",
                "加入時間": _format_iso_localish(str(pdata.get("joined_at", ""))),
                "最後在線": _format_iso_localish(last_seen),
            }
        )

    if not rows:
        return pd.DataFrame(columns=["狀態", "玩家", "角色", "完成題數", "加入時間", "最後在線"])

    df_players = pd.DataFrame(rows)
    df_players["_online_sort"] = df_players["狀態"].map({"在線": 0, "離線": 1}).fillna(2)
    df_players["_role_sort"] = df_players["角色"].map({"房主": 0, "玩家": 1}).fillna(2)
    return df_players.sort_values(["_online_sort", "_role_sort", "玩家"]).drop(columns=["_online_sort", "_role_sort"]).reset_index(drop=True)


def get_question_timer_key(room_code: str, player_name: str, question_no: int) -> str:
    room = normalize_room_code(room_code)
    player = normalize_player_name(player_name).replace(" ", "_") or "unknown"
    return f"battle_question_started_at__{room}__{player}__{int(question_no)}"


def ensure_question_timer(room_code: str, player_name: str, question_no: int, room_data: dict[str, Any] | None) -> str:
    key = get_question_timer_key(room_code, player_name, question_no)
    if key not in st.session_state:
        if int(question_no) == 1 and isinstance(room_data, dict) and room_data.get("started_at"):
            st.session_state[key] = str(room_data.get("started_at"))
        else:
            st.session_state[key] = _utc_now_iso()
    return str(st.session_state[key])


def reset_question_timer(room_code: str, player_name: str, question_no: int) -> None:
    st.session_state[get_question_timer_key(room_code, player_name, question_no)] = _utc_now_iso()


def get_question_time_status(room_code: str, player_name: str, question_no: int, room_data: dict[str, Any] | None) -> dict[str, Any]:
    limit_minutes = get_room_time_limit_from_data(room_data)
    started_iso = ensure_question_timer(room_code, player_name, question_no, room_data)
    try:
        started_dt = datetime.fromisoformat(started_iso)
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
    except Exception:
        started_dt = datetime.now(timezone.utc)
        st.session_state[get_question_timer_key(room_code, player_name, question_no)] = started_dt.isoformat()
    deadline = started_dt + timedelta(minutes=limit_minutes)
    now = datetime.now(timezone.utc)
    left = int((deadline - now).total_seconds())
    return {
        "started_at": started_dt,
        "deadline": deadline,
        "limit_minutes": limit_minutes,
        "left_seconds": max(0, left),
        "time_up": left <= 0,
        "time_text": f"{max(0, left)//60:02d}:{max(0, left)%60:02d}",
    }


def get_room_start_datetime(room_data: dict[str, Any] | None) -> datetime | None:
    if not isinstance(room_data, dict) or not room_data.get("started_at"):
        return None
    try:
        dt = datetime.fromisoformat(str(room_data.get("started_at")))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def get_global_battle_time_status(room_data: dict[str, Any] | None, question_no: int | None = None) -> dict[str, Any]:
    """依房間 started_at 計算全房同步題號與倒數時間。"""
    question_count = get_room_question_count_from_data(room_data)
    limit_minutes = get_room_time_limit_from_data(room_data)
    limit_seconds = max(1, int(limit_minutes * 60))
    started_dt = get_room_start_datetime(room_data)

    if started_dt is None:
        return {
            "started_at": None,
            "question_no": 1,
            "scheduled_question_no": 1,
            "limit_minutes": limit_minutes,
            "left_seconds": limit_seconds,
            "elapsed_seconds": 0,
            "time_up": False,
            "game_over": False,
            "time_text": f"{limit_seconds//60:02d}:{limit_seconds%60:02d}",
        }

    now = datetime.now(timezone.utc)
    elapsed_seconds = max(0, int((now - started_dt).total_seconds()))
    scheduled_question_no = min(question_count, elapsed_seconds // limit_seconds + 1)
    if question_no is None:
        question_no = scheduled_question_no
    question_no = int(np.clip(int(question_no), 1, question_count))
    question_start = started_dt + timedelta(seconds=(question_no - 1) * limit_seconds)
    question_deadline = question_start + timedelta(seconds=limit_seconds)
    left = int((question_deadline - now).total_seconds())
    total_duration = question_count * limit_seconds
    game_over = elapsed_seconds >= total_duration
    left_clamped = max(0, left)
    return {
        "started_at": started_dt,
        "question_start": question_start,
        "deadline": question_deadline,
        "question_no": question_no,
        "scheduled_question_no": int(scheduled_question_no),
        "limit_minutes": limit_minutes,
        "left_seconds": left_clamped,
        "elapsed_seconds": elapsed_seconds,
        "time_up": left <= 0,
        "game_over": bool(game_over),
        "time_text": f"{left_clamped//60:02d}:{left_clamped%60:02d}",
    }


def mark_battle_room_ended_if_needed(room_code: str, room_data: dict[str, Any] | None) -> None:
    if not isinstance(room_data, dict) or not is_battle_room_started(room_data):
        return
    status = get_global_battle_time_status(room_data)
    if not status.get("game_over"):
        return
    room = normalize_room_code(room_code)
    state = load_battle_state()
    saved = state.get("rooms", {}).get(room)
    if not isinstance(saved, dict):
        return
    if saved.get("status") != "ended":
        saved["status"] = "ended"
        saved["ended_at"] = _utc_now_iso()
        saved["updated_at"] = _utc_now_iso()
        save_battle_state(state)



def get_battle_winner_summary(room_code: str) -> dict[str, Any]:
    room_data = get_battle_room_meta(room_code)
    question_count = get_room_question_count_from_data(room_data)
    players = room_data.get("players", {}) if isinstance(room_data, dict) else {}
    leaderboard = build_battle_leaderboard(room_code)
    if leaderboard.empty:
        return {"has_winner": False, "all_finished": False, "message": ""}

    completed_df = leaderboard[leaderboard["完成題數"] >= question_count].copy()
    if completed_df.empty:
        return {"has_winner": False, "all_finished": False, "message": ""}

    completed_df = completed_df.sort_values("總金額", ascending=False).reset_index(drop=True)
    winner = str(completed_df.iloc[0]["玩家"])
    winner_amount = float(completed_df.iloc[0]["總金額"])
    if len(completed_df) >= 2:
        second = str(completed_df.iloc[1]["玩家"])
        second_amount = float(completed_df.iloc[1]["總金額"])
        margin = winner_amount - second_amount
        message = f"🏆 {winner} 獲勝，目前總金額 {winner_amount:,.0f} 元，贏第二名 {second} {margin:,.0f} 元。"
    else:
        second = ""
        margin = 0.0
        message = f"🏆 {winner} 暫時領先，目前總金額 {winner_amount:,.0f} 元。"

    finished_players = 0
    for pdata in players.values():
        scores = pdata.get("scores", {}) if isinstance(pdata, dict) else {}
        if len(scores) >= question_count:
            finished_players += 1
    all_finished = len(players) > 0 and finished_players >= len(players)
    return {
        "has_winner": True,
        "all_finished": all_finished,
        "winner": winner,
        "winner_amount": winner_amount,
        "second": second,
        "margin": margin,
        "message": message,
    }


def remove_battle_player(room_code: str, player_name: str, session_id: str | None = None, reason: str = "refresh") -> tuple[bool, str]:
    """從房間移除玩家，避免同一瀏覽器重新整理後留下舊玩家紀錄；房主踢人也共用此函式。"""
    room = normalize_room_code(room_code)
    player = normalize_player_name(player_name)
    if not room or not player:
        return False, "房間或玩家名稱不完整。"

    state = load_battle_state()
    room_data = state.get("rooms", {}).get(room)
    if not isinstance(room_data, dict):
        return False, f"找不到房間 {room}。"

    players = room_data.setdefault("players", {})
    active_players = room_data.setdefault("active_players", {})
    existed = player in players or player in active_players

    old_session = ""
    try:
        old_session = str(active_players.get(player, {}).get("session_id", ""))
    except Exception:
        old_session = ""

    # refresh cleanup 通常帶有舊 session_id；如果 active session 已經不同，仍允許清除同名舊玩家，避免殘留。
    if player in active_players:
        active_players.pop(player, None)
    if player in players:
        players.pop(player, None)

    now = _utc_now_iso()
    removed = room_data.setdefault("removed_players", {})
    removed[player] = {
        "player_name": player,
        "removed_at": now,
        "reason": reason,
        "session_id": str(session_id or old_session),
    }

    if reason == "kicked":
        kicked = room_data.setdefault("kicked_players", {})
        kicked[player] = {
            "player_name": player,
            "kicked_at": now,
            "reason": "房主移出房間",
        }

    room_data["updated_at"] = now
    save_battle_state(state)
    if existed:
        return True, f"已將 {player} 從房間 {room} 移除。"
    return True, f"{player} 不在房間 {room} 內，已清理殘留狀態。"


def kick_battle_player(room_code: str, owner_name: str, target_player_name: str) -> tuple[bool, str]:
    room = normalize_room_code(room_code)
    owner = normalize_player_name(owner_name)
    target = normalize_player_name(target_player_name)
    if not target:
        return False, "請選擇要踢出的玩家。"

    state = load_battle_state()
    room_data = state.get("rooms", {}).get(room)
    if not isinstance(room_data, dict):
        return False, "找不到房間。"
    if room_data.get("created_by", "") != owner:
        return False, "只有房主可以踢出玩家。"
    if target == owner:
        return False, "不能踢出房主自己。"

    ok, msg = remove_battle_player(room, target, reason="kicked")
    return ok, f"已踢出玩家：{target}" if ok else msg


def is_player_kicked(room_code: str, player_name: str) -> bool:
    room = normalize_room_code(room_code)
    player = normalize_player_name(player_name)
    room_data = get_battle_room_meta(room)
    kicked = room_data.get("kicked_players", {}) if isinstance(room_data, dict) else {}
    return player in kicked


def handle_refresh_cleanup_from_query() -> None:
    """若瀏覽器手動重新整理造成新 session，先移除 localStorage 記錄的舊房間成員。"""
    room = st.query_params.get("br_cleanup_room")
    player = st.query_params.get("br_cleanup_player")
    old_session = st.query_params.get("br_cleanup_session")
    cleanup_for = st.query_params.get("br_cleanup_for_session")
    current_session = str(st.session_state.get("battle_session_id", ""))
    if not room or not player or not cleanup_for or cleanup_for != current_session:
        return
    done_key = f"battle_refresh_cleanup_done__{cleanup_for}"
    if st.session_state.get(done_key):
        return
    remove_battle_player(room, player, session_id=old_session, reason="refresh")
    st.session_state[done_key] = True
    st.session_state.battle_room_joined = False
    st.session_state.battle_joined_room_code = ""
    st.session_state.battle_room_owner = False
    st.session_state.battle_room_notice = "偵測到重新整理，已將你從原房間移除；請重新加入房間。"


def install_refresh_cleanup_probe() -> None:
    """前端偵測 localStorage 上一次加入的房間；若 session_id 改變，回傳給 Python 清除舊成員。"""
    current_session = str(st.session_state.get("battle_session_id", ""))
    components.html(
        f"""
        <script>
        (function() {{
            const parentWindow = window.parent;
            const membershipKey = {json.dumps(BATTLE_MEMBERSHIP_KEY)};
            const internalReloadKey = {json.dumps(BATTLE_INTERNAL_RELOAD_KEY)};
            const currentSession = {json.dumps(current_session)};
            try {{
                // 重要：Streamlit 按鈕本身也會造成 rerun。
                // 先監聽使用者點擊按鈕/輸入操作，把這類 rerun 標記為「App 內部更新」，
                // 避免建立房間或加入房間後，被 refresh-cleanup 誤判成瀏覽器手動重新整理而踢出。
                const guardKey = "__barreplay_internal_action_guard_v22";
                if (!parentWindow[guardKey]) {{
                    parentWindow[guardKey] = true;
                    const markInternalAction = function() {{
                        try {{ parentWindow.localStorage.setItem(internalReloadKey, String(Date.now())); }} catch (e) {{}}
                    }};
                    parentWindow.document.addEventListener("click", function(e) {{
                        const target = e.target;
                        if (!target || !target.closest) return;
                        if (target.closest("button, a, [role='button'], input, textarea, select")) {{
                            markInternalAction();
                        }}
                    }}, true);
                    parentWindow.document.addEventListener("keydown", function(e) {{
                        if (e.key === "Enter" || e.key === " ") markInternalAction();
                    }}, true);
                }}

                const raw = parentWindow.localStorage.getItem(membershipKey);
                if (!raw) return;
                const old = JSON.parse(raw);
                if (!old || !old.room || !old.player || !old.session_id) return;
                if (old.session_id === currentSession) return;

                const lastInternal = Number(parentWindow.localStorage.getItem(internalReloadKey) || "0");
                const internalReloadRecently = lastInternal && ((Date.now() - lastInternal) < 20000);
                if (internalReloadRecently) return;

                const url = new URL(parentWindow.location.href);
                if (url.searchParams.get("br_cleanup_for_session") === currentSession) return;
                url.searchParams.set("br_cleanup_room", old.room);
                url.searchParams.set("br_cleanup_player", old.player);
                url.searchParams.set("br_cleanup_session", old.session_id);
                url.searchParams.set("br_cleanup_for_session", currentSession);
                url.searchParams.set("br_cleanup_ts", String(Date.now()));
                parentWindow.location.replace(url.toString());
            }} catch (e) {{
                console.log("BARREPLAY refresh cleanup probe failed:", e);
            }}
        }})();
        </script>
        """,
        height=0,
    )


def install_battle_membership_storage(room_code: str, player_name: str, joined: bool) -> None:
    """把目前加入狀態寫入使用者瀏覽器；重新整理時用來清除舊房間成員。"""
    session_id = str(st.session_state.get("battle_session_id", ""))
    room = normalize_room_code(room_code)
    player = normalize_player_name(player_name)
    payload = {"room": room, "player": player, "session_id": session_id}
    components.html(
        f"""
        <script>
        (function() {{
            const parentWindow = window.parent;
            const membershipKey = {json.dumps(BATTLE_MEMBERSHIP_KEY)};
            try {{
                if ({json.dumps(bool(joined and room and player))}) {{
                    parentWindow.localStorage.setItem(membershipKey, {json.dumps(json.dumps(payload, ensure_ascii=False), ensure_ascii=False)});
                }} else {{
                    parentWindow.localStorage.removeItem(membershipKey);
                }}
            }} catch (e) {{
                console.log("BARREPLAY membership storage failed:", e);
            }}
        }})();
        </script>
        """,
        height=0,
    )


init_session_state()
handle_refresh_cleanup_from_query()
install_refresh_cleanup_probe()
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
        side = trade["side"]

        if side == "買入":
            markers.append({"time": timestamp_seconds(row["Time"]), "position": "belowBar", "color": "#ff5252", "shape": "arrowUp", "text": "買"})
        elif side == "賣出":
            markers.append({"time": timestamp_seconds(row["Time"]), "position": "aboveBar", "color": "#00e676", "shape": "arrowDown", "text": "賣"})
        elif side == "放空":
            markers.append({"time": timestamp_seconds(row["Time"]), "position": "aboveBar", "color": "#42a5f5", "shape": "arrowDown", "text": "空"})
        elif side == "回補":
            markers.append({"time": timestamp_seconds(row["Time"]), "position": "belowBar", "color": "#ab47bc", "shape": "arrowUp", "text": "補"})
        elif side == "全部平倉":
            marker_position = "belowBar" if int(trade.get("position_after", 0)) <= 0 else "aboveBar"
            markers.append({"time": timestamp_seconds(row["Time"]), "position": marker_position, "color": "#ffffff", "shape": "circle", "text": "平"})
    return markers

# =========================================================
# 5. Account / Challenge / Trading
# =========================================================
def reset_account(initial_cash: float) -> None:
    st.session_state.cash = float(initial_cash)  # 可用現金；融券賣出價款與保證金不放在這裡，避免無限放空。
    st.session_state.shares = 0  # 正數 = 多單股數；負數 = 空單股數
    st.session_state.avg_cost = 0.0  # 多單平均成本 / 空單平均放空價
    st.session_state.realized_pnl = 0.0
    st.session_state.short_sale_proceeds = 0.0  # 融券賣出價款，凍結擔保
    st.session_state.short_margin = 0.0  # 融券保證金，模擬 90%
    st.session_state.trade_log = []


def setup_challenge(
    df: pd.DataFrame,
    challenge_bars: int,
    initial_cash: float,
    random_start: bool,
    deterministic_seed: str | None = None,
) -> None:
    min_start = max(120, min(300, len(df) // 5))

    if len(df) < challenge_bars + min_start + 5:
        start_idx = min(max(60, len(df) // 3), len(df) - 2)
        end_idx = len(df) - 1
    else:
        max_start = len(df) - challenge_bars - 1
        if deterministic_seed:
            rng = random.Random(stable_hash_int(deterministic_seed))
            start_idx = rng.randint(min_start, max_start)
        else:
            start_idx = random.randint(min_start, max_start) if random_start else min_start
        end_idx = min(start_idx + challenge_bars, len(df) - 1)

    st.session_state.challenge_start_idx = int(start_idx)
    st.session_state.challenge_end_idx = int(end_idx)
    st.session_state.current_idx = int(start_idx)
    st.session_state.show_answer = False
    reset_account(initial_cash)


def get_short_shares() -> int:
    """目前空單股數。"""
    return abs(int(st.session_state.shares)) if int(st.session_state.shares) < 0 else 0


def get_short_sale_proceeds() -> float:
    return float(st.session_state.get("short_sale_proceeds", 0.0))


def get_short_margin() -> float:
    return float(st.session_state.get("short_margin", 0.0))


def get_short_collateral() -> float:
    """融券擔保品：賣出價款 + 融券保證金。"""
    return get_short_sale_proceeds() + get_short_margin()


def calc_unrealized_pnl(price: float) -> float:
    """多單與空單共用的未實現損益公式。shares 正數為多，負數為空。"""
    shares = int(st.session_state.shares)
    avg_cost = float(st.session_state.avg_cost)
    if shares == 0:
        return 0.0
    return (float(price) - avg_cost) * shares


def calc_short_market_value(price: float) -> float:
    return get_short_shares() * float(price)


def calc_short_maintenance_rate(price: float) -> float | None:
    """
    融券維持率簡化公式：
    (融券賣出價款 + 融券保證金) / 融券標的證券市值。
    回傳百分比，例如 190.0 代表 190%。
    """
    market_value = calc_short_market_value(price)
    if market_value <= 0:
        return None
    return get_short_collateral() / market_value * 100.0


def calc_total_equity(price: float) -> float:
    """
    帳戶總資產。
    多單：可用現金 + 股票市值。
    空單：可用現金 + 凍結融券保證金 + 凍結賣出價款 - 目前回補市值。
    """
    shares = int(st.session_state.shares)
    if shares >= 0:
        return float(st.session_state.cash) + shares * float(price)
    return (
        float(st.session_state.cash)
        + get_short_margin()
        + get_short_sale_proceeds()
        - calc_short_market_value(price)
    )


def calc_max_new_short_lots(price: float) -> int:
    """依 90% 融券保證金，計算目前可用現金最多還能新增放空幾張。"""
    price = float(price)
    if price <= 0:
        return 0
    required_per_lot = price * 1000 * TW_SHORT_MARGIN_RATE
    return max(0, int(float(st.session_state.cash) // required_per_lot))


def get_position_label() -> str:
    shares = int(st.session_state.shares)
    if shares > 0:
        return f"多單 {shares:,} 股"
    if shares < 0:
        return f"空單 {abs(shares):,} 股"
    return "空手 0 股"


def record_trade(row: pd.Series, side: str, shares: int, price: float, reason: str) -> None:
    total_equity = calc_total_equity(price)
    unrealized_pnl = calc_unrealized_pnl(price)
    maintenance_rate = calc_short_maintenance_rate(price)

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
            "short_sale_proceeds_after": round(get_short_sale_proceeds(), 2),
            "short_margin_after": round(get_short_margin(), 2),
            "short_maintenance_rate": round(float(maintenance_rate), 2) if maintenance_rate is not None else None,
            "realized_pnl": round(float(st.session_state.realized_pnl), 2),
            "unrealized_pnl": round(float(unrealized_pnl), 2),
            "total_equity": round(float(total_equity), 2),
            "reason": reason,
        }
    )


def buy_shares(row: pd.Series, lot_count: int, reason: str) -> tuple[bool, str]:
    """現股買入 / 做多。若目前有空單，請先用回補。"""
    price = float(row["Close"])
    shares_to_buy = int(lot_count) * 1000
    cost = shares_to_buy * price

    if st.session_state.shares < 0:
        return False, "目前有空單，請先按『回補』或『全部平倉』，再建立多單。"

    if st.session_state.cash < cost:
        return False, "現金不足，無法買入。"

    old_position_cost = st.session_state.avg_cost * st.session_state.shares
    st.session_state.shares += shares_to_buy
    st.session_state.avg_cost = (old_position_cost + cost) / st.session_state.shares
    st.session_state.cash -= cost
    record_trade(row, "買入", shares_to_buy, price, reason)
    return True, f"成功買入 {lot_count} 張，成交價 {price:.2f}"


def sell_shares(row: pd.Series, lot_count: int, reason: str) -> tuple[bool, str]:
    """賣出多單。"""
    price = float(row["Close"])
    shares_to_sell = int(lot_count) * 1000

    if st.session_state.shares <= 0:
        return False, "目前沒有多單庫存。若要做空請按『放空』。"

    if st.session_state.shares < shares_to_sell:
        return False, "多單庫存不足，無法賣出。"

    revenue = shares_to_sell * price
    pnl = (price - st.session_state.avg_cost) * shares_to_sell
    st.session_state.cash += revenue
    st.session_state.shares -= shares_to_sell
    st.session_state.realized_pnl += pnl

    if st.session_state.shares == 0:
        st.session_state.avg_cost = 0.0

    record_trade(row, "賣出", shares_to_sell, price, reason)
    return True, f"成功賣出 {lot_count} 張，成交價 {price:.2f}，本次損益 {pnl:.1f}"


def short_shares(row: pd.Series, lot_count: int, reason: str) -> tuple[bool, str]:
    """
    台股融券放空模擬：
    1. 不能與多單同時存在。
    2. 放空時賣出價款凍結，不加入可用現金。
    3. 需另外提出 90% 融券保證金，從可用現金扣除。
    4. 若融券維持率已低於 130%，禁止新增放空，只能回補或平倉。
    """
    price = float(row["Close"])
    shares_to_short = int(lot_count) * 1000
    proceeds = shares_to_short * price
    required_margin = proceeds * TW_SHORT_MARGIN_RATE

    if st.session_state.shares > 0:
        return False, "目前有多單，請先賣出或全部平倉後再放空。"

    current_maintenance = calc_short_maintenance_rate(price)
    if current_maintenance is not None and current_maintenance < TW_MIN_MAINTENANCE_RATE * 100:
        return False, f"融券維持率 {current_maintenance:.2f}% 低於 130%，依規則只能回補或平倉，不能再加空。"

    if st.session_state.cash < required_margin:
        max_lots = calc_max_new_short_lots(price)
        return False, (
            f"融券保證金不足：放空 {lot_count} 張需要 {required_margin:,.0f} 元 "
            f"（成交價款 90%），目前可用現金 {st.session_state.cash:,.0f} 元；"
            f"目前最多只能再放空 {max_lots} 張。"
        )

    current_short = get_short_shares()
    old_short_value = float(st.session_state.avg_cost) * current_short
    new_short_value = old_short_value + proceeds
    new_short = current_short + shares_to_short

    st.session_state.shares = -new_short
    st.session_state.avg_cost = new_short_value / new_short
    st.session_state.cash -= required_margin
    st.session_state.short_sale_proceeds = get_short_sale_proceeds() + proceeds
    st.session_state.short_margin = get_short_margin() + required_margin

    record_trade(row, "放空", shares_to_short, price, reason)
    maintenance_rate = calc_short_maintenance_rate(price)
    return True, (
        f"成功放空 {lot_count} 張，成交價 {price:.2f}；"
        f"凍結賣出價款 {proceeds:,.0f} 元，扣除融券保證金 {required_margin:,.0f} 元，"
        f"融券維持率 {maintenance_rate:.2f}%。"
    )


def cover_shares(row: pd.Series, lot_count: int, reason: str) -> tuple[bool, str]:
    """回補空單：按回補比例釋放凍結的融券賣出價款與保證金。"""
    price = float(row["Close"])
    shares_to_cover = int(lot_count) * 1000
    current_short = get_short_shares()

    if st.session_state.shares >= 0:
        return False, "目前沒有空單可回補。"

    if current_short < shares_to_cover:
        return False, "空單股數不足，無法回補這麼多。"

    cover_cost = shares_to_cover * price
    pnl = (st.session_state.avg_cost - price) * shares_to_cover
    release_ratio = shares_to_cover / current_short
    release_proceeds = get_short_sale_proceeds() * release_ratio
    release_margin = get_short_margin() * release_ratio

    st.session_state.cash += release_proceeds + release_margin - cover_cost
    st.session_state.short_sale_proceeds = max(0.0, get_short_sale_proceeds() - release_proceeds)
    st.session_state.short_margin = max(0.0, get_short_margin() - release_margin)
    st.session_state.shares += shares_to_cover
    st.session_state.realized_pnl += pnl

    if st.session_state.shares == 0:
        st.session_state.avg_cost = 0.0
        st.session_state.short_sale_proceeds = 0.0
        st.session_state.short_margin = 0.0

    record_trade(row, "回補", shares_to_cover, price, reason)
    return True, f"成功回補 {lot_count} 張，成交價 {price:.2f}，本次損益 {pnl:.1f}"


def close_all(row: pd.Series, reason: str) -> tuple[bool, str]:
    """全部平倉：多單全部賣出；空單全部回補並釋放所有融券擔保。"""
    price = float(row["Close"])
    shares = int(st.session_state.shares)

    if shares == 0:
        return False, "目前沒有持倉。"

    if shares > 0:
        shares_to_close = shares
        revenue = shares_to_close * price
        pnl = (price - st.session_state.avg_cost) * shares_to_close
        st.session_state.cash += revenue
        closed_side = "全部平倉"
        closed_shares = shares_to_close
    else:
        shares_to_close = abs(shares)
        cover_cost = shares_to_close * price
        pnl = (st.session_state.avg_cost - price) * shares_to_close
        st.session_state.cash += get_short_sale_proceeds() + get_short_margin() - cover_cost
        st.session_state.short_sale_proceeds = 0.0
        st.session_state.short_margin = 0.0
        closed_side = "全部平倉"
        closed_shares = shares_to_close

    st.session_state.shares = 0
    st.session_state.avg_cost = 0.0
    st.session_state.realized_pnl += pnl
    record_trade(row, closed_side, closed_shares, price, reason)
    return True, f"成功全部平倉，成交價 {price:.2f}，本次損益 {pnl:.1f}"


# 舊函式名稱保留相容性。
def sell_all(row: pd.Series, reason: str) -> tuple[bool, str]:
    return close_all(row, reason)

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
#toolbar{height:76px;display:flex;align-items:center;align-content:center;gap:5px;padding:5px 8px;box-sizing:border-box;background:#151a23;border-bottom:1px solid rgba(255,255,255,0.08);user-select:none;overflow-x:visible;white-space:normal;flex-wrap:wrap;}
.tool-btn{height:30px;padding:0 8px;background:#202736;color:#d1d4dc;border:1px solid #343b4a;border-radius:6px;cursor:pointer;font-size:13px;white-space:nowrap;flex:0 0 auto;}
.tool-btn:hover{background:#2d3547;}.tool-btn.active{background:#2962ff;border-color:#2962ff;color:white;}
.tool-btn.disabled{opacity:.38;cursor:not-allowed;background:#1b202b;border-color:#2a3040;color:#6f7786;}
.tool-btn.disabled:hover{background:#1b202b;}
.toolbar-sep{height:24px;width:1px;background:rgba(255,255,255,0.18);margin:0 4px;flex:0 0 auto;}
.action-btn{background:#263047;border-color:#44506a;}
.trade-btn{background:#2b263a;border-color:#5c4a82;}
#status{margin-left:8px;font-size:13px;color:#9aa4b2;white-space:nowrap;flex:0 0 auto;}
#chartBox{position:relative;width:100%;height:__MAIN_CHART_HEIGHT__px;}#mainChart{position:absolute;inset:0;}#drawCanvas{position:absolute;inset:0;z-index:10;pointer-events:none;}#battleOverlay{position:absolute;left:12px;top:12px;z-index:25;display:__OVERLAY_DISPLAY__;min-width:250px;max-width:360px;background:rgba(10,14,22,.78);backdrop-filter:blur(6px);border:1px solid rgba(255,255,255,.16);border-radius:10px;padding:10px 12px;color:#f1f5f9;font-size:13px;line-height:1.55;box-shadow:0 10px 28px rgba(0,0,0,.35);pointer-events:none;}#battleOverlay .big{font-size:20px;font-weight:800;}#battleOverlay .good{color:#ff6b6b;}#battleOverlay .bad{color:#26c6da;}
#macdBox{position:relative;width:100%;height:__MACD_CHART_HEIGHT__px;display:__MACD_DISPLAY__;border-top:1px solid rgba(255,255,255,0.08);}#macdChart{position:absolute;inset:0;}
</style>
</head>
<body>
<div id="wrap">
<div id="toolbar">
<button class="tool-btn active" data-tool="cursor">游標</button><span class="toolbar-sep"></span><button class="tool-btn action-btn back-btn" data-action="-10" title="對戰模式禁止回看">⏮ -10</button><button class="tool-btn action-btn back-btn" data-action="上一根" title="對戰模式禁止回看">⬅ 上一根</button><button class="tool-btn action-btn" data-action="下一根">➡ 下一根</button><button class="tool-btn action-btn" data-action="+10">⏭ +10</button><button class="tool-btn action-btn" data-action="下一關">🎲 下一關</button><span class="toolbar-sep"></span><button class="tool-btn trade-btn" data-action="買入做多">買</button><button class="tool-btn trade-btn" data-action="賣出多單">賣</button><button class="tool-btn trade-btn" data-action="放空">空</button><button class="tool-btn trade-btn" data-action="回補空單">補</button><button class="tool-btn trade-btn" data-action="全部平倉">平</button><span class="toolbar-sep"></span><button class="tool-btn" data-tool="trend">趨勢線</button><button class="tool-btn" data-tool="hline">水平線</button><button class="tool-btn" data-tool="vline">垂直線</button><button class="tool-btn" data-tool="rect">矩形</button><button class="tool-btn" data-tool="fib">斐波</button><button class="tool-btn" data-tool="text">文字</button><button class="tool-btn" data-tool="delete">刪除</button><button class="tool-btn" id="clearAll">全清</button><button class="tool-btn" id="exportDrawings">匯出</button><button class="tool-btn" id="importDrawings">匯入</button><span id="status">模式：游標</span>
</div>
<div id="chartBox"><div id="mainChart"></div><div id="battleOverlay">__OVERLAY_HTML__</div><canvas id="drawCanvas"></canvas></div><div id="macdBox"><div id="macdChart"></div></div>
</div>
<script>
const candleData=__CANDLES__;const volumeData=__VOLUMES__;const indicatorPayload=__INDICATORS__;const markers=__MARKERS__;const macdPayload=__MACD__;const showMacd=__SHOW_MACD__;const drawingsKey=__DRAWINGS_KEY__;const viewKey=__VIEW_KEY__;const allowBackActions=__ALLOW_BACK_ACTIONS__;const focusMode=__FOCUS_MODE__;
const chartBox=document.getElementById("chartBox");const canvas=document.getElementById("drawCanvas");const ctx=canvas.getContext("2d");const statusEl=document.getElementById("status");
const timeLabelMap={};candleData.forEach(d=>{timeLabelMap[d.time]=d.label;});
if(!allowBackActions){
    document.querySelectorAll('.back-btn').forEach(btn=>{
        btn.classList.add('disabled');
        btn.setAttribute('aria-disabled','true');
        btn.title='對戰模式禁止回看，只能往前作答';
    });
}
function isBackAction(keyword){return keyword==='上一根'||keyword==='-10'||String(keyword).includes('上一根');}
let tool="cursor";let firstPoint=null;let drawings=[];try{drawings=JSON.parse(localStorage.getItem(drawingsKey)||"[]");if(!Array.isArray(drawings))drawings=[];}catch(e){drawings=[];}
const chart=LightweightCharts.createChart(document.getElementById("mainChart"),{layout:{background:{color:"#0f131a"},textColor:"#d1d4dc"},localization:{timeFormatter:(time)=>timeLabelMap[time]||String(time)},grid:{vertLines:{color:"rgba(255,255,255,0.08)"},horzLines:{color:"rgba(255,255,255,0.08)"}},rightPriceScale:{borderColor:"rgba(255,255,255,0.15)"},timeScale:{borderColor:"rgba(255,255,255,0.15)",timeVisible:true,secondsVisible:false,tickMarkFormatter:(time)=>timeLabelMap[time]||""},crosshair:{mode:LightweightCharts.CrosshairMode.Normal},handleScale:true,handleScroll:true});
const candleSeries=chart.addCandlestickSeries({upColor:"#ef5350",downColor:"#26a69a",borderUpColor:"#ef5350",borderDownColor:"#26a69a",wickUpColor:"#ef5350",wickDownColor:"#26a69a"});candleSeries.setData(candleData);if(markers.length>0)candleSeries.setMarkers(markers);
if(volumeData.length>0){const volumeSeries=chart.addHistogramSeries({priceFormat:{type:"volume"},priceScaleId:""});volumeSeries.priceScale().applyOptions({scaleMargins:{top:0.80,bottom:0}});volumeSeries.setData(volumeData);}
indicatorPayload.forEach(item=>{const s=chart.addLineSeries({color:item.color,lineWidth:item.lineWidth,priceLineVisible:false,lastValueVisible:false,title:""});s.setData(item.data);});
let restoringView=true;
function getSavedViewRange(){try{const raw=localStorage.getItem(viewKey);if(!raw)return null;const range=JSON.parse(raw);if(range&&typeof range.from==="number"&&typeof range.to==="number")return range;}catch(e){}return null;}
function applySavedViewRange(){const range=getSavedViewRange();if(range){chart.timeScale().setVisibleLogicalRange(range);if(showMacd&&macdChart)macdChart.timeScale().setVisibleLogicalRange(range);}else{chart.timeScale().fitContent();if(showMacd&&macdChart)macdChart.timeScale().fitContent();}setTimeout(()=>{restoringView=false;},350);}
function saveViewRange(){if(restoringView)return;const range=chart.timeScale().getVisibleLogicalRange();if(range&&typeof range.from==="number"&&typeof range.to==="number"){try{localStorage.setItem(viewKey,JSON.stringify(range));}catch(e){}}}
let macdChart=null;let syncingRange=false;if(showMacd){macdChart=LightweightCharts.createChart(document.getElementById("macdChart"),{layout:{background:{color:"#0f131a"},textColor:"#d1d4dc"},localization:{timeFormatter:(time)=>timeLabelMap[time]||String(time)},grid:{vertLines:{color:"rgba(255,255,255,0.08)"},horzLines:{color:"rgba(255,255,255,0.08)"}},rightPriceScale:{borderColor:"rgba(255,255,255,0.15)"},timeScale:{borderColor:"rgba(255,255,255,0.15)",timeVisible:true,secondsVisible:false,tickMarkFormatter:(time)=>timeLabelMap[time]||""},crosshair:{mode:LightweightCharts.CrosshairMode.Normal},handleScale:true,handleScroll:true});
const histSeries=macdChart.addHistogramSeries({priceFormat:{type:"price",precision:2,minMove:0.01},priceLineVisible:false,lastValueVisible:false,title:""});histSeries.setData(macdPayload.hist);const zeroSeries=macdChart.addLineSeries({color:"rgba(255,255,255,0.35)",lineWidth:1,priceLineVisible:false,lastValueVisible:false,title:""});zeroSeries.setData(macdPayload.zero);const difSeries=macdChart.addLineSeries({color:"#ffca28",lineWidth:2,title:"",priceLineVisible:false,lastValueVisible:false});difSeries.setData(macdPayload.dif);const deaSeries=macdChart.addLineSeries({color:"#42a5f5",lineWidth:2,title:"",priceLineVisible:false,lastValueVisible:false});deaSeries.setData(macdPayload.dea);chart.timeScale().subscribeVisibleLogicalRangeChange(range=>{if(!range||syncingRange||!macdChart)return;syncingRange=true;macdChart.timeScale().setVisibleLogicalRange(range);syncingRange=false;});macdChart.timeScale().subscribeVisibleLogicalRangeChange(range=>{if(!range||syncingRange)return;syncingRange=true;chart.timeScale().setVisibleLogicalRange(range);syncingRange=false;});}
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
function clickParentButtonByText(keyword){if(!allowBackActions&&isBackAction(keyword)){statusEl.innerText="對戰模式禁止回看，只能往前作答";return;}try{saveViewRange();}catch(e){}try{const buttons=Array.from(window.parent.document.querySelectorAll("button"));const target=buttons.find(btn=>btn.innerText&&btn.innerText.includes(keyword));if(target&&!target.disabled){target.click();return;}}catch(e){console.log("Direct parent click failed:",e);}try{window.parent.postMessage({type:"tv_replay_action",keyword:keyword},"*");}catch(e){console.log("postMessage failed:",e);}}
document.querySelectorAll(".action-btn[data-action], .trade-btn[data-action]").forEach(btn=>btn.addEventListener("click",()=>clickParentButtonByText(btn.dataset.action)));
document.addEventListener("keydown",e=>{const tag=(e.target.tagName||"").toLowerCase();if(tag==="input"||tag==="textarea"||e.target.isContentEditable)return;if(e.key==="ArrowLeft"){e.preventDefault();if(allowBackActions)clickParentButtonByText("上一根");else statusEl.innerText="對戰模式禁止回看，只能往前作答";}if(e.key==="ArrowRight"){e.preventDefault();clickParentButtonByText("下一根");}},true);
window.addEventListener("keydown",e=>{if(e.key==="ArrowLeft"){e.preventDefault();if(allowBackActions)clickParentButtonByText("上一根");else statusEl.innerText="對戰模式禁止回看，只能往前作答";}if(e.key==="ArrowRight"){e.preventDefault();clickParentButtonByText("下一根");}},true);
chart.timeScale().subscribeVisibleLogicalRangeChange(()=>{drawAll();saveViewRange();});chart.subscribeCrosshairMove(()=>drawAll());setInterval(drawAll,400);setTool("cursor");setTimeout(applySavedViewRange,0);setTimeout(applySavedViewRange,80);setTimeout(applySavedViewRange,250);if(focusMode){setTimeout(()=>{try{window.frameElement.scrollIntoView({behavior:"smooth",block:"start"});}catch(e){}},250);setTimeout(()=>{try{document.getElementById("wrap").requestFullscreen();}catch(e){}},600);}drawAll();
</script>
</body>
</html>
'''


def render_tv_chart(visible_df, indicator_df, selected_indicators, show_volume, show_macd, trade_log, df_all, blind_mode, ticker, interval, challenge_start_idx, challenge_id, height, allow_back_actions=True, overlay_html="", focus_mode=False) -> None:
    candles = make_candle_data(visible_df, blind_mode=blind_mode, challenge_start_idx=challenge_start_idx)
    volumes = make_volume_data(visible_df) if show_volume else []
    indicators = build_indicator_payload(indicator_df, selected_indicators)
    markers = build_trade_markers(df_all, trade_log)
    macd_payload = make_macd_payload(indicator_df) if show_macd else {"dif": [], "dea": [], "hist": [], "zero": []}

    indicator_signature = "-".join(selected_indicators) if selected_indicators else "none"
    drawings_key = f"tv_drawings_{ticker}_{interval}_{challenge_id}"
    view_key = f"tv_view_{ticker}_{interval}_{challenge_id}"
    chart_signature = f"{ticker}_{interval}_{challenge_id}_{indicator_signature}_{show_macd}_{show_volume}_{len(visible_df)}_{float(visible_df.iloc[-1]['Close']) if len(visible_df) else 0}"

    inner_height = max(300, height - 76)
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
        .replace("__DRAWINGS_KEY__", json.dumps(drawings_key, ensure_ascii=False))
        .replace("__VIEW_KEY__", json.dumps(view_key, ensure_ascii=False))
        .replace("__ALLOW_BACK_ACTIONS__", json.dumps(bool(allow_back_actions)))
        .replace("__FOCUS_MODE__", json.dumps(bool(focus_mode)))
        .replace("__OVERLAY_DISPLAY__", "block" if overlay_html else "none")
        .replace("__OVERLAY_HTML__", str(overlay_html)))

    html_code = f"<!-- BARREPLAY_V22_CHART_SIGNATURE:{chart_signature} -->\n" + html_code
    components.html(html_code, height=height + 10, scrolling=False)

# =========================================================
# 7. Sidebar Settings
# =========================================================
if st.session_state.get("pending_stock_code") is not None:
    st.session_state.stock_code = st.session_state.pending_stock_code
    st.session_state.pending_stock_code = None

with st.sidebar:
    st.header("⚙️ 闖關設定")
    st.caption("目前版本：V22-join-no-refresh-kick（建立/加入不會被刷新踢出，仍支援房主踢人）")

    mode = st.radio("模式", ["闖關模式", "自選練習", "對戰模式"], key="setting_mode")

    stock_pool_text = st.text_area("隨機股票池", height=90, key="setting_stock_pool_text")
    stock_pool = parse_stock_codes(stock_pool_text)

    if mode == "對戰模式":
        st.markdown("---")
        st.subheader("🏁 對戰房間")
        st.text_input("房間號碼", key="battle_room_code", help="先輸入房號，再按建立或加入。同一房間會使用同一組題目。")
        st.text_input("玩家名稱（必填）", key="battle_player_name", placeholder="請輸入你的名字，例如：Yao")

        requested_room_code = normalize_room_code(st.session_state.get("battle_room_code", "ROOM001"))
        requested_player_name = normalize_player_name(st.session_state.get("battle_player_name", ""))
        valid_player_name = is_valid_player_name(requested_player_name)

        if not valid_player_name:
            st.error("對戰模式必須先取名字，不能空白，也不要使用 Player / Guest / 匿名。")

        room_meta_now = get_battle_room_meta(requested_room_code)
        room_joined_now = is_player_joined_room(requested_room_code, requested_player_name)
        room_waiting_now = is_battle_room_waiting(room_meta_now)
        room_started_now = is_battle_room_started(room_meta_now)
        room_owner_now = bool(room_meta_now) and room_meta_now.get("created_by", "") == requested_player_name

        if "battle_room_question_count" not in st.session_state:
            st.session_state.battle_room_question_count = get_room_question_count_from_data(room_meta_now)
        if "battle_time_limit_minutes" not in st.session_state:
            st.session_state.battle_time_limit_minutes = get_room_time_limit_from_data(room_meta_now)

        if room_owner_now and room_waiting_now:
            st.markdown("##### 房主賽前設定")
            st.number_input(
                "本房關卡數",
                min_value=BATTLE_MIN_QUESTION_COUNT,
                max_value=BATTLE_MAX_QUESTION_COUNT,
                step=1,
                key="battle_room_question_count",
                help="只有房主可在遊戲開始前修改。",
            )
            st.number_input(
                "每關限時（分鐘）",
                min_value=BATTLE_MIN_TIME_LIMIT_MINUTES,
                max_value=BATTLE_MAX_TIME_LIMIT_MINUTES,
                step=1,
                key="battle_time_limit_minutes",
                help="時間到後不能再按下一根或交易，只能提交當下成績。",
            )
        else:
            q_count_hint = get_room_question_count_from_data(room_meta_now)
            t_limit_hint = get_room_time_limit_from_data(room_meta_now)
            st.caption(f"房間設定：{q_count_hint} 關，每關 {t_limit_hint} 分鐘。")

        room_col1, room_col2 = st.columns(2)
        with room_col1:
            if st.button("➕ 建立房間", use_container_width=True, disabled=not valid_player_name):
                ok, msg = create_battle_room(
                    requested_room_code,
                    requested_player_name,
                    st.session_state.get("setting_interval_label", "日線"),
                    st.session_state.get("setting_challenge_bars", 120),
                    int(st.session_state.get("battle_room_question_count", BATTLE_DEFAULT_QUESTION_COUNT)),
                    int(st.session_state.get("battle_time_limit_minutes", BATTLE_DEFAULT_TIME_LIMIT_MINUTES)),
                    float(st.session_state.get("setting_initial_cash", DEFAULT_SETTINGS["initial_cash"])),
                    float(st.session_state.get("setting_target_return_pct", DEFAULT_SETTINGS["target_return_pct"])),
                )
                if ok:
                    st.session_state.battle_room_joined = True
                    st.session_state.battle_joined_room_code = requested_room_code
                    st.session_state.battle_room_owner = True
                    st.session_state.battle_question_no = 1
                    st.session_state.pending_new_challenge = True
                st.session_state.battle_room_notice = msg
                # 不在建立房間後立刻 st.rerun()；讓本輪先把 membership 寫入瀏覽器，
                # 避免下一輪被 refresh-cleanup 誤判為重新整理而踢出。

        with room_col2:
            if st.button("🚪 加入房間", use_container_width=True, disabled=not valid_player_name):
                ok, msg = join_battle_room(
                    requested_room_code,
                    requested_player_name,
                    st.session_state.get("setting_interval_label", "日線"),
                    st.session_state.get("setting_challenge_bars", 120),
                )
                if ok:
                    st.session_state.battle_room_joined = True
                    st.session_state.battle_joined_room_code = requested_room_code
                    st.session_state.battle_room_owner = False
                    st.session_state.battle_question_no = 1
                    st.session_state.pending_new_challenge = True
                st.session_state.battle_room_notice = msg
                # 不在加入房間後立刻 st.rerun()；讓本輪先把 membership 寫入瀏覽器，
                # 避免下一輪被 refresh-cleanup 誤判為重新整理而踢出。

        # 重新讀取，避免剛建立/加入後仍使用舊的 room_meta。
        room_meta_now = get_battle_room_meta(requested_room_code)
        room_joined_now = is_player_joined_room(requested_room_code, requested_player_name)
        room_waiting_now = is_battle_room_waiting(room_meta_now)
        room_started_now = is_battle_room_started(room_meta_now)
        room_owner_now = bool(room_meta_now) and room_meta_now.get("created_by", "") == requested_player_name

        if is_player_kicked(requested_room_code, requested_player_name):
            st.session_state.battle_room_joined = False
            st.session_state.battle_joined_room_code = ""
            st.session_state.battle_room_owner = False
            st.session_state.battle_kicked_notice = f"你已被房主移出房間 {requested_room_code}。"
            room_joined_now = False
            room_owner_now = False
            install_battle_membership_storage(requested_room_code, requested_player_name, False)
            st.warning(st.session_state.battle_kicked_notice)

        if room_joined_now and room_owner_now and room_waiting_now:
            set_col, start_col = st.columns(2)
            with set_col:
                if st.button("💾 儲存房間設定", use_container_width=True):
                    ok, msg = update_battle_room_settings(
                        requested_room_code,
                        requested_player_name,
                        int(st.session_state.get("battle_room_question_count", BATTLE_DEFAULT_QUESTION_COUNT)),
                        int(st.session_state.get("battle_time_limit_minutes", BATTLE_DEFAULT_TIME_LIMIT_MINUTES)),
                        st.session_state.get("setting_interval_label", "日線"),
                        int(st.session_state.get("setting_challenge_bars", 120)),
                        float(st.session_state.get("setting_initial_cash", DEFAULT_SETTINGS["initial_cash"])),
                        float(st.session_state.get("setting_target_return_pct", DEFAULT_SETTINGS["target_return_pct"])),
                    )
                    st.session_state.battle_room_notice = msg
                    st.rerun()
            with start_col:
                if st.button("▶️ 開始對戰", type="primary", use_container_width=True):
                    # 開始前自動保存房主當前設定。
                    update_battle_room_settings(
                        requested_room_code,
                        requested_player_name,
                        int(st.session_state.get("battle_room_question_count", BATTLE_DEFAULT_QUESTION_COUNT)),
                        int(st.session_state.get("battle_time_limit_minutes", BATTLE_DEFAULT_TIME_LIMIT_MINUTES)),
                        st.session_state.get("setting_interval_label", "日線"),
                        int(st.session_state.get("setting_challenge_bars", 120)),
                        float(st.session_state.get("setting_initial_cash", DEFAULT_SETTINGS["initial_cash"])),
                        float(st.session_state.get("setting_target_return_pct", DEFAULT_SETTINGS["target_return_pct"])),
                    )
                    ok, msg = start_battle_room(requested_room_code, requested_player_name)
                    if ok:
                        st.session_state.battle_question_no = 1
                        reset_question_timer(requested_room_code, requested_player_name, 1)
                        st.session_state.pending_new_challenge = True
                    st.session_state.battle_room_notice = msg
                    st.rerun()
        elif room_joined_now and not room_started_now:
            st.info("已加入房間，等待房主按『開始對戰』。")
        elif room_started_now:
            st.success("此房間已開始。")

        if st.session_state.get("battle_room_notice"):
            if str(st.session_state.battle_room_notice).startswith("已") or "開始" in str(st.session_state.battle_room_notice) or "儲存" in str(st.session_state.battle_room_notice):
                st.success(st.session_state.battle_room_notice)
            else:
                st.warning(st.session_state.battle_room_notice)

        if room_joined_now:
            st.success(f"目前已在房間：{requested_room_code}")
            install_battle_membership_storage(requested_room_code, requested_player_name, True)
        else:
            st.warning("尚未加入這個房間。請先建立房間或加入房間。")
            install_battle_membership_storage(requested_room_code, requested_player_name, False)

        if room_joined_now and room_owner_now:
            latest_room_for_kick = get_battle_room_meta(requested_room_code)
            latest_players = latest_room_for_kick.get("players", {}) if isinstance(latest_room_for_kick, dict) else {}
            kick_candidates = [p for p in latest_players.keys() if p != requested_player_name]
            if kick_candidates:
                st.markdown("##### 房主管理")
                kick_target = st.selectbox("選擇要踢出的玩家", kick_candidates, key="battle_kick_target")
                if st.button("🚫 踢出玩家", use_container_width=True):
                    ok, msg = kick_battle_player(requested_room_code, requested_player_name, kick_target)
                    st.session_state.battle_room_notice = msg
                    st.rerun()

        if st.button("🔄 立即同步房間", use_container_width=True):
            if room_joined_now:
                register_battle_presence(requested_room_code, requested_player_name, st.session_state.get("setting_interval_label", "日線"), st.session_state.get("setting_challenge_bars", 120))
            st.rerun()

        st.caption("對戰模式由房主開始；開始後玩家從第 1 關作答，禁止回看並有時間限制。")
    else:
        if st.button("🎲 隨機開新關卡", use_container_width=True):
            if stock_pool:
                st.session_state.pending_stock_code = random.choice(stock_pool)
                st.session_state.pending_new_challenge = True
                st.rerun()

    raw_code_input = st.text_input("目前股票代號", key="stock_code", disabled=(mode == "對戰模式"))

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

battle_room_code = normalize_room_code(st.session_state.get("battle_room_code", "ROOM001"))
battle_player_name = normalize_player_name(st.session_state.get("battle_player_name", ""))
battle_room_meta = get_battle_room_meta(battle_room_code)
battle_room_joined = is_player_joined_room(battle_room_code, battle_player_name)
if mode == "對戰模式" and is_player_kicked(battle_room_code, battle_player_name):
    st.session_state.battle_room_joined = False
    st.session_state.battle_joined_room_code = ""
    st.session_state.battle_room_owner = False
    battle_room_joined = False
    st.warning(f"你已被房主移出房間 {battle_room_code}。")
    install_battle_membership_storage(battle_room_code, battle_player_name, False)
battle_room_started = is_battle_room_started(battle_room_meta)
battle_question_count = get_room_question_count_from_data(battle_room_meta)
battle_time_limit_minutes = get_room_time_limit_from_data(battle_room_meta)

if mode == "對戰模式":
    if not is_valid_player_name(battle_player_name):
        st.error("請先在左側輸入玩家名稱。對戰模式必須取名字才能建立 / 加入 / 開始。")
        st.stop()

    if not battle_room_joined:
        st.info("請先在左側建立房間或加入房間。加入後會看到房間玩家列表，房主按開始後才會進入第 1 關。")
        players_df = build_battle_room_players_df(battle_room_code)
        if not players_df.empty:
            st.markdown("#### 👥 目前房間玩家")
            st.dataframe(players_df, use_container_width=True, hide_index=True)
        st.stop()

    register_battle_presence(battle_room_code, battle_player_name, interval_label, challenge_bars)
    battle_room_meta = get_battle_room_meta(battle_room_code)
    battle_room_started = is_battle_room_started(battle_room_meta)
    battle_question_count = get_room_question_count_from_data(battle_room_meta)
    battle_time_limit_minutes = get_room_time_limit_from_data(battle_room_meta)
    install_battle_live_sync(True, seconds=1.0 if battle_room_started else 2.0)

    if not battle_room_started:
        install_battle_live_sync(True, seconds=2.0)
        st.markdown("### 🕒 對戰等待室")
        owner_name = battle_room_meta.get("created_by", "") if isinstance(battle_room_meta, dict) else ""
        st.info(f"房間 {battle_room_code} 尚未開始，等待房主 {owner_name or '未知'} 按『開始對戰』。")
        c1, c2, c3 = st.columns(3)
        c1.metric("房間號碼", battle_room_code)
        c2.metric("關卡數", f"{battle_question_count} 關")
        c3.metric("每關限時", f"{battle_time_limit_minutes} 分鐘")
        players_df = build_battle_room_players_df(battle_room_code)
        st.markdown("#### 👥 房間玩家")
        if not players_df.empty:
            st.dataframe(players_df, use_container_width=True, hide_index=True)
        else:
            st.caption("目前還沒有玩家。")
        st.stop()

if mode == "對戰模式":
    # 遊戲開始後，所有玩家強制使用房主開始前儲存的房間設定，避免每個人本機 sidebar 設定不同。
    interval_label = str(battle_room_meta.get("interval_label", interval_label))
    interval = interval_map.get(interval_label, interval)
    period = "5y" if interval == "1d" else "60d"
    challenge_bars = int(battle_room_meta.get("challenge_bars", challenge_bars))
    initial_cash = float(battle_room_meta.get("initial_cash", initial_cash))
    target_return_pct = float(battle_room_meta.get("target_return_pct", target_return_pct))

battle_question_no = int(st.session_state.get("battle_question_no", 1))
battle_question_no = int(np.clip(battle_question_no, 1, battle_question_count))
if mode == "對戰模式" and battle_room_started:
    scheduled_status_pre = get_global_battle_time_status(battle_room_meta, question_no=battle_question_no)
    scheduled_q = int(scheduled_status_pre.get("scheduled_question_no", battle_question_no))
    submitted_scores_pre = get_player_battle_scores(battle_room_code, battle_player_name)
    # 若房間時間已進入下一題，而且本題已提交，就自動跟著全房進入下一題。
    if scheduled_q > battle_question_no and str(battle_question_no) in submitted_scores_pre:
        battle_question_no = scheduled_q
        st.session_state["battle_question_no"] = battle_question_no
        st.session_state.pending_new_challenge = True
    elif scheduled_q < battle_question_no:
        battle_question_no = scheduled_q
        st.session_state["battle_question_no"] = battle_question_no
        st.session_state.pending_new_challenge = True
    else:
        st.session_state["battle_question_no"] = battle_question_no
else:
    st.session_state["battle_question_no"] = battle_question_no
battle_questions = build_battle_questions(battle_room_code, interval_label, challenge_bars, battle_question_count)
battle_question = battle_questions[battle_question_no - 1]

if mode == "對戰模式":
    raw_code = str(battle_question["stock_code"]).strip()
else:
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
battle_seed = str(battle_question["seed"]) if mode == "對戰模式" else None
config_key = f"{actual_loaded_ticker}_{period}_{interval}_{challenge_bars}_{target_return_pct}_{initial_cash}_{mode}_{battle_room_code}_{battle_question_no}_{battle_seed}"

if st.session_state.last_config_key != config_key or st.session_state.pending_new_challenge:
    st.session_state.last_config_key = config_key
    setup_challenge(
        df=df,
        challenge_bars=challenge_bars,
        initial_cash=initial_cash,
        random_start=(mode == "闖關模式"),
        deterministic_seed=battle_seed,
    )
    st.session_state.pending_new_challenge = False

if mode in ["闖關模式", "對戰模式"]:
    min_idx = st.session_state.challenge_start_idx
    max_idx = st.session_state.challenge_end_idx
else:
    min_idx = 5
    max_idx = len(df) - 1

st.session_state.current_idx = int(np.clip(st.session_state.current_idx, min_idx, max_idx))
current_row = df.iloc[st.session_state.current_idx]
current_price = float(current_row["Close"])
unrealized_pnl = calc_unrealized_pnl(current_price)
total_equity = calc_total_equity(current_price)
short_maintenance_rate = calc_short_maintenance_rate(current_price)
max_new_short_lots = calc_max_new_short_lots(current_price)
return_pct = (total_equity - initial_cash) / initial_cash * 100.0
bars_passed = int(st.session_state.current_idx - st.session_state.challenge_start_idx)
bars_total = int(st.session_state.challenge_end_idx - st.session_state.challenge_start_idx)
bars_left = max(0, bars_total - bars_passed)
battle_time_status = None
battle_time_up = False
battle_game_over = False
if mode == "對戰模式":
    battle_time_status = get_global_battle_time_status(battle_room_meta, question_no=battle_question_no)
    battle_time_up = bool(battle_time_status["time_up"])
    battle_game_over = bool(battle_time_status.get("game_over", False))
    if battle_game_over:
        mark_battle_room_ended_if_needed(battle_room_code, battle_room_meta)

stock_name = "" if blind_mode else get_stock_name(actual_loaded_ticker)
if mode == "對戰模式":
    battle_prefix = f"房間 {battle_room_code}｜第 {battle_question_no}/{battle_question_count} 題"
    show_title = f"{battle_prefix}｜隨機盲測標的" if blind_mode else f"{battle_prefix}｜{actual_loaded_ticker} {stock_name}"
else:
    show_title = "隨機盲測標的" if blind_mode else f"{actual_loaded_ticker} {stock_name}"
show_time = f"D+{bars_passed}" if blind_mode else current_row["TimeStr"]

# 對戰模式：時間到時自動提交目前題目的當下資產，確保全房可同步進入下一題或結算。
if mode == "對戰模式":
    submitted_scores_auto = get_player_battle_scores(battle_room_code, battle_player_name)
    if battle_time_up and str(battle_question_no) not in submitted_scores_auto:
        submit_battle_score(
            room_code=battle_room_code,
            player_name=battle_player_name,
            question_no=battle_question_no,
            final_equity=total_equity,
            return_pct=return_pct,
            ticker=actual_loaded_ticker,
            interval_label=interval_label,
            challenge_bars=challenge_bars,
            trade_count=len(st.session_state.trade_log),
        )
        st.session_state.battle_last_submit_message = f"時間到，自動提交第 {battle_question_no} 題：{total_equity:,.0f} 元"
        st.rerun()

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

if mode == "對戰模式":
    install_battle_live_sync(True, seconds=1.0 if not battle_game_over else 5.0)
    install_battle_focus_mode(True)
    chart_height = max(chart_height, 920)
    st.markdown("### 🏆 對戰模式")
    st.warning("對戰模式已鎖定回看：不能按上一根或 -10，只能往下一根推進。")
    players_df = build_battle_room_players_df(battle_room_code)
    online_count = int((players_df["狀態"] == "在線").sum()) if not players_df.empty and "狀態" in players_df.columns else 0
    b1, b2, b3, b4, b5, b6 = st.columns(6)
    b1.metric("房間號碼", battle_room_code)
    b2.metric("玩家", battle_player_name)
    b3.metric("目前題目", f"{battle_question_no} / {battle_question_count}")
    b4.metric("本題期末金額", f"{total_equity:,.0f}")
    b5.metric("房間人數", f"{len(players_df)} 人", delta=f"在線 {online_count}")
    b6.metric("倒數時間", "結束" if battle_game_over else (str(battle_time_status["time_text"]) if battle_time_status else "--"))

    if battle_game_over:
        st.success("🏁 本房所有關卡時間已結束，系統進入最終結算。")
    elif battle_time_up:
        st.error("⏰ 本題時間到！系統會自動提交當下成績，並等待下一題同步開始。")
    else:
        st.info(f"本題限時 {battle_time_limit_minutes} 分鐘，剩餘 {battle_time_status['time_text']}。")

    with st.expander("👥 房間玩家 / 進入狀態", expanded=True):
        if not players_df.empty:
            st.dataframe(players_df, use_container_width=True, hide_index=True)
        else:
            st.caption("目前還沒有玩家加入此房間。")

    submitted_scores_now = get_player_battle_scores(battle_room_code, battle_player_name)
    q_df = pd.DataFrame(
        [
            {
                "題號": item["question_no"],
                "狀態": "已提交" if str(item["question_no"]) in submitted_scores_now else ("目前" if item["question_no"] == battle_question_no else "待作答"),
                "股票代號": "盲測中" if blind_mode else item["stock_code"],
            }
            for item in battle_questions
        ]
    )
    with st.expander(f"查看本房 {battle_question_count} 題清單"):
        st.dataframe(q_df, use_container_width=True, hide_index=True)

    leaderboard_df = build_battle_leaderboard(battle_room_code)
    if not leaderboard_df.empty:
        st.markdown("#### 🏆 房間排行榜")
        st.dataframe(leaderboard_df, use_container_width=True, hide_index=True)
    else:
        st.caption("目前房間還沒有玩家提交成績。完成每題或時間到後按「提交本題成績」即可上榜。")

    winner_info = get_battle_winner_summary(battle_room_code)
    if winner_info.get("has_winner"):
        if winner_info.get("all_finished"):
            st.success("🎉 對戰結束！" + winner_info.get("message", ""))
            st.balloons()
        else:
            st.info("目前戰況：" + winner_info.get("message", ""))

# =========================================================
# 9.5 Indicator Checkboxes
# =========================================================
st.markdown("#### 📊 指標設定")
st.caption("指標面板放在重播控制上方：按「下一根」前會先渲染 checkbox，所以 MA/EMA/VWAP 不會被 Streamlit 清掉。每位使用者設定仍儲存在自己的 session / browser localStorage。")

indicator_cols = st.columns(8)
for idx, indicator in enumerate(INDICATOR_OPTIONS):
    with indicator_cols[idx]:
        st.checkbox(indicator, key=f"setting_indicator_{indicator}")

show_macd = st.checkbox("顯示 MACD", key="setting_show_macd")
selected_indicators = [
    name for name in INDICATOR_OPTIONS
    if st.session_state.get(f"setting_indicator_{name}", False)
]

# 保險備份：即使某次 rerun 有 widget 沒渲染，也可用上一輪指標設定復原。
st.session_state.persisted_selected_indicators = selected_indicators.copy()
st.session_state.persisted_show_macd = bool(show_macd)

settings_now = collect_current_settings()
persist_settings_to_browser(settings_now)

battle_submitted_scores = get_player_battle_scores(battle_room_code, battle_player_name) if mode == "對戰模式" else {}
battle_current_question_submitted = str(battle_question_no) in battle_submitted_scores if mode == "對戰模式" else False
battle_player_completed_all = len(battle_submitted_scores) >= battle_question_count if mode == "對戰模式" else False
allow_back_actions = mode != "對戰模式"
allow_forward_actions = not (mode == "對戰模式" and (battle_time_up or battle_current_question_submitted or battle_player_completed_all or battle_game_over))
trade_actions_disabled = mode == "對戰模式" and (battle_time_up or battle_current_question_submitted or battle_player_completed_all or battle_game_over)

# =========================================================
# 10. Replay Control
# =========================================================
st.markdown("### 🕹️ 重播控制")
replay_col1, replay_col2, replay_col3, replay_col4, replay_col5, replay_col6 = st.columns([1, 1.3, 1.8, 1.8, 1.3, 1])

with replay_col1:
    if st.button("⏮️ -10", use_container_width=True, disabled=not allow_back_actions):
        st.session_state.current_idx = max(min_idx, st.session_state.current_idx - 10)
        st.session_state.show_answer = False
        st.rerun()

with replay_col2:
    if st.button("⬅️ 上一根", use_container_width=True, disabled=not allow_back_actions):
        st.session_state.current_idx = max(min_idx, st.session_state.current_idx - 1)
        st.session_state.show_answer = False
        st.rerun()

with replay_col3:
    if st.button("➡️ 下一根", type="primary", use_container_width=True, disabled=not allow_forward_actions):
        st.session_state.current_idx = min(max_idx, st.session_state.current_idx + 1)
        st.session_state.show_answer = False
        st.rerun()

with replay_col4:
    if st.button("👁️ 對答案 / 顯示終點", use_container_width=True, disabled=(mode == "對戰模式")):
        st.session_state.show_answer = not st.session_state.show_answer
        st.rerun()

with replay_col5:
    if st.button("⏭️ +10", use_container_width=True, disabled=not allow_forward_actions):
        st.session_state.current_idx = min(max_idx, st.session_state.current_idx + 10)
        st.session_state.show_answer = False
        st.rerun()

with replay_col6:
    if st.button("🎲 下一關", use_container_width=True, disabled=(mode == "對戰模式")):
        if mode == "對戰模式":
            next_question_no = min(battle_question_count, int(st.session_state.battle_question_no) + 1)
            st.session_state.battle_question_no = next_question_no
            reset_question_timer(battle_room_code, battle_player_name, next_question_no)
        else:
            if stock_pool:
                st.session_state.pending_stock_code = random.choice(stock_pool)
        st.session_state.pending_new_challenge = True
        st.rerun()

st.caption("快捷鍵：`→` 下一根。對戰模式禁止 `←` 上一根與 `-10` 回看；非對戰模式仍可使用左右鍵。")

# =========================================================
# 11. Account / Trade Panel
# =========================================================
st.markdown("#### 💰 模擬交易帳戶")
a1, a2, a3, a4, a5, a6, a7 = st.columns(7)
a1.metric("可用現金", f"{st.session_state.cash:,.0f}")
a2.metric("目前持倉", get_position_label())
a3.metric("平均成本 / 放空價", f"{st.session_state.avg_cost:.2f}")
a4.metric("未實現損益", f"{unrealized_pnl:,.0f}")
a5.metric("已實現損益", f"{st.session_state.realized_pnl:,.0f}")
a6.metric("融券擔保", f"{get_short_collateral():,.0f}")
a7.metric("融券維持率", "--" if short_maintenance_rate is None else f"{short_maintenance_rate:.2f}%")

if short_maintenance_rate is not None:
    st.caption(
        f"台股融券模擬：放空時賣出價款會凍結，另扣 90% 融券保證金；"
        f"目前最多可再新增放空 {max_new_short_lots} 張。"
    )
    if short_maintenance_rate < TW_MIN_MAINTENANCE_RATE * 100:
        st.error("⚠️ 融券維持率低於 130%，模擬規則限制：不能再加空，只能回補或全部平倉。")
    elif short_maintenance_rate < TW_SAFE_MAINTENANCE_RATE * 100:
        st.warning("⚠️ 融券維持率低於 166%，接近追繳風險區，請注意風險。")

st.markdown("#### 🧾 買入 / 賣出 / 放空 / 回補")
t1, t2, t3, t4, t5, t6, t7 = st.columns([2, 1, 1, 1, 1, 1, 1])
trade_note = t1.text_input("買賣原因", placeholder="例：突破箱頂買入 / 跌破支撐停損 / 壓力不過放空 / 空單回補")
lot_count = t2.number_input("交易張數", min_value=1, max_value=100, value=1, step=1)

with t3:
    if st.button("🔴 買入做多", type="primary", use_container_width=True, disabled=trade_actions_disabled):
        ok, msg = buy_shares(current_row, lot_count, trade_note)
        if ok:
            st.toast(msg)
        else:
            st.error(msg)
        st.rerun()

with t4:
    if st.button("🟢 賣出多單", use_container_width=True, disabled=trade_actions_disabled):
        ok, msg = sell_shares(current_row, lot_count, trade_note)
        if ok:
            st.toast(msg)
        else:
            st.error(msg)
        st.rerun()

with t5:
    if st.button("🔵 放空", use_container_width=True, disabled=trade_actions_disabled):
        ok, msg = short_shares(current_row, lot_count, trade_note)
        if ok:
            st.toast(msg)
        else:
            st.error(msg)
        st.rerun()

with t6:
    if st.button("🟣 回補空單", use_container_width=True, disabled=trade_actions_disabled):
        ok, msg = cover_shares(current_row, lot_count, trade_note)
        if ok:
            st.toast(msg)
        else:
            st.error(msg)
        st.rerun()

with t7:
    if st.button("⚪ 全部平倉", use_container_width=True, disabled=trade_actions_disabled):
        ok, msg = close_all(current_row, trade_note)
        if ok:
            st.toast(msg)
        else:
            st.error(msg)
        st.rerun()

# =========================================================
# 13. Chart
# =========================================================
visible_start = max(0, st.session_state.challenge_start_idx - lookback_bars + 1)
visible_end = st.session_state.challenge_end_idx if st.session_state.show_answer else st.session_state.current_idx
visible_df = df.iloc[visible_start: visible_end + 1]
challenge_id = f"{st.session_state.challenge_start_idx}_{st.session_state.challenge_end_idx}"

battle_overlay_html = ""
chart_focus_mode = mode == "對戰模式" and battle_room_started
if chart_focus_mode:
    pnl_class = "good" if return_pct >= 0 else "bad"
    timer_text = "結束" if battle_game_over else (battle_time_status.get("time_text", "--") if battle_time_status else "--")
    battle_overlay_html = (
        f"<div>房間 {battle_room_code}｜第 {battle_question_no}/{battle_question_count} 題</div>"
        f"<div>剩餘時間</div><div class='big'>{timer_text}</div>"
        f"<div>目前總資產：{total_equity:,.0f} 元</div>"
        f"<div>目前損益：<span class='{pnl_class}'>{total_equity - initial_cash:,.0f} 元（{return_pct:.2f}%）</span></div>"
        f"<div>持倉：{get_position_label()}</div>"
    )

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
    allow_back_actions=allow_back_actions,
    overlay_html=battle_overlay_html,
    focus_mode=chart_focus_mode,
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
show_stage_result = st.session_state.current_idx >= st.session_state.challenge_end_idx or (mode == "對戰模式" and battle_time_up)
if show_stage_result:
    st.markdown("### 🏁 闖關結果")
    if mode == "對戰模式" and battle_time_up and st.session_state.current_idx < st.session_state.challenge_end_idx:
        st.error(f"⏰ 本題時間到！系統已用目前總資產 {total_equity:,.0f} 元自動提交本題成績。")
    elif return_pct >= target_return_pct:
        st.success(f"過關！目標 {target_return_pct:.2f}%，你的報酬率 {return_pct:.2f}%。")
    else:
        st.error(f"未過關。目標 {target_return_pct:.2f}%，你的報酬率 {return_pct:.2f}%。")

    level = "S 級" if return_pct >= 20 else "A 級" if return_pct >= 10 else "B 級" if return_pct >= 5 else "C 級" if return_pct >= 0 else "D 級"
    st.metric("本關評級", level)

    if mode == "對戰模式":
        submitted_scores = get_player_battle_scores(battle_room_code, battle_player_name)
        already_submitted = str(battle_question_no) in submitted_scores
        if already_submitted:
            st.info("你已提交本題成績；本題操作已鎖定，系統會依房間時間自動進入下一題。")

        c_submit, c_next = st.columns([1, 1])
        can_submit_battle = battle_room_joined
        if not can_submit_battle:
            st.warning("請先在左側建立或加入房間，才能提交本題成績。")

        with c_submit:
            if st.button("🏁 提交本題成績到房間排行榜", type="primary", use_container_width=True, disabled=not can_submit_battle):
                submit_battle_score(
                    room_code=battle_room_code,
                    player_name=battle_player_name,
                    question_no=battle_question_no,
                    final_equity=total_equity,
                    return_pct=return_pct,
                    ticker=actual_loaded_ticker,
                    interval_label=interval_label,
                    challenge_bars=challenge_bars,
                    trade_count=len(st.session_state.trade_log),
                )
                st.session_state.battle_last_submit_message = f"已提交第 {battle_question_no} 題：{total_equity:,.0f} 元"
                st.rerun()
        with c_next:
            if st.button("➡️ 等待下一題自動開始", use_container_width=True, disabled=True):
                next_question_no = min(battle_question_count, battle_question_no + 1)
                st.session_state.battle_question_no = next_question_no
                reset_question_timer(battle_room_code, battle_player_name, next_question_no)
                st.session_state.pending_new_challenge = True
                st.rerun()

        if st.session_state.get("battle_last_submit_message"):
            st.success(st.session_state.battle_last_submit_message)

        if already_submitted and battle_question_no >= battle_question_count:
            st.success("你已完成本房全部關卡，等待其他玩家完成後會顯示最終勝負。")

        latest_leaderboard = build_battle_leaderboard(battle_room_code)
        if not latest_leaderboard.empty:
            st.markdown("#### 🏆 最新房間排行榜")
            st.dataframe(latest_leaderboard, use_container_width=True, hide_index=True)

st.markdown("#### 📒 買賣紀錄")
if st.session_state.trade_log:
    trade_df = pd.DataFrame(st.session_state.trade_log)
    # 相容舊版交易紀錄：若使用者部署更新前已有紀錄，補上新欄位避免 KeyError。
    for missing_col in ["short_margin_after", "short_sale_proceeds_after", "short_maintenance_rate"]:
        if missing_col not in trade_df.columns:
            trade_df[missing_col] = None
    cols = [
        "relative_bar" if blind_mode else "time", "side", "shares", "price",
        "position_after", "avg_cost_after", "short_margin_after",
        "short_sale_proceeds_after", "short_maintenance_rate",
        "realized_pnl", "unrealized_pnl", "total_equity", "reason"
    ]
    rename = {
        "relative_bar": "相對K數",
        "time": "時間",
        "side": "動作",
        "shares": "股數",
        "price": "價格",
        "position_after": "交易後持倉",
        "avg_cost_after": "交易後均價",
        "short_margin_after": "融券保證金",
        "short_sale_proceeds_after": "融券賣出價款",
        "short_maintenance_rate": "融券維持率%",
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


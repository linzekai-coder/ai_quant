import os
import json
import secrets
from datetime import datetime, timedelta
from html import escape

import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy import text
try:
    import extra_streamlit_components as stx
except ImportError:
    stx = None

try:
    import plotly.graph_objects as go
except ImportError:
    go = None


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in os.sys.path:
    os.sys.path.insert(0, PROJECT_ROOT)

from stock_pool import get_stock_pool
from data.tushare_provider import (
    fetch_daily_basic,
    fetch_daily_price,
    fetch_latest_quote,
    find_stock,
    get_stock_basic_list,
)

from database.watchlist import (
    delete_watchlist_stock,
    list_watchlist,
    set_watchlist_enabled,
    upsert_watchlist_stock,
)
from database.daily_candidates import list_latest_candidates
from database.dynamic_pool import get_latest_dynamic_pool_summary, list_dynamic_pool
from database.users import (
    authenticate_user,
    authenticate_remember_token,
    clear_remember_token,
    create_user,
    ensure_default_admin,
    get_user_by_id,
    list_users,
    save_remember_token,
    set_user_active,
    update_user_password,
    validate_password_strength,
    validate_username,
)
from database.connection import engine
from database.user_settings import get_user_settings, reset_user_settings, save_user_settings
from database.backtest_results import list_backtest_results, save_backtest_result
from strategy.score import score_level
from watchlist_pipeline import process_watchlist_stock
from dynamic_pool_pipeline import DynamicPoolConfig, run_dynamic_pool_scan
from news.free_news_sentiment import analyze_watchlist_news
from database.news_sentiment import (
    get_news_sentiment,
    list_latest_news_sentiments,
    list_news_sentiments,
)
from database.news_raw import list_news_raw

STOCKS_DIR = os.path.join(PROJECT_ROOT, "data", "stocks")
STOCK_POOL = get_stock_pool()
STOCK_NAMES = {
    item["code"]: item["name"]
    for item in STOCK_POOL
}
STOCK_METADATA = {
    item["code"]: {
        "industry": item.get("industry", "未分类"),
        "market_cap": item.get("market_cap", "未分类"),
    }
    for item in STOCK_POOL
}
STOCK_OPTIONS = [
    f"{item['code']} {item['name']}"
    for item in STOCK_POOL
]
A_SHARE_UP_COLOR = "#E84B4B"
A_SHARE_DOWN_COLOR = "#1DB954"
A_SHARE_FLAT_COLOR = "#6b7280"
AI_SCORE_COLOR = "#2563eb"
LOADING_TEXT = "数据获取中..."
SESSION_TIMEOUT_SECONDS = 8 * 60 * 60
REMEMBER_COOKIE = "ai_quant_remember"

COLUMN_LABELS = {
    "id": "ID",
    "stock_code": "股票代码",
    "stock_name": "股票名称",
    "industry": "行业",
    "market_cap": "市值",
    "source": "来源",
    "enabled": "状态",
    "note": "备注",
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "score": "评分",
    "level": "等级",
    "display_name": "股票",
    "trade_date": "交易日期",
    "reason": "推荐理由",
    "score_date": "评分日期",
    "return_1d": "1日收益",
    "return_3d": "3日收益",
    "return_5d": "5日收益",
    "return_10d": "10日收益",
    "evaluated_at": "验证时间",
    "ma_status": "均线状态",
}


def stock_source_map():
    return {
        item["code"]: item.get("source", "扫描")
        for item in STOCK_POOL
    }


def display_table(dataframe):
    return dataframe.rename(columns=COLUMN_LABELS)


def clear_session_state():
    for key in list(st.session_state.keys()):
        del st.session_state[key]


@st.cache_resource(show_spinner=False)
def cookie_manager():
    if stx is None:
        return None
    return stx.CookieManager()


def read_remember_cookie():
    manager = cookie_manager()
    if manager is None:
        return None, None
    raw_value = manager.get(cookie=REMEMBER_COOKIE)
    if not raw_value or ":" not in str(raw_value):
        return None, None
    user_id, token = str(raw_value).split(":", 1)
    if not user_id.isdigit() or not token:
        return None, None
    return int(user_id), token


def write_remember_cookie(user_id):
    manager = cookie_manager()
    if manager is None:
        return
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=7)
    save_remember_token(user_id, token, expires_at)
    manager.set(
        REMEMBER_COOKIE,
        f"{user_id}:{token}",
        expires_at=expires_at,
        key="set_remember_cookie",
    )


def delete_remember_cookie(user_id=None):
    manager = cookie_manager()
    if user_id:
        clear_remember_token(user_id)
    if manager is not None:
        manager.delete(REMEMBER_COOKIE, key="delete_remember_cookie")


def set_current_user(user):
    st.session_state["user_id"] = user["id"]
    st.session_state["username"] = user["username"]
    st.session_state["display_name"] = user["display_name"]
    st.session_state["role"] = user["role"]
    st.session_state["must_change_password"] = int(user.get("must_change_password", 0) or 0)
    st.session_state["session_version"] = int(user.get("session_version", 0) or 0)
    st.session_state["login_at"] = datetime.now().timestamp()
    st.session_state["last_active_at"] = datetime.now().timestamp()


def current_user():
    user_id = st.session_state.get("user_id")
    if not user_id:
        return None
    user = get_user_by_id(user_id)
    if not user or not int(user.get("is_active", 0)):
        return None
    if int(user.get("session_version", 0) or 0) != int(st.session_state.get("session_version", 0) or 0):
        return None
    return user


def get_current_user_id():
    user_id = st.session_state.get("user_id")
    if not user_id:
        require_authentication()
        st.stop()
    return int(user_id)


def get_current_user_settings():
    return get_user_settings(get_current_user_id())


def current_score_weights():
    settings = get_current_user_settings()
    return {
        "technical": settings["score.technical_weight"],
        "fundamental": settings["score.fundamental_weight"],
        "sentiment": settings["score.sentiment_weight"],
    }


def session_expired():
    last_active = st.session_state.get("last_active_at")
    if not last_active:
        return False
    return datetime.now().timestamp() - float(last_active) > SESSION_TIMEOUT_SECONDS


def logout():
    delete_remember_cookie(st.session_state.get("user_id"))
    clear_session_state()
    st.rerun()


def render_user_header(user):
    cols = st.columns([4, 1.2])
    cols[0].markdown(
        f"<div style='text-align:right;color:#475569;padding-top:0.35rem;'>当前用户："
        f"<strong>{escape(str(user['display_name']))}</strong></div>",
        unsafe_allow_html=True,
    )
    if cols[1].button("退出登录", key="logout_button"):
        logout()


def render_change_password(user):
    st.title("修改初始密码")
    st.info("默认管理员首次登录必须修改密码后才能进入系统。")
    with st.form("force_change_password"):
        password = st.text_input("新密码", type="password")
        confirm = st.text_input("确认新密码", type="password")
        submitted = st.form_submit_button("保存并进入系统")
        if submitted:
            if password != confirm:
                st.error("两次输入的密码不一致")
                return
            try:
                update_user_password(user["id"], password)
            except ValueError as exc:
                st.error(str(exc))
                return
            updated = get_user_by_id(user["id"])
            set_current_user(updated)
            st.success("密码已更新")
            st.rerun()


def render_login_form():
    st.title("AI量化交易系统")
    tab_login, tab_register = st.tabs(["登录", "注册"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("用户名")
            password = st.text_input("密码", type="password")
            remember_me = st.checkbox("记住我（7天免登录）", value=True)
            submitted = st.form_submit_button("登录")
        if submitted:
            user, error = authenticate_user(username.strip(), password)
            if error:
                st.error(error)
            else:
                set_current_user(user)
                st.session_state["remember_me"] = bool(remember_me)
                if remember_me:
                    write_remember_cookie(user["id"])
                st.rerun()

    with tab_register:
        with st.form("register_form"):
            username = st.text_input("用户名", key="register_username")
            display_name = st.text_input("昵称")
            email = st.text_input("邮箱")
            password = st.text_input("密码", type="password", key="register_password")
            confirm = st.text_input("确认密码", type="password")
            submitted = st.form_submit_button("注册并登录")
        if submitted:
            if not validate_username(username.strip()):
                st.error("用户名只允许字母、数字、下划线，长度 3-20 位")
                return
            if password != confirm:
                st.error("两次输入的密码不一致")
                return
            if not validate_password_strength(password):
                st.error("密码至少 8 位，且必须包含字母和数字")
                return
            try:
                user = create_user(username, display_name, email, password)
            except ValueError as exc:
                st.error(str(exc))
                return
            set_current_user(user)
            st.rerun()


def require_authentication():
    ensure_default_admin()
    if session_expired():
        clear_session_state()
        st.warning("登录已超时，请重新登录")

    user = current_user()
    if not user:
        cookie_user_id, token = read_remember_cookie()
        if cookie_user_id and token:
            remembered = authenticate_remember_token(cookie_user_id, token)
            if remembered:
                set_current_user(remembered)
                user = remembered

    if not user:
        render_login_form()
        st.stop()

    st.session_state["last_active_at"] = datetime.now().timestamp()
    if int(user.get("must_change_password", 0) or 0):
        render_change_password(user)
        st.stop()
    return user


def pick_column(dataframe, candidates, fallback_index):
    for candidate in candidates:
        if candidate in dataframe.columns:
            return candidate
    if len(dataframe.columns) > fallback_index:
        return dataframe.columns[fallback_index]
    return None


def get_stock_name(stock_code):
    code = str(stock_code).zfill(6)
    if code in STOCK_NAMES:
        local_name = str(STOCK_NAMES[code])
        if local_name and local_name != code and "?" not in local_name:
            return local_name

    basic = find_stock(code)
    if basic and basic.get("name"):
        return str(basic["name"])

    for filename in os.listdir(STOCKS_DIR):
        if not filename.endswith(".csv"):
            continue

        csv_path = os.path.join(STOCKS_DIR, filename)
        try:
            sample = pd.read_csv(csv_path, nrows=1, dtype={"代码": str})
        except Exception:
            continue

        if "代码" in sample.columns and "股票名称" in sample.columns:
            sample_code = str(sample.loc[0, "代码"]).zfill(6)
            if sample_code == code:
                return sample.loc[0, "股票名称"]

    return code


def get_stock_metadata(stock_code):
    code = str(stock_code).zfill(6)
    basic = find_stock(code)
    local_metadata = STOCK_METADATA.get(code)
    if local_metadata:
        local_industry = local_metadata.get("industry", "未分类")
        local_market_cap = local_metadata.get("market_cap", "未分类")
        if local_industry not in ("自选", "推荐池", "扫描", "未分类"):
            return local_metadata
        if basic:
            return {
                "industry": basic.get("industry") or local_industry,
                "market_cap": basic.get("market") or local_market_cap,
            }
        return local_metadata

    if basic:
        return {
            "industry": basic.get("industry") or "未分类",
            "market_cap": basic.get("market") or "未分类",
        }
    return {
        "industry": "未分类",
        "market_cap": "未分类",
    }


def industry_badge_html(industry, market_cap=None):
    label = escape(str(industry))
    if market_cap and str(market_cap) != "未分类":
        label = f"{label} · {escape(str(market_cap))}"
    return (
        "<span style='display:inline-flex;align-items:center;padding:0.18rem 0.54rem;"
        "border-radius:999px;background:#eef2ff;color:#3730a3;border:1px solid #c7d2fe;"
        f"font-size:0.82rem;font-weight:700;'>{label}</span>"
    )


def source_badge_html(source):
    source = str(source or "扫描")
    styles = {
        "自选": "background:#dcfce7;color:#166534;border:1px solid #86efac;",
        "扫描": "background:#dbeafe;color:#1d4ed8;border:1px solid #93c5fd;",
        "默认": "background:#f3f4f6;color:#374151;border:1px solid #d1d5db;",
    }
    style = styles.get(source, styles["扫描"])
    return (
        "<span style='display:inline-flex;align-items:center;justify-content:center;"
        "min-width:3rem;padding:0.18rem 0.54rem;border-radius:999px;"
        f"font-size:0.82rem;font-weight:800;{style}'>"
        f"{escape(source)}</span>"
    )


def normalize_price_frame(price_df):
    if price_df.empty:
        return pd.DataFrame()

    date_col = pick_column(price_df, ["日期"], 0)
    open_col = pick_column(price_df, ["开盘"], 3)
    high_col = pick_column(price_df, ["最高"], 4)
    low_col = pick_column(price_df, ["最低"], 5)
    close_col = pick_column(price_df, ["收盘"], 6)
    volume_col = pick_column(price_df, ["成交量"], 8)
    pct_col = pick_column(price_df, ["涨跌幅"], 11)
    if not date_col or not close_col:
        return pd.DataFrame()

    normalized = pd.DataFrame({
        "日期": pd.to_datetime(price_df[date_col], errors="coerce"),
        "收盘": pd.to_numeric(price_df[close_col], errors="coerce"),
    })
    optional_cols = {
        "开盘": open_col,
        "最高": high_col,
        "最低": low_col,
        "成交量": volume_col,
        "涨跌幅": pct_col,
    }
    for target_col, source_col in optional_cols.items():
        if source_col:
            normalized[target_col] = pd.to_numeric(price_df[source_col], errors="coerce")

    if "涨跌幅" not in normalized.columns:
        normalized["涨跌幅"] = normalized["收盘"].pct_change() * 100

    normalized = normalized.dropna(subset=["日期", "收盘"]).sort_values("日期")
    return normalized.reset_index(drop=True)


def load_local_price_data(stock_code):
    code = str(stock_code).zfill(6)
    stock_name = STOCK_NAMES.get(code)
    csv_path = None
    if stock_name:
        named_path = os.path.join(STOCKS_DIR, f"{stock_name}.csv")
        if os.path.exists(named_path):
            csv_path = named_path
    if not csv_path:
        csv_path = resolve_stock_csv(code)
    if not csv_path:
        return pd.DataFrame()

    try:
        price_df = pd.read_csv(csv_path, dtype={"代码": str}, encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame()

    return normalize_price_frame(price_df)


def fetch_tushare_price_data(stock_code, start_date, end_date):
    return normalize_price_frame(fetch_daily_price(stock_code, start_date, end_date))


@st.cache_data(ttl=300, show_spinner=False)
def get_spot_market_data():
    return get_stock_basic_list()


def spot_market_available():
    return not get_spot_market_data().empty


def normalize_stock_code(stock_code):
    code = str(stock_code).strip().upper()
    if "." in code:
        parts = code.split(".")
        code = parts[0] if parts[0].isdigit() else parts[-1]
    code = code.replace("SH", "").replace("SZ", "")
    digits = "".join(ch for ch in code if ch.isdigit())
    return digits.zfill(6) if digits else ""


def market_prefixed_code(stock_code):
    code = normalize_stock_code(stock_code)
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def match_spot_stock(stock_code):
    code = normalize_stock_code(stock_code)
    ts_code = market_prefixed_code(code)
    spot_df = get_spot_market_data()
    if spot_df.empty:
        return None

    for candidate in ["symbol", "代码", "code", "股票代码"]:
        if candidate in spot_df.columns:
            code_col = candidate
            break
    else:
        code_col = spot_df.columns[1] if len(spot_df.columns) > 1 else spot_df.columns[0]

    codes = spot_df[code_col].astype(str).str.lower()
    ts_codes = spot_df["ts_code"].astype(str).str.upper() if "ts_code" in spot_df.columns else pd.Series([], dtype=str)
    matched = spot_df[(codes.str.zfill(6) == code.lower()) | (ts_codes == ts_code.upper())]
    if matched.empty:
        return None
    return matched.iloc[0]


@st.cache_data(ttl=300, show_spinner=False)
def get_spot_stock_quote(stock_code):
    return fetch_latest_quote(stock_code)


@st.cache_data(ttl=3600, show_spinner=False)
def get_recent_price_data(stock_code):
    code = str(stock_code).zfill(6)
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=140)).strftime("%Y%m%d")

    price_df = fetch_tushare_price_data(code, start_date, end_date)
    if price_df.empty:
        price_df = load_local_price_data(code)

    return price_df.tail(60).reset_index(drop=True)


def get_ma_status(stock_code):
    price_df = get_recent_price_data(stock_code)
    if price_df.empty:
        return "震荡整理"

    closes = price_df["收盘"].dropna().tail(60)
    if len(closes) < 20:
        return "震荡整理"

    ma5 = closes.rolling(5).mean().iloc[-1]
    ma10 = closes.rolling(10).mean().iloc[-1]
    ma20 = closes.rolling(20).mean().iloc[-1]

    if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20):
        return "震荡整理"
    if ma5 > ma10 > ma20:
        return "多头排列"
    if ma5 < ma10 < ma20:
        return "空头排列"
    return "震荡整理"


def get_price_summary(stock_code):
    price_df = get_recent_price_data(stock_code)
    quote = get_spot_stock_quote(stock_code)
    if price_df.empty:
        return pd.DataFrame(), quote["price"], quote["pct_change"], pd.NA

    spark_df = price_df.tail(20).copy()
    if spark_df.empty:
        return spark_df, quote["price"], quote["pct_change"], pd.NA

    current_price = quote["price"] if pd.notna(quote["price"]) else spark_df["收盘"].iloc[-1]
    fallback_pct = spark_df["涨跌幅"].iloc[-1] if "涨跌幅" in spark_df.columns else pd.NA
    pct_change = quote["pct_change"] if pd.notna(quote["pct_change"]) else fallback_pct
    trend_return = spark_df["收盘"].iloc[-1] - spark_df["收盘"].iloc[0]
    return spark_df, current_price, pct_change, trend_return


def trend_color(value):
    if pd.isna(value) or value == 0:
        return A_SHARE_FLAT_COLOR
    return A_SHARE_UP_COLOR if value > 0 else A_SHARE_DOWN_COLOR


def format_colored_number(value, suffix="", precision=2):
    if pd.isna(value):
        return f"<span style='color:#6b7280;'>{LOADING_TEXT}</span>"
    color = trend_color(value)
    prefix = "+" if value > 0 else ""
    return f"<span style='color:{color};font-weight:700;'>{prefix}{value:.{precision}f}{suffix}</span>"


@st.cache_data(ttl=3600, show_spinner=False)
def get_detail_price_data(stock_code):
    code = str(stock_code).zfill(6)
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=220)).strftime("%Y%m%d")

    required_cols = ["日期", "开盘", "最高", "最低", "收盘"]
    price_df = fetch_tushare_price_data(code, start_date, end_date)
    if price_df.empty:
        price_df = load_local_price_data(code)

    if price_df.empty or any(col not in price_df.columns for col in required_cols):
        return pd.DataFrame()

    price_df = price_df[required_cols].copy().sort_values("日期")
    price_df["MA5"] = price_df["收盘"].rolling(5).mean()
    price_df["MA20"] = price_df["收盘"].rolling(20).mean()
    return price_df.tail(30).reset_index(drop=True)


def get_watchlist_indicators(stock_code):
    price_df = get_recent_price_data(stock_code)
    if price_df.empty:
        return {
            "MA5": pd.NA,
            "MA10": pd.NA,
            "MA20": pd.NA,
            "成交量": pd.NA,
            "PE": pd.NA,
            "PB": pd.NA,
        }

    indicators = {
        "MA5": price_df["收盘"].rolling(5).mean().iloc[-1] if len(price_df) >= 5 else pd.NA,
        "MA10": price_df["收盘"].rolling(10).mean().iloc[-1] if len(price_df) >= 10 else pd.NA,
        "MA20": price_df["收盘"].rolling(20).mean().iloc[-1] if len(price_df) >= 20 else pd.NA,
        "成交量": pd.NA,
        "PE": pd.NA,
        "PB": pd.NA,
    }
    volume_col = pick_column(price_df, ["成交量"], 8)
    if volume_col in price_df.columns:
        indicators["成交量"] = pd.to_numeric(price_df[volume_col], errors="coerce").iloc[-1]

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=20)).strftime("%Y%m%d")
    indicator_df = fetch_daily_basic(stock_code, start_date, end_date)
    if not indicator_df.empty:
        latest = indicator_df.tail(1).iloc[0]
        indicators["PE"] = pd.to_numeric(latest.get("pe"), errors="coerce")
        indicators["PB"] = pd.to_numeric(latest.get("pb"), errors="coerce")

    return indicators


def build_detail_score_data(history_df, price_df):
    if history_df.empty or price_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    scores = history_df[["created_at", "score"]].copy()
    scores["score_date"] = pd.to_datetime(scores["created_at"], errors="coerce").dt.normalize()
    scores["score_date"] = pd.to_datetime(scores["score_date"], errors="coerce").astype("datetime64[ns]")
    scores["score"] = pd.to_numeric(scores["score"], errors="coerce")
    scores = scores.dropna(subset=["score_date", "score"])
    if scores.empty:
        return pd.DataFrame(), pd.DataFrame()

    trade_dates = price_df[["日期"]].rename(columns={"日期": "score_date"}).sort_values("score_date")
    trade_dates["score_date"] = pd.to_datetime(trade_dates["score_date"], errors="coerce").astype("datetime64[ns]")
    trade_dates = trade_dates.dropna(subset=["score_date"]).sort_values("score_date")
    aligned = pd.merge_asof(
        trade_dates,
        scores.sort_values("score_date"),
        on="score_date",
        direction="backward",
    )
    aligned["score"] = aligned["score"].ffill()
    aligned = aligned.dropna(subset=["score"])
    if aligned.empty:
        return aligned, pd.DataFrame()

    change_points = aligned[aligned["score"].ne(aligned["score"].shift())].copy()
    if not scores.empty and not change_points.empty:
        first_date = aligned["score_date"].iloc[0]
        change_points = change_points[change_points["score_date"] != first_date]
    return aligned, change_points


def load_stock_news_sentiment_data(stock_code, days=7):
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = list_news_sentiments(stock_code, start_date=start_date, end_date=end_date, limit=days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df["sentiment_score"] = pd.to_numeric(df["sentiment_score"], errors="coerce")
    if "final_sentiment_score" in df.columns:
        df["final_sentiment_score"] = pd.to_numeric(df["final_sentiment_score"], errors="coerce").fillna(df["sentiment_score"])
    else:
        df["final_sentiment_score"] = df["sentiment_score"]
    df["news_count"] = pd.to_numeric(df["news_count"], errors="coerce").fillna(0).astype(int)
    return df.dropna(subset=["trade_date"]).sort_values("trade_date").reset_index(drop=True)


def sentiment_color(sentiment):
    if sentiment == "正面":
        return A_SHARE_UP_COLOR
    if sentiment == "负面":
        return "#00AA00"
    return A_SHARE_FLAT_COLOR


def sentiment_icon(sentiment):
    if sentiment == "正面":
        return "▲"
    if sentiment == "负面":
        return "▼"
    return "●"


def render_detail_trend_chart(stock_code, history_df):
    st.subheader("近30日价格、AI评分与新闻情绪")

    if go is None:
        st.warning("缺少 plotly 依赖，请先运行 python -m pip install -r requirements.txt")
        return

    price_df = get_detail_price_data(stock_code)
    if price_df.empty:
        st.info("暂无可用行情数据，无法生成近30日价格与AI评分图")
        return

    if len(history_df) < 2:
        st.caption("当前股票只有 1 条历史评分记录，AI评分只能显示单个点；多次运行评分任务后会形成趋势线。")

    score_df, score_changes = build_detail_score_data(history_df, price_df)
    sentiment_df = load_stock_news_sentiment_data(stock_code, days=7)
    fig = go.Figure()

    candle_groups = [
        ("阳线", price_df["收盘"] > price_df["开盘"], A_SHARE_UP_COLOR),
        ("阴线", price_df["收盘"] < price_df["开盘"], A_SHARE_DOWN_COLOR),
        ("平盘", price_df["收盘"] == price_df["开盘"], A_SHARE_FLAT_COLOR),
    ]
    for name, mask, color in candle_groups:
        candle_df = price_df[mask]
        if candle_df.empty:
            continue
        fig.add_trace(go.Candlestick(
            x=candle_df["日期"],
            open=candle_df["开盘"],
            high=candle_df["最高"],
            low=candle_df["最低"],
            close=candle_df["收盘"],
            name=name,
            yaxis="y",
            increasing_line_color=color,
            decreasing_line_color=color,
            increasing_fillcolor=color,
            decreasing_fillcolor=color,
            hovertemplate=(
                "日期：%{x|%Y-%m-%d}<br>"
                "开盘：%{open:.2f}<br>"
                "最高：%{high:.2f}<br>"
                "最低：%{low:.2f}<br>"
                "收盘：%{close:.2f}<extra></extra>"
            ),
        ))
    fig.add_trace(go.Scatter(
        x=price_df["日期"],
        y=price_df["MA5"],
        mode="lines",
        name="MA5",
        line={"color": "#f59e0b", "width": 1.8},
        yaxis="y",
        hovertemplate="日期：%{x|%Y-%m-%d}<br>MA5：%{y:.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=price_df["日期"],
        y=price_df["MA20"],
        mode="lines",
        name="MA20",
        line={"color": "#7c3aed", "width": 1.8},
        yaxis="y",
        hovertemplate="日期：%{x|%Y-%m-%d}<br>MA20：%{y:.2f}<extra></extra>",
    ))

    if not score_df.empty:
        fig.add_trace(go.Scatter(
            x=score_df["score_date"],
            y=score_df["score"],
            mode="lines+markers",
            name="AI评分",
            line={"color": AI_SCORE_COLOR, "width": 2, "dash": "dash"},
            marker={"size": 6},
            yaxis="y2",
            hovertemplate="日期：%{x|%Y-%m-%d}<br>AI评分：%{y:.1f}<extra></extra>",
        ))

    for _, row in score_changes.iterrows():
        fig.add_vline(
            x=row["score_date"],
            line_width=1,
            line_dash="dot",
            line_color=AI_SCORE_COLOR,
            opacity=0.35,
        )

    if not score_changes.empty:
        fig.add_trace(go.Scatter(
            x=score_changes["score_date"],
            y=score_changes["score"],
            mode="markers",
            name="评分变化",
            marker={"symbol": "diamond", "size": 9, "color": AI_SCORE_COLOR},
            yaxis="y2",
            hovertemplate="评分变化日：%{x|%Y-%m-%d}<br>AI评分：%{y:.1f}<extra></extra>",
        ))

    if not sentiment_df.empty:
        fig.add_trace(go.Scatter(
            x=sentiment_df["trade_date"],
            y=sentiment_df["final_sentiment_score"],
            mode="lines+markers",
            name="综合新闻情绪",
            line={"color": "#0f766e", "width": 2, "dash": "dot"},
            marker={
                "size": 8,
                "color": [sentiment_color(value) for value in sentiment_df["sentiment"]],
            },
            yaxis="y3",
            customdata=sentiment_df[["sentiment", "news_count", "analysis_reason"]],
            hovertemplate=(
                "日期：%{x|%Y-%m-%d}<br>"
                "情绪：%{customdata[0]}<br>"
                "情绪分：%{y:.2f}<br>"
                "新闻数：%{customdata[1]}<br>"
                "原因：%{customdata[2]}<extra></extra>"
            ),
        ))

    fig.update_layout(
        height=520,
        margin={"l": 10, "r": 10, "t": 20, "b": 10},
        hovermode="x unified",
        xaxis={"title": "日期", "rangeslider": {"visible": False}},
        yaxis={"title": "收盘价", "side": "left", "showgrid": True, "zeroline": False},
        yaxis2={
            "title": "AI评分",
            "side": "right",
            "overlaying": "y",
            "range": [0, 100],
            "showgrid": False,
            "zeroline": False,
        },
        yaxis3={
            "title": "情绪分",
            "side": "right",
            "overlaying": "y",
            "anchor": "free",
            "position": 0.98,
            "range": [-1, 1],
            "showgrid": False,
            "zeroline": True,
            "zerolinecolor": "#cbd5e1",
        },
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
    )
    st.plotly_chart(fig, use_container_width=True)


def render_sparkline(column, spark_df, trend_return):
    if spark_df.empty or len(spark_df) < 2:
        column.caption("暂无数据")
        return

    chart_df = spark_df.reset_index().rename(columns={"index": "point", "收盘": "close"})
    line_color = trend_color(trend_return)
    chart = (
        alt.Chart(chart_df)
        .mark_line(color=line_color, strokeWidth=2)
        .encode(
            x=alt.X("point:Q", axis=None, title=None),
            y=alt.Y("close:Q", axis=None, title=None, scale=alt.Scale(zero=False)),
        )
        .properties(width=120, height=40)
        .configure_view(strokeWidth=0)
    )
    column.altair_chart(chart, use_container_width=False)


def render_plotly_sparkline(column, stock_code, days=10):
    price_df = get_recent_price_data(stock_code).tail(days)
    if price_df.empty or len(price_df) < 2 or go is None:
        column.caption("暂无走势")
        return

    trend_value = price_df["收盘"].iloc[-1] - price_df["收盘"].iloc[0]
    line_color = trend_color(trend_value)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=price_df["日期"],
        y=price_df["收盘"],
        mode="lines",
        line={"color": line_color, "width": 2},
        hovertemplate="日期：%{x|%Y-%m-%d}<br>收盘：%{y:.2f}<extra></extra>",
    ))
    fig.update_layout(
        height=54,
        margin={"l": 0, "r": 0, "t": 2, "b": 0},
        xaxis={"visible": False, "showgrid": False},
        yaxis={"visible": False, "showgrid": False},
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    column.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def format_price(value):
    if pd.isna(value):
        return LOADING_TEXT
    return f"{value:.2f}"


def format_pct_change(value):
    if pd.isna(value):
        return LOADING_TEXT
    return f"{value:.2f}%"


def render_pct_change(column, value):
    column.markdown(format_colored_number(value, suffix="%"), unsafe_allow_html=True)


def ma_status_badge_html(stock_code):
    status = get_ma_status(stock_code)
    styles = {
        "多头排列": "background:#dcfce7;color:#166534;border:1px solid #86efac;",
        "空头排列": "background:#fee2e2;color:#991b1b;border:1px solid #fecaca;",
        "震荡整理": "background:#f3f4f6;color:#374151;border:1px solid #d1d5db;",
    }
    return (
        "<span style='display:inline-flex;align-items:center;justify-content:center;"
        "min-width:4.8rem;padding:0.22rem 0.58rem;border-radius:999px;"
        f"font-size:0.84rem;font-weight:700;line-height:1.2;{styles[status]}'>"
        f"{escape(status)}</span>"
    )


def resolve_stock_csv(stock_code):
    code = str(stock_code).zfill(6)
    stock_name = STOCK_NAMES.get(code)
    if stock_name:
        named_path = os.path.join(STOCKS_DIR, f"{stock_name}.csv")
        if os.path.exists(named_path):
            return named_path

    for filename in os.listdir(STOCKS_DIR):
        if not filename.endswith(".csv") or "sentiment" in filename:
            continue

        csv_path = os.path.join(STOCKS_DIR, filename)
        try:
            sample = pd.read_csv(csv_path, nrows=1, dtype={"代码": str})
        except Exception:
            continue

        code_col = pick_column(sample, ["代码"], 2)
        if code_col:
            sample_code = str(sample.loc[0, code_col]).zfill(6)
            if sample_code == code:
                return csv_path

    return None


def load_price_data(stock_code):
    csv_path = resolve_stock_csv(stock_code)
    if not csv_path:
        return pd.DataFrame()

    try:
        price_df = pd.read_csv(csv_path, dtype={"代码": str})
    except Exception:
        return pd.DataFrame()

    date_col = pick_column(price_df, ["日期"], 0)
    close_col = pick_column(price_df, ["收盘"], 6)
    if not date_col or not close_col:
        return pd.DataFrame()

    price_df = price_df[[date_col, close_col]].copy()
    price_df.columns = ["trade_date", "close"]
    price_df["trade_date"] = pd.to_datetime(price_df["trade_date"], errors="coerce")
    price_df["close"] = pd.to_numeric(price_df["close"], errors="coerce")
    price_df = price_df.dropna(subset=["trade_date", "close"])
    return price_df.sort_values("trade_date")


def load_backtest_price_data(stock_code):
    csv_path = resolve_stock_csv(stock_code)
    if not csv_path:
        return pd.DataFrame()

    try:
        price_df = pd.read_csv(csv_path, dtype={"代码": str})
    except Exception:
        return pd.DataFrame()

    date_col = pick_column(price_df, ["日期"], 0)
    open_col = pick_column(price_df, ["开盘"], 3)
    high_col = pick_column(price_df, ["最高"], 4)
    low_col = pick_column(price_df, ["最低"], 5)
    close_col = pick_column(price_df, ["收盘"], 6)
    required_cols = [date_col, open_col, high_col, low_col, close_col]
    if any(column is None for column in required_cols):
        return pd.DataFrame()

    price_df = price_df[required_cols].copy()
    price_df.columns = ["trade_date", "open", "high", "low", "close"]
    price_df["trade_date"] = pd.to_datetime(price_df["trade_date"], errors="coerce")
    for col in ["open", "high", "low", "close"]:
        price_df[col] = pd.to_numeric(price_df[col], errors="coerce")
    price_df = price_df.dropna(subset=["trade_date", "open", "high", "low", "close"])
    return price_df.sort_values("trade_date").reset_index(drop=True)


def format_return(value):
    if pd.isna(value):
        return "等待后续交易日"
    return f"{value * 100:.2f}%"


def format_colored_return(value):
    if pd.isna(value):
        return "<span style='color:#6b7280;'>等待后续交易日</span>"
    return format_colored_number(value * 100, suffix="%")


def metric_html(label, value_html):
    return (
        "<div style='padding:0.85rem 1rem;border:1px solid rgba(148,163,184,0.22);"
        "border-radius:8px;background:rgba(255,255,255,0.72);'>"
        f"<div style='font-size:0.86rem;color:#64748b;margin-bottom:0.28rem;'>{escape(str(label))}</div>"
        f"<div style='font-size:1.35rem;line-height:1.2;'>{value_html}</div>"
        "</div>"
    )


def format_ratio(value):
    if pd.isna(value):
        return "等待数据"
    return f"{value * 100:.2f}%"


LEVEL_BADGE_STYLES = {
    "强烈关注": "background-color: #dcfce7; color: #166534; border: 1px solid #86efac; font-weight: 700;",
    "观察": "background-color: #fef9c3; color: #854d0e; border: 1px solid #fde68a; font-weight: 700;",
    "回避": "background-color: #fee2e2; color: #991b1b; border: 1px solid #fecaca; font-weight: 700;",
}


def enrich_level_from_score(dataframe):
    if "level" in dataframe.columns or "score" not in dataframe.columns:
        return dataframe

    dataframe = dataframe.copy()
    dataframe["level"] = dataframe["score"].apply(score_level)
    return dataframe


def map_candidate_status(dataframe):
    if "enabled" not in dataframe.columns:
        return dataframe

    dataframe = dataframe.copy()
    dataframe["enabled"] = dataframe["enabled"].map({1: "有效", 0: "失效"}).fillna("待验证")
    return dataframe


def score_band(score):
    if score >= 70:
        return "强烈关注（≥70）"
    if score >= 40:
        return "观察（40-69）"
    return "回避（<40）"


def max_drawdown(returns):
    returns = pd.Series(returns).dropna()
    if returns.empty:
        return pd.NA
    return returns.min()


def build_validation_summary(validation_df, return_cols):
    rows = []
    for level, group in validation_df.groupby("level", dropna=False):
        for col in return_cols:
            valid_returns = group[col].dropna()
            rows.append({
                "level": level,
                "horizon": COLUMN_LABELS[col],
                "sample_count": int(valid_returns.count()),
                "win_rate": pd.NA if valid_returns.empty else (valid_returns > 0).mean(),
                "avg_return": pd.NA if valid_returns.empty else valid_returns.mean(),
                "max_drawdown": max_drawdown(valid_returns),
            })

    return pd.DataFrame(rows)


def run_ma_backtest(
    price_df,
    short_window,
    long_window,
    initial_cash,
    commission,
    slippage,
):
    df = price_df.copy()
    df[f"MA{short_window}"] = df["close"].rolling(short_window).mean()
    df[f"MA{long_window}"] = df["close"].rolling(long_window).mean()
    df["prev_short_ma"] = df[f"MA{short_window}"].shift(1)
    df["prev_long_ma"] = df[f"MA{long_window}"].shift(1)

    buy = (df["prev_short_ma"] <= df["prev_long_ma"]) & (df[f"MA{short_window}"] > df[f"MA{long_window}"])
    sell = (df["prev_short_ma"] >= df["prev_long_ma"]) & (df[f"MA{short_window}"] < df[f"MA{long_window}"])

    df["signal"] = 0
    df.loc[buy, "signal"] = 1
    df.loc[sell, "signal"] = -1

    cash = float(initial_cash)
    shares = 0
    position = 0
    trade_records = []
    equity_values = []

    for _, row in df.iterrows():
        signal = row["signal"]
        close = row["close"]

        if signal == 1 and position == 0:
            exec_price = close * (1 + slippage)
            lot_cost = exec_price * 100
            max_shares = int(cash // lot_cost) * 100
            if max_shares > 0:
                fee = max_shares * exec_price * commission
                cash -= max_shares * exec_price + fee
                shares = max_shares
                position = 1
                trade_records.append({
                    "trade_date": row["trade_date"],
                    "action": "买入",
                    "price": exec_price,
                    "shares": shares,
                    "fee": fee,
                    "cash": cash,
                })
        elif signal == -1 and position == 1:
            exec_price = close * (1 - slippage)
            fee = shares * exec_price * commission
            cash += shares * exec_price - fee
            trade_records.append({
                "trade_date": row["trade_date"],
                "action": "卖出",
                "price": exec_price,
                "shares": shares,
                "fee": fee,
                "cash": cash,
            })
            shares = 0
            position = 0

        equity_values.append(cash + shares * close)

    df["equity"] = equity_values
    df["strategy_nav"] = df["equity"] / initial_cash
    df["market_nav"] = df["close"] / df["close"].iloc[0]
    df["strategy_return"] = df["strategy_nav"].pct_change()
    df["drawdown"] = df["strategy_nav"] / df["strategy_nav"].cummax() - 1

    final_value = df["equity"].iloc[-1]
    total_return = final_value / initial_cash - 1
    buy_hold_return = df["market_nav"].iloc[-1] - 1
    daily_returns = df["strategy_return"].dropna()
    sharpe = 0
    if not daily_returns.empty and daily_returns.std() > 0:
        sharpe = (252 ** 0.5) * daily_returns.mean() / daily_returns.std()

    trade_df = pd.DataFrame(trade_records)
    total_fee = trade_df["fee"].sum() if not trade_df.empty else 0
    metrics = {
        "策略累计收益率": total_return,
        "买入持有收益率": buy_hold_return,
        "超额收益": total_return - buy_hold_return,
        "夏普比率": sharpe,
        "最大回撤": df["drawdown"].min(),
        "期末总资产": final_value,
        "交易次数": len(trade_df),
        "信号次数": int(df["signal"].abs().sum()),
        "总手续费": total_fee,
    }
    return df, trade_df, metrics


def style_level_badges(dataframe):
    display_df = display_table(dataframe)
    level_col = COLUMN_LABELS["level"]
    if level_col not in display_df.columns:
        return display_df

    def style_level(value):
        style = LEVEL_BADGE_STYLES.get(value)
        if not style:
            return ""
        return f"{style} text-align: center;"

    return display_df.style.map(style_level, subset=[level_col])


def level_badge_html(level):
    style = LEVEL_BADGE_STYLES.get(level, "background-color: #f3f4f6; color: #374151; border: 1px solid #e5e7eb;")
    return (
        f"<span style='display: inline-flex; align-items: center; justify-content: center; "
        f"min-width: 4.8rem; padding: 0.24rem 0.62rem; border-radius: 999px; "
        f"font-size: 0.86rem; line-height: 1.2; {style}'>{escape(str(level))}</span>"
    )


def open_stock_detail(stock_code):
    stock_code = str(stock_code).zfill(6)
    selected_option = next(
        (option for option in STOCK_OPTIONS if option.startswith(f"{stock_code} ")),
        None,
    )
    if selected_option:
        st.session_state["selected_stock_option"] = selected_option
    st.session_state["selected_stock_code"] = stock_code
    st.session_state["page"] = "股票分析"


def apply_theme():
    st.markdown(
        """
        <style>
        @keyframes ambientSweep {
            0% { transform: translateX(-18%) translateY(-4%); opacity: 0.38; }
            50% { transform: translateX(8%) translateY(3%); opacity: 0.62; }
            100% { transform: translateX(-18%) translateY(-4%); opacity: 0.38; }
        }

        @keyframes gridFlow {
            from { background-position: 0 0; }
            to { background-position: 72px 72px; }
        }

        @keyframes fadeUp {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .stApp {
            color: #1f2937;
            background:
                linear-gradient(180deg, rgba(255, 255, 255, 0.78), rgba(255, 255, 255, 0.90)),
                radial-gradient(circle at 14% 10%, rgba(37, 99, 235, 0.13), transparent 34%),
                radial-gradient(circle at 86% 6%, rgba(20, 184, 166, 0.10), transparent 30%),
                linear-gradient(135deg, #eef5ff 0%, #f7fbff 46%, #edf8f6 100%);
        }

        .stApp::before {
            content: "";
            position: fixed;
            inset: 0;
            pointer-events: none;
            z-index: 0;
            opacity: 0.46;
            background-image:
                linear-gradient(rgba(28, 100, 242, 0.055) 1px, transparent 1px),
                linear-gradient(90deg, rgba(28, 100, 242, 0.055) 1px, transparent 1px);
            background-size: 36px 36px;
            animation: gridFlow 34s linear infinite;
        }

        .stApp::after {
            content: "";
            position: fixed;
            top: 4.5rem;
            left: 8%;
            width: 58vw;
            height: 18rem;
            pointer-events: none;
            z-index: 0;
            background:
                linear-gradient(105deg, transparent 0%, rgba(37, 99, 235, 0.10) 35%, rgba(20, 184, 166, 0.12) 52%, transparent 72%);
            filter: blur(20px);
            animation: ambientSweep 12s ease-in-out infinite;
        }

        .block-container {
            position: relative;
            z-index: 1;
            max-width: 1180px;
            padding-top: 3.2rem;
            animation: fadeUp 0.45s ease-out;
        }

        h1, h2, h3, label, .stMarkdown, .stMetric label {
            color: #1f2937 !important;
        }

        h1 {
            letter-spacing: 0;
            color: #172033 !important;
        }

        h2, h3 {
            margin-top: 1.2rem;
        }

        [data-testid="stMetric"] {
            padding: 1rem 1.1rem;
            border: 1px solid rgba(37, 99, 235, 0.12);
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.72);
            box-shadow: 0 14px 40px rgba(31, 41, 55, 0.08);
            backdrop-filter: blur(14px);
        }

        [data-testid="stMetricValue"] {
            color: #172033 !important;
        }

        div[data-testid="stDataFrame"],
        div[data-testid="stTable"],
        div[data-baseweb="select"] > div,
        div[data-testid="stForm"] {
            border-radius: 8px;
            border: 1px solid rgba(37, 99, 235, 0.10);
            background: rgba(255, 255, 255, 0.78);
            box-shadow: 0 18px 44px rgba(31, 41, 55, 0.09);
            backdrop-filter: blur(14px);
        }

        div[data-testid="stDataFrame"] *,
        div[data-testid="stTable"] * {
            color: #1f2937;
        }

        div[data-testid="stForm"] {
            padding: 1.1rem 1.2rem 1.2rem;
        }

        input,
        textarea,
        div[data-baseweb="select"] span {
            color: #1f2937 !important;
        }

        .stButton > button,
        .stFormSubmitButton > button {
            border: 1px solid rgba(37, 99, 235, 0.20);
            border-radius: 8px;
            background: linear-gradient(135deg, #2563eb, #0f9f8f);
            color: white;
            transition: transform 0.16s ease, box-shadow 0.16s ease;
        }

        .stButton > button:hover,
        .stFormSubmitButton > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 10px 24px rgba(37, 99, 235, 0.20);
        }

        div[data-testid="stAlert"] {
            border-radius: 8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def load_score_data():
    with engine.connect() as conn:
        df = pd.read_sql(text("""
    SELECT *
    FROM stock_scores
    WHERE id IN (
        SELECT MAX(id)
        FROM stock_scores
        GROUP BY stock_code
    )
    ORDER BY score DESC, created_at DESC
    """), conn)

        try:
            validation_df = pd.read_sql(text("""
        SELECT *
        FROM score_validation
        ORDER BY score_date DESC, score DESC
        """), conn)
        except Exception:
            validation_df = pd.DataFrame()

    return df, validation_df


def load_history_data(stock_code):
    with engine.connect() as conn:
        history_df = pd.read_sql(text("""
    SELECT *
    FROM stock_scores
    WHERE stock_code = :stock_code
    ORDER BY created_at
    """), conn, params={"stock_code": stock_code})
    return history_df


def load_candidate_reasons(stock_code):
    with engine.connect() as conn:
        try:
            reasons_df = pd.read_sql(text("""
        SELECT trade_date, stock_code, reason
        FROM daily_candidates
        WHERE stock_code = :stock_code
        ORDER BY trade_date
        """), conn, params={"stock_code": str(stock_code).zfill(6)})
        except Exception:
            reasons_df = pd.DataFrame()
    return reasons_df


def prepare_score_data(dataframe):
    dataframe = dataframe.copy()
    dataframe["stock_name"] = dataframe["stock_code"].apply(get_stock_name)
    dataframe["industry"] = dataframe["stock_code"].apply(lambda code: get_stock_metadata(code)["industry"])
    dataframe["market_cap"] = dataframe["stock_code"].apply(lambda code: get_stock_metadata(code)["market_cap"])
    dataframe["display_name"] = dataframe["stock_code"] + " " + dataframe["stock_name"]
    dataframe["created_at"] = pd.to_datetime(dataframe["created_at"])
    return dataframe


def render_metrics(dataframe):
    latest_time = dataframe["created_at"].max().strftime("%Y-%m-%d %H:%M:%S")
    metric_cols = st.columns([1, 1, 1.8])
    metric_cols[0].metric("股票池数量", len(STOCK_POOL))
    metric_cols[1].metric("已评分股票", len(dataframe))
    metric_cols[2].markdown(
        f"""
        <div style="padding-top: 0.15rem;">
            <div style="font-size: 0.95rem; color: #555; margin-bottom: 0.35rem;">最近更新时间</div>
            <div style="font-size: 1.55rem; line-height: 1.2; white-space: nowrap;">{latest_time}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_market_scan_card():
    settings = get_current_user_settings()
    summary = get_latest_dynamic_pool_summary()
    scan_cols = st.columns(4)
    if summary is None:
        scan_cols[0].metric("扫描股票数", "--")
        scan_cols[1].metric("通过筛选数", "--")
        scan_cols[2].metric("最高评分", "--")
        scan_cols[3].metric("扫描完成时间", "尚未执行")
    else:
        scan_cols[0].metric("扫描股票数", summary.get("scanned_count", "--"))
        scan_cols[1].metric("通过筛选数", summary["selected_count"])
        max_score = summary["max_ai_score"]
        scan_cols[2].metric("最高评分", "--" if pd.isna(max_score) else f"{float(max_score):.1f}")
        scan_cols[3].metric("扫描完成时间", summary["completed_at"] or "--")

    with st.expander("今日全市场扫描参数", expanded=False):
        form_cols = st.columns([1, 1, 1, 1, 1, 1])
        min_amount = form_cols[0].number_input(
            "成交额门槛（亿元）",
            min_value=0.1,
            max_value=50.0,
            value=float(st.session_state.get("scan_min_amount_yi", settings["filter.min_amount_yi"])),
            step=0.5,
            key="scan_min_amount_yi",
        )
        volume_ratio = form_cols[1].number_input(
            "量比门槛",
            min_value=0.5,
            max_value=5.0,
            value=float(st.session_state.get("scan_volume_ratio", settings["filter.volume_ratio"])),
            step=0.1,
            key="scan_volume_ratio",
        )
        min_close = form_cols[2].number_input(
            "最低收盘价",
            min_value=0.1,
            max_value=100.0,
            value=float(st.session_state.get("scan_min_close", 3.0)),
            step=0.5,
            key="scan_min_close",
        )
        top_n = form_cols[3].number_input(
            "动态池数量",
            min_value=5,
            max_value=100,
            value=int(st.session_state.get("scan_top_n", settings["recommend.top_n"])),
            step=5,
            key="scan_top_n",
        )
        max_workers = form_cols[4].number_input(
            "并发数",
            min_value=1,
            max_value=20,
            value=int(st.session_state.get("scan_max_workers", 10)),
            step=1,
            key="scan_max_workers",
        )
        run_scan = form_cols[5].button("立即扫描", key="run_market_scan")

        if run_scan:
            if not settings["recommend.enable_market_scan"]:
                st.warning("你的个人设置已关闭全市场扫描")
                return
            config = DynamicPoolConfig(
                min_amount_yuan=float(min_amount) * 100000000,
                volume_ratio_threshold=float(volume_ratio),
                min_close=float(min_close),
                top_n=int(top_n),
                max_workers=int(max_workers),
                score_weights=current_score_weights(),
            )
            with st.spinner("正在执行全市场扫描与评分..."):
                result = run_dynamic_pool_scan(config=config)
            st.session_state["market_scan_result"] = result
            st.session_state["last_scan_count"] = result.get("scanned_count", "--")
            st.rerun()

    result = st.session_state.get("market_scan_result")
    if result:
        if result.get("ok"):
            st.success(
                f"{result.get('trade_date')} 扫描完成："
                f"静态通过 {result.get('static_pass_count')}，"
                f"行情通过 {result.get('daily_pass_count')}，"
                f"评分通过 {result.get('scored_count')}，"
                f"入选 {result.get('selected_count')}"
            )
        else:
            st.info(result.get("message", "全市场扫描已跳过"))


def render_news_sentiment_controls():
    st.subheader("新闻情绪分析")
    filter_cols = st.columns([1, 1.2, 2])
    selected_date = filter_cols[1].date_input(
        "查看日期",
        value=datetime.now().date(),
        key="news_sentiment_date_filter",
    )
    sort_desc = filter_cols[2].toggle("按情绪分数降序", value=True, key="news_sentiment_sort_desc")

    if filter_cols[0].button("立即分析今日新闻", key="run_news_sentiment_today"):
        with st.spinner("正在拉取免费新闻源并执行 AI 情绪分析..."):
            result = analyze_watchlist_news(force=True)
        st.session_state["news_sentiment_result"] = result

    result = st.session_state.get("news_sentiment_result")
    if result:
        st.markdown(
            f"""
            <div style="display:flex;gap:0.75rem;align-items:center;margin:0.25rem 0 0.75rem;">
                <span style="color:#166534;background:#dcfce7;border:1px solid #86efac;border-radius:999px;padding:0.18rem 0.62rem;font-weight:800;">新增 {int(result.get("saved", 0))}</span>
                <span style="color:#075985;background:#e0f2fe;border:1px solid #7dd3fc;border-radius:999px;padding:0.18rem 0.62rem;font-weight:800;">原始新闻 {int(result.get("raw_inserted", 0))}</span>
                <span style="color:#4b5563;background:#f3f4f6;border:1px solid #d1d5db;border-radius:999px;padding:0.18rem 0.62rem;font-weight:800;">跳过 {int(result.get("skipped", 0))}</span>
                <span style="color:white;background:{A_SHARE_UP_COLOR};border:1px solid {A_SHARE_UP_COLOR};border-radius:999px;padding:0.18rem 0.62rem;font-weight:800;">失败 {int(result.get("failed", 0))}</span>
                <span style="color:#64748b;">{escape(str(result.get("message", "")))}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    rows = load_news_sentiments_by_date(selected_date.strftime("%Y-%m-%d"))
    if not rows:
        st.info("当前日期暂无新闻情绪分析记录")
        return

    rows = sorted(
        rows,
        key=lambda item: float(item.get("final_sentiment_score", item.get("sentiment_score", 0)) or 0),
        reverse=sort_desc,
    )
    render_macro_sentiment_overview(rows)
    render_news_sentiment_table(rows)


def load_news_sentiments_by_date(trade_date):
    user_id = get_current_user_id()
    with engine.connect() as conn:
        try:
            rows_df = pd.read_sql(text("""
        SELECT
            s.*,
            COALESCE(w.stock_name, '') AS stock_name
        FROM stock_news_sentiment s
        LEFT JOIN watchlist w
            ON w.stock_code = s.stock_code
            AND w.user_id = :user_id
        WHERE s.trade_date = :trade_date
        ORDER BY s.final_sentiment_score DESC, s.stock_code
        """), conn, params={"trade_date": trade_date, "user_id": user_id})
        except Exception:
            rows_df = pd.DataFrame()
    if rows_df.empty:
        return []

    rows = rows_df.to_dict("records")
    import json
    for row in rows:
        try:
            row["news_titles"] = json.loads(row.get("news_titles") or "[]")
        except Exception:
            row["news_titles"] = []
        try:
            row["score_breakdown"] = json.loads(row.get("score_breakdown") or "{}")
        except Exception:
            row["score_breakdown"] = {}
    return rows


def sentiment_badge_html(label):
    styles = {
        "正面": f"background:{A_SHARE_UP_COLOR};color:white;border:1px solid {A_SHARE_UP_COLOR};",
        "负面": "background:#00AA00;color:white;border:1px solid #00AA00;",
        "中性": "background:#6b7280;color:white;border:1px solid #6b7280;",
        "无数据": "background:#f3f4f6;color:#6b7280;border:1px solid #d1d5db;",
    }
    style = styles.get(label, styles["无数据"])
    return (
        "<span style='display:inline-flex;align-items:center;justify-content:center;"
        f"min-width:3.6rem;padding:0.18rem 0.56rem;border-radius:999px;font-size:0.82rem;font-weight:800;{style}'>"
        f"{escape(str(label))}</span>"
    )


def sentiment_score_bar_html(score):
    score = 0 if pd.isna(score) else max(-1, min(1, float(score)))
    color = A_SHARE_UP_COLOR if score > 0 else "#00AA00" if score < 0 else A_SHARE_FLAT_COLOR
    width = abs(score) * 50
    if score > 0:
        fill_style = f"left:50%;width:{width}%;background:{color};"
    elif score < 0:
        fill_style = f"left:{50 - width}%;width:{width}%;background:{color};"
    else:
        fill_style = "left:50%;width:0%;background:#d1d5db;"
    return (
        "<div class='sent-score-wrap'>"
        "<div class='sent-score-value'>"
        f"{score:+.2f}</div>"
        "<div class='sent-score-track'>"
        f"<span style='position:absolute;top:0;bottom:0;{fill_style}'></span>"
        "<span style='position:absolute;left:50%;top:-0.1rem;bottom:-0.1rem;width:2px;background:#374151;opacity:0.55;'></span>"
        "</div>"
        "</div>"
    )


def macro_only_badge_html():
    return (
        "<span class='macro-only-badge'>仅宏观</span>"
    )


def macro_score_card_html(title, score, subtitle="", source_note=""):
    score = 0 if pd.isna(score) else max(-1, min(1, float(score)))
    label = "正面" if score > 0.3 else "负面" if score < -0.3 else "中性"
    color = sentiment_color(label)
    progress_value = (score + 1) * 50
    return f"""
    <div style="border:1px solid rgba(148,163,184,0.28);border-radius:8px;background:rgba(255,255,255,0.76);padding:0.85rem 0.95rem;box-shadow:0 2px 10px rgba(15,23,42,0.04);">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:0.75rem;">
            <div style="font-weight:800;color:#1f2937;">{escape(title)}</div>
            <div style="color:{color};font-weight:900;">{score:+.2f}</div>
        </div>
        <div style="height:0.6rem;border-radius:999px;background:linear-gradient(90deg,#00AA00 0%,#e5e7eb 50%,{A_SHARE_UP_COLOR} 100%);position:relative;margin:0.65rem 0 0.42rem;">
            <span style="position:absolute;left:{progress_value:.1f}%;top:50%;width:0.82rem;height:0.82rem;border-radius:50%;background:#111827;border:2px solid white;transform:translate(-50%,-50%);box-shadow:0 2px 8px rgba(15,23,42,0.24);"></span>
        </div>
        <div style="display:flex;justify-content:space-between;color:#64748b;font-size:0.78rem;">
            <span>负面</span><span>{escape(label)}</span><span>正面</span>
        </div>
        <div style="margin-top:0.35rem;color:#64748b;font-size:0.8rem;">{escape(subtitle)}</div>
        <div style="margin-top:0.18rem;color:#94a3b8;font-size:0.76rem;">{escape(source_note)}</div>
    </div>
    """


def raw_news_titles(source_type, limit=20):
    rows = list_news_raw(source_type, limit=limit)
    return [row.get("title", "") for row in rows if row.get("title")]


def render_macro_sentiment_overview(rows):
    if not rows:
        return
    first = rows[0]
    breakdown = first.get("score_breakdown") or {}
    macro_cn_score = float(first.get("macro_sentiment_cn", 0) or 0)
    macro_global_score = float(first.get("macro_sentiment_global", 0) or 0)
    market_score = float((breakdown.get("market") or {}).get("score", 0) or 0)
    final_scores = [
        float(row.get("final_sentiment_score", row.get("sentiment_score", 0)) or 0)
        for row in rows
    ]
    broad_score = sum(final_scores) / len(final_scores) if final_scores else market_score
    macro_count = int((breakdown.get("macro_cn") or {}).get("count", 0) or 0)
    market_count = int((breakdown.get("market") or {}).get("count", 0) or 0)
    global_count = int((breakdown.get("macro_global") or {}).get("count", 0) or 0)
    cards = [
        ("国内政策情绪", macro_cn_score, f"政策/大盘新闻 {macro_count + market_count} 条", "来源：东方财富RSS / 财联社", "macro_cn"),
        ("国际市场情绪", macro_global_score, f"国际宏观新闻 {global_count} 条", "来源：NewsAPI", "macro_global"),
        ("综合大盘情绪", broad_score, f"覆盖股票 {len(rows)} 只", "来源：新浪财经RSS", "individual"),
    ]
    cols = st.columns(3)
    for col, (title, score, subtitle, source_note, source_type) in zip(cols, cards):
        col.markdown(macro_score_card_html(title, score, subtitle, source_note), unsafe_allow_html=True)
        with col.expander("查看原始新闻", expanded=False):
            titles = raw_news_titles(source_type, limit=20)
            if not titles:
                st.caption("暂无原始新闻")
            for idx, news_title in enumerate(titles, 1):
                st.caption(f"{idx}. {news_title}")


def render_news_title_cell(row):
    titles = row.get("news_titles") or []
    if not titles:
        return "暂无"
    normalized_titles = []
    for item in titles:
        if isinstance(item, dict):
            title = item.get("title", "")
        else:
            title = str(item)
        if title:
            normalized_titles.append(title)
    if not normalized_titles:
        return "暂无"
    first = escape(normalized_titles[0])
    details = "".join(f"<li>{escape(title)}</li>" for title in normalized_titles)
    return (
        "<details class='news-title-details'>"
        f"<summary title='{first}'>{first} "
        f"<span style='color:#64748b;font-weight:500;'>({len(normalized_titles)}条)</span></summary>"
        f"<ul style='margin:0.45rem 0 0 1rem;padding:0;color:#334155;'>{details}</ul>"
        "</details>"
    )


def score_breakdown_html(row):
    breakdown = row.get("score_breakdown") or {}
    weights = breakdown.get("weights") or {}
    labels = [
        ("个股情绪", "stock"),
        ("行业情绪", "industry"),
        ("国内宏观", "macro_cn"),
        ("国际宏观", "macro_global"),
    ]
    lines = []
    for label, key in labels:
        item = breakdown.get(key) or {}
        score = float(item.get("score", 0) or 0)
        count = int(item.get("count", 0) or 0)
        weight = weights.get(key)
        weight_text = "" if weight is None else f" / 权重 {float(weight):.0%}"
        if key == "stock" and count == 0:
            score_text = "无数据（权重转移）"
        else:
            score_text = f"{score:+.2f}"
        lines.append(f"<li>{escape(label)}：{score_text}（{count}条{weight_text}）</li>")
    return (
        "<details style='cursor:pointer;'>"
        "<summary style='color:#2563eb;font-weight:700;'>查看明细</summary>"
        f"<ul style='margin:0.45rem 0 0 1rem;padding:0;color:#334155;font-size:0.84rem;line-height:1.45;'>{''.join(lines)}</ul>"
        "</details>"
    )


def render_news_sentiment_table(rows):
    header = """
    <div class="news-sent-table">
        <div class="news-sent-head">日期</div>
        <div class="news-sent-head">股票代码</div>
        <div class="news-sent-head">股票名称</div>
        <div class="news-sent-head">情绪</div>
        <div class="news-sent-head">个股情绪</div>
        <div class="news-sent-head">综合情绪</div>
        <div class="news-sent-head">得分明细</div>
        <div class="news-sent-head">新闻数</div>
        <div class="news-sent-head">新闻标题</div>
        <div class="news-sent-head">分析原因</div>
    """
    body = []
    for row in rows:
        stock_name = row.get("stock_name") or get_stock_name(row.get("stock_code", ""))
        trade_date = str(row.get("trade_date", ""))
        short_date = trade_date[5:] if len(trade_date) >= 10 else trade_date
        news_count = int(row.get("news_count", 0) or 0)
        final_score_html = (
            macro_only_badge_html()
            if news_count == 0
            else sentiment_score_bar_html(row.get("final_sentiment_score", row.get("sentiment_score", 0)))
        )
        reason = str(row.get("analysis_reason", "") or "")
        if news_count == 0:
            reason = "当日无个股新闻，综合情绪仅反映宏观市场环境"
        body.append(
            "<div class='news-sent-cell date' title='{full_date}'>{date}</div>"
            "<div class='news-sent-cell code'>{code}</div>"
            "<div class='news-sent-cell name'>{name}</div>"
            "<div class='news-sent-cell badge-cell'>{badge}</div>"
            "<div class='news-sent-cell'>{stock_bar}</div>"
            "<div class='news-sent-cell'>{final_bar}</div>"
            "<div class='news-sent-cell'>{breakdown}</div>"
            "<div class='news-sent-cell count'>{count}</div>"
            "<div class='news-sent-cell titles'>{titles}</div>"
            "<div class='news-sent-cell reason' title='{reason_title}'>{reason}</div>".format(
                full_date=escape(trade_date),
                date=escape(short_date),
                code=escape(str(row.get("stock_code", "")).zfill(6)),
                name=escape(str(stock_name)),
                badge=sentiment_badge_html(row.get("sentiment", "无数据")),
                stock_bar=sentiment_score_bar_html(row.get("sentiment_score", 0)),
                final_bar=final_score_html,
                breakdown=score_breakdown_html(row),
                count=news_count,
                titles=render_news_title_cell(row),
                reason_title=escape(reason),
                reason=escape(reason),
            )
        )
    footer = "</div>"
    st.markdown(
        """
        <style>
        .news-sent-table {
            display:grid;
            grid-template-columns: 4.6rem 5.2rem 5.6rem 5.2rem 9.4rem 9.4rem 6.2rem 3.2rem minmax(16rem, 1.4fr) minmax(16rem, 1.2fr);
            border:1px solid rgba(148,163,184,0.28);
            border-radius:8px;
            overflow:auto;
            background:rgba(255,255,255,0.78);
        }
        .news-sent-head {
            padding:0.62rem 0.7rem;
            background:#f8fafc;
            color:#334155;
            font-weight:800;
            border-bottom:1px solid rgba(148,163,184,0.26);
        }
        .news-sent-cell {
            padding:0.62rem 0.7rem;
            border-bottom:1px solid rgba(148,163,184,0.16);
            color:#1f2937;
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        .news-sent-cell.date,
        .news-sent-cell.code,
        .news-sent-cell.name {
            white-space:nowrap;
        }
        .news-sent-cell.code { font-family:Consolas, monospace; color:#475569; }
        .news-sent-cell.badge-cell,
        .news-sent-cell.count {
            text-align:center;
        }
        .news-sent-cell.titles, .news-sent-cell.reason {
            font-size:0.88rem;
            line-height:1.45;
            white-space:nowrap;
        }
        .news-title-details summary {
            color:#2563eb;
            font-weight:700;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
            list-style-position:inside;
        }
        .sent-score-wrap {
            display:flex;
            flex-direction:column;
            gap:0.2rem;
            width:8.2rem;
        }
        .sent-score-value {
            text-align:right;
            font-size:0.82rem;
            color:#475569;
            font-weight:700;
            line-height:1;
        }
        .sent-score-track {
            position:relative;
            width:8.2rem;
            height:0.7rem;
            border-radius:999px;
            background:#f3f4f6;
            border:1px solid #e5e7eb;
            overflow:hidden;
        }
        .macro-only-badge {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            min-width:4rem;
            padding:0.18rem 0.5rem;
            border-radius:999px;
            color:#64748b;
            background:#f1f5f9;
            border:1px solid #cbd5e1;
            font-weight:800;
            font-size:0.78rem;
        }
        </style>
        """ + header + "".join(body) + footer,
        unsafe_allow_html=True,
    )


def render_stock_pool_overview(pool):
    st.subheader("股票池筛选")

    pool_df = pd.DataFrame(pool)
    if pool_df.empty:
        st.info("暂无股票池数据")
        return

    filtered_pool = filter_stock_pool(pool)
    filtered_df = pd.DataFrame(filtered_pool)
    metric_cols = st.columns(3)
    metric_cols[0].metric("当前股票池", len(pool_df))
    metric_cols[1].metric("筛选后数量", len(filtered_df))
    metric_cols[2].metric("行业数量", filtered_df["industry"].nunique() if not filtered_df.empty else 0)

    if filtered_df.empty:
        st.info("当前筛选条件下暂无股票")
        return

    if "source" not in filtered_df.columns:
        filtered_df["source"] = "扫描"
    view = filtered_df[["code", "name", "industry", "market_cap", "source"]].rename(columns={
        "code": "股票代码",
        "name": "股票名称",
        "industry": "行业",
        "market_cap": "市值",
        "source": "来源",
    })
    st.dataframe(view, use_container_width=True, hide_index=True)


def render_pool_filters():
    industries = sorted({item.get("industry", "未分类") for item in STOCK_POOL})
    market_caps = sorted({item.get("market_cap", "未分类") for item in STOCK_POOL})
    selected_industries = st.sidebar.multiselect(
        "行业筛选",
        industries,
        default=industries,
    )
    selected_market_caps = st.sidebar.multiselect(
        "市值筛选",
        market_caps,
        default=market_caps,
    )
    return selected_industries, selected_market_caps


def filter_stock_pool(pool):
    selected_industries = st.session_state.get("selected_industries", [])
    selected_market_caps = st.session_state.get("selected_market_caps", [])
    if not selected_industries:
        selected_industries = sorted({item.get("industry", "未分类") for item in pool})
    if not selected_market_caps:
        selected_market_caps = sorted({item.get("market_cap", "未分类") for item in pool})
    return [
        item
        for item in pool
        if item.get("industry", "未分类") in selected_industries
        and item.get("market_cap", "未分类") in selected_market_caps
    ]


def apply_score_filters(dataframe, filtered_pool):
    filtered_codes = {item["code"] for item in filtered_pool}
    if not filtered_codes:
        return dataframe.iloc[0:0].copy()
    return dataframe[dataframe["stock_code"].isin(filtered_codes)].copy()


def render_watchlist_manager():
    st.subheader("自选股管理")
    user_id = get_current_user_id()

    if "watchlist_pipeline_message" in st.session_state:
        message = st.session_state.pop("watchlist_pipeline_message")
        if message["ok"]:
            st.success(message["text"])
        else:
            st.warning(message["text"])

    watchlist_rows = list_watchlist(user_id=user_id)
    if "watchlist_detail_code" not in st.session_state:
        st.session_state["watchlist_detail_code"] = None
    if "watchlist_delete_code" not in st.session_state:
        st.session_state["watchlist_delete_code"] = None

    stock_lookup = {
        f"{item['code']} {item['name']}": item
        for item in STOCK_POOL
    }
    with st.form("add_watchlist_stock", clear_on_submit=True):
        form_cols = st.columns([1, 1.4, 1, 1])
        search_text = form_cols[0].text_input("搜索", placeholder="代码/名称，如 601991")
        filtered_options = [
            option
            for option in stock_lookup
            if not search_text.strip()
            or search_text.strip().lower() in option.lower()
        ]
        selected_option = form_cols[1].selectbox(
            "添加股票",
            options=[""] + filtered_options,
            format_func=lambda value: "可选：从默认池选择" if value == "" else value,
        )
        selected_item = stock_lookup.get(selected_option)
        note = form_cols[2].text_input("备注", placeholder="可选")
        submitted = form_cols[3].form_submit_button("加入自选股")

        if submitted:
            manual_code = normalize_stock_code(search_text) if search_text.strip() else ""
            if selected_item:
                stock_code = selected_item["code"]
                stock_name = selected_item["name"]
            elif manual_code.strip("0"):
                quote = get_spot_stock_quote(manual_code)
                if not quote["found"] and spot_market_available():
                    st.warning("未找到该股票")
                    return
                if not quote["found"]:
                    recent_df = get_recent_price_data(manual_code)
                    if recent_df.empty:
                        st.warning("未找到该股票")
                        return
                stock_code = manual_code
                stock_name = quote["name"] or get_stock_name(stock_code)
            else:
                st.warning("请输入股票代码或从默认池选择一只股票")
                return

            upsert_watchlist_stock(
                stock_code,
                stock_name,
                note=note.strip() or None,
                user_id=user_id,
            )
            get_recent_price_data.clear()
            get_spot_market_data.clear()
            get_spot_stock_quote(stock_code)
            get_recent_price_data(stock_code)
            with st.spinner("正在采集行情并生成评分..."):
                result = process_watchlist_stock(
                    stock_code,
                    stock_name,
                    score_weights=current_score_weights(),
                )
            st.session_state["watchlist_pipeline_message"] = {
                "ok": result["ok"],
                "text": result["message"],
            }
            st.rerun()

    watchlist_df = pd.DataFrame(list_watchlist(user_id=user_id))
    if watchlist_df.empty:
        st.info("暂无自选股")
        return

    status_filter = st.radio(
        "状态筛选",
        ["全部", "启用中", "已停用"],
        horizontal=True,
        key="watchlist_status_filter",
    )
    if status_filter == "启用中":
        watchlist_df = watchlist_df[watchlist_df["enabled"] == 1]
    elif status_filter == "已停用":
        watchlist_df = watchlist_df[watchlist_df["enabled"] == 0]

    if watchlist_df.empty:
        st.info("当前筛选条件下暂无自选股")
        return

    css = """
    <style>
    .watch-card {
        border: 1px solid rgba(148, 163, 184, 0.28);
        border-radius: 8px;
        padding: 0.95rem;
        min-height: 255px;
        background: rgba(255,255,255,0.82);
        box-shadow: 0 8px 18px rgba(15, 23, 42, 0.06);
        transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }
    .watch-card:hover {
        border-color: rgba(37, 99, 235, 0.48);
        box-shadow: 0 12px 26px rgba(15, 23, 42, 0.10);
    }
    .watch-card-disabled {
        opacity: 0.56;
        filter: grayscale(0.25);
    }
    .watch-title { font-size: 1.18rem; font-weight: 800; color: #1f2937; line-height: 1.2; }
    .watch-code { font-size: 0.82rem; color: #64748b; margin-top: 0.16rem; }
    .watch-row { display:flex; align-items:baseline; justify-content:space-between; gap:0.5rem; margin-top:0.7rem; }
    .watch-price { font-size: 1.25rem; font-weight: 800; color:#111827; }
    .watch-note { min-height: 2.2rem; color:#64748b; font-size:0.86rem; margin-top:0.65rem; }
    .watch-badge { display:inline-flex; align-items:center; padding:0.16rem 0.5rem; border-radius:999px; font-size:0.78rem; font-weight:700; }
    .watch-badge-on { background:#dcfce7; color:#166534; border:1px solid #86efac; }
    .watch-badge-off { background:#f3f4f6; color:#4b5563; border:1px solid #d1d5db; }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)

    rows = watchlist_df.to_dict("records")
    for row_start in range(0, len(rows), 4):
        cols = st.columns(4)
        for col, item in zip(cols, rows[row_start:row_start + 4]):
            stock_code = str(item["stock_code"]).zfill(6)
            stock_name = item["stock_name"]
            enabled = int(item.get("enabled", 0)) == 1
            _, current_price, pct_change, _ = get_price_summary(stock_code)
            card_classes = "watch-card" + ("" if enabled else " watch-card-disabled")
            badge_class = "watch-badge-on" if enabled else "watch-badge-off"
            badge_text = "启用" if enabled else "停用"
            raw_note = item.get("note")
            note = "" if pd.isna(raw_note) or raw_note is None else str(raw_note)

            with col:
                st.markdown(
                    f"""
                    <div class="{card_classes}">
                        <div class="watch-title">{escape(str(stock_name))}</div>
                        <div class="watch-code">{stock_code}</div>
                        <div class="watch-row">
                            <span class="watch-price">{format_price(current_price)}</span>
                            {format_colored_number(pct_change, suffix="%")}
                        </div>
                        <div style="margin-top:0.55rem;">
                            <span class="watch-badge {badge_class}">{badge_text}</span>
                        </div>
                        <div class="watch-note">{escape(str(note))}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                render_plotly_sparkline(col, stock_code, days=10)
                action_cols = st.columns(3)
                if action_cols[0].button("查看详情", key=f"watch_detail_{stock_code}"):
                    st.session_state["watchlist_detail_code"] = stock_code
                    st.session_state["selected_stock_code"] = stock_code
                toggle_label = "停用" if enabled else "启用"
                if action_cols[1].button(toggle_label, key=f"watch_toggle_{stock_code}"):
                    set_watchlist_enabled(stock_code, 0 if enabled else 1, user_id=user_id)
                    st.rerun()
                if action_cols[2].button("删除", key=f"watch_delete_{stock_code}"):
                    st.session_state["watchlist_delete_code"] = stock_code

                if st.session_state.get("watchlist_delete_code") == stock_code:
                    st.warning(f"确认删除 {stock_code} {stock_name}？")
                    confirm_cols = st.columns(2)
                    if confirm_cols[0].button("确认删除", key=f"watch_confirm_delete_{stock_code}"):
                        delete_watchlist_stock(stock_code, user_id=user_id)
                        st.session_state["watchlist_delete_code"] = None
                        if st.session_state.get("watchlist_detail_code") == stock_code:
                            st.session_state["watchlist_detail_code"] = None
                        st.rerun()
                    if confirm_cols[1].button("取消", key=f"watch_cancel_delete_{stock_code}"):
                        st.session_state["watchlist_delete_code"] = None
                        st.rerun()

    detail_code = st.session_state.get("watchlist_detail_code")
    if detail_code:
        detail_name = get_stock_name(detail_code)
        matched_detail = watchlist_df[watchlist_df["stock_code"].astype(str).str.zfill(6) == str(detail_code).zfill(6)]
        if not matched_detail.empty:
            detail_name = matched_detail.iloc[0]["stock_name"]
        st.divider()
        st.subheader(f"自选股详情：{detail_code} {detail_name}")
        history_df = load_history_data(detail_code)
        if not history_df.empty:
            history_df["created_at"] = pd.to_datetime(history_df["created_at"])
            history_df = history_df.sort_values("created_at")
        render_detail_trend_chart(detail_code, history_df)

        indicators = get_watchlist_indicators(detail_code)
        indicator_cols = st.columns(6)
        for idx, label in enumerate(["MA5", "MA10", "MA20", "成交量", "PE", "PB"]):
            value = indicators.get(label)
            if pd.isna(value):
                display_value = "--"
            elif label == "成交量":
                display_value = f"{float(value):,.0f}"
            else:
                display_value = f"{float(value):.2f}"
            indicator_cols[idx].metric(label, display_value)

        render_stock_score_history_table(history_df, detail_code)


def render_top5(top5):
    st.subheader(f"今日Top{len(top5)}推荐")
    user_id = get_current_user_id()
    sources = stock_source_map()
    header_cols = st.columns([1, 1.35, 0.75, 1.15, 0.65, 0.95, 1, 0.8, 0.85, 1.25, 0.9, 0.9])
    header_cols[0].markdown("**股票代码**")
    header_cols[1].markdown("**股票名称**")
    header_cols[2].markdown("**来源**")
    header_cols[3].markdown("**板块**")
    header_cols[4].markdown("**评分**")
    header_cols[5].markdown("**等级**")
    header_cols[6].markdown("**均线状态**")
    header_cols[7].markdown("**当前价**")
    header_cols[8].markdown("**今日涨跌幅**")
    header_cols[9].markdown("**近20日走势**")
    header_cols[10].markdown("**加入自选**")
    header_cols[11].markdown("**查看详情**")

    for _, row in top5.iterrows():
        stock_code = str(row["stock_code"]).zfill(6)
        stock_name = str(row["stock_name"])
        metadata = get_stock_metadata(stock_code)
        spark_df, current_price, pct_change, trend_return = get_price_summary(stock_code)
        row_cols = st.columns([1, 1.35, 0.75, 1.15, 0.65, 0.95, 1, 0.8, 0.85, 1.25, 0.9, 0.9])
        row_cols[0].write(stock_code)
        row_cols[1].write(stock_name)
        row_cols[2].markdown(source_badge_html(sources.get(stock_code, "扫描")), unsafe_allow_html=True)
        row_cols[3].markdown(industry_badge_html(metadata["industry"], metadata["market_cap"]), unsafe_allow_html=True)
        row_cols[4].write(row["score"])
        row_cols[5].markdown(level_badge_html(row["level"]), unsafe_allow_html=True)
        row_cols[6].markdown(ma_status_badge_html(stock_code), unsafe_allow_html=True)
        row_cols[7].write(format_price(current_price))
        render_pct_change(row_cols[8], pct_change)
        render_sparkline(row_cols[9], spark_df, trend_return)

        if row_cols[10].button("加入自选", key=f"top5_add_{stock_code}"):
            upsert_watchlist_stock(
                stock_code,
                stock_name,
                source="top5",
                note="来自今日Top5推荐",
                user_id=user_id,
            )
            st.success(f"{stock_code} {stock_name} 已加入自选股")

        row_cols[11].button(
            "查看详情",
            key=f"top5_detail_{stock_code}",
            on_click=open_stock_detail,
            args=(stock_code,),
        )


def render_candidate_pool():
    st.subheader("动态扫描池")

    dynamic_rows = list_dynamic_pool(limit=100)
    if dynamic_rows:
        dynamic_df = pd.DataFrame(dynamic_rows)
        dynamic_df["stock_code"] = dynamic_df["ts_code"].astype(str).str.split(".", n=1).str[0].str.zfill(6)
        dynamic_df["amount_yi"] = pd.to_numeric(dynamic_df["amount"], errors="coerce") / 100000
        view = dynamic_df[[
            "trade_date",
            "stock_code",
            "stock_name",
            "close",
            "pct_chg",
            "amount_yi",
            "volume_ratio",
            "ma20",
            "ai_score",
            "ai_grade",
            "filter_pass_reason",
        ]].rename(columns={
            "trade_date": "交易日期",
            "stock_code": "股票代码",
            "stock_name": "股票名称",
            "close": "收盘价",
            "pct_chg": "涨跌幅",
            "amount_yi": "成交额(亿)",
            "volume_ratio": "量比",
            "ma20": "MA20",
            "ai_score": "AI评分",
            "ai_grade": "等级",
            "filter_pass_reason": "筛选原因",
        })
        st.dataframe(view, use_container_width=True, hide_index=True)
    else:
        st.info("暂无全市场动态扫描结果，可在首页执行扫描或等待 17:30 定时任务。")

    st.subheader("AI每日推荐池")

    candidate_rows = list_latest_candidates(enabled_only=False)
    if candidate_rows:
        candidate_df = pd.DataFrame(candidate_rows)
        candidate_df = enrich_level_from_score(candidate_df)
        candidate_df = map_candidate_status(candidate_df)
        candidate_df["industry"] = candidate_df["stock_code"].apply(lambda code: get_stock_metadata(code)["industry"])
        candidate_df["market_cap"] = candidate_df["stock_code"].apply(lambda code: get_stock_metadata(code)["market_cap"])
        header_cols = st.columns([0.9, 0.9, 1.15, 1, 0.65, 0.9, 1, 0.75, 0.85, 1.2, 2, 0.7, 0.7])
        for col, label in zip(
            header_cols,
            [
                "交易日期",
                "股票代码",
                "股票名称",
                "板块",
                "评分",
                "等级",
                "均线状态",
                "当前价",
                "今日涨跌幅",
                "近20日走势",
                "推荐理由",
                "来源",
                "状态",
            ],
        ):
            col.markdown(f"**{label}**")

        for _, row in candidate_df.iterrows():
            stock_code = str(row["stock_code"]).zfill(6)
            spark_df, current_price, pct_change, trend_return = get_price_summary(stock_code)
            row_cols = st.columns([0.9, 0.9, 1.15, 1, 0.65, 0.9, 1, 0.75, 0.85, 1.2, 2, 0.7, 0.7])
            row_cols[0].write(row["trade_date"])
            row_cols[1].write(stock_code)
            row_cols[2].write(row["stock_name"])
            row_cols[3].markdown(
                industry_badge_html(row["industry"], row["market_cap"]),
                unsafe_allow_html=True,
            )
            row_cols[4].write(row["score"])
            row_cols[5].markdown(level_badge_html(row["level"]), unsafe_allow_html=True)
            row_cols[6].markdown(ma_status_badge_html(stock_code), unsafe_allow_html=True)
            row_cols[7].write(format_price(current_price))
            render_pct_change(row_cols[8], pct_change)
            render_sparkline(row_cols[9], spark_df, trend_return)
            row_cols[10].write(row["reason"])
            row_cols[11].write(row["source"])
            row_cols[12].write(row["enabled"])
    else:
        st.info("暂无AI每日推荐池数据，请运行 python report/daily_candidates.py")


def render_score_chart(dataframe):
    st.subheader("股票评分图")

    min_chart_count = min(5, len(dataframe))
    top_n = st.slider(
        "显示股票数量",
        min_value=min_chart_count,
        max_value=len(dataframe),
        value=min(10, len(dataframe)),
    )

    chart_data = dataframe.head(top_n)[[
        "display_name",
        "score",
    ]].copy()
    chart_data["score_band"] = chart_data["score"].apply(score_band)

    chart = (
        alt.Chart(chart_data)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X(
                "display_name:N",
                title="股票",
                sort=None,
                axis=alt.Axis(labelAngle=-35),
            ),
            y=alt.Y("score:Q", title="评分", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color(
                "score_band:N",
                title="评分区间",
                scale=alt.Scale(
                    domain=["强烈关注（≥70）", "观察（40-69）", "回避（<40）"],
                    range=["#16a34a", "#f59e0b", "#dc2626"],
                ),
            ),
            tooltip=[
                alt.Tooltip("display_name:N", title="股票"),
                alt.Tooltip("score:Q", title="评分"),
                alt.Tooltip("score_band:N", title="评分区间"),
            ],
        )
        .properties(height=420)
    )

    st.altair_chart(chart, use_container_width=True)


def render_stock_summary(stock_code, latest_row=None):
    stock_code = str(stock_code).zfill(6)
    stock_name = get_stock_name(stock_code)
    metadata = get_stock_metadata(stock_code)
    _, current_price, pct_change, _ = get_price_summary(stock_code)

    st.subheader("股票概览")
    summary_cols = st.columns([1.2, 1, 1, 1.2, 1])
    summary_cols[0].metric("股票名称", stock_name)
    summary_cols[1].metric("当前价格", format_price(current_price))
    summary_cols[2].markdown("**今日涨跌幅**")
    summary_cols[2].markdown(format_colored_number(pct_change, suffix="%"), unsafe_allow_html=True)
    summary_cols[3].markdown(
        f"<div style='padding-top:1.8rem;'>{industry_badge_html(metadata['industry'], metadata['market_cap'])}</div>",
        unsafe_allow_html=True,
    )
    if latest_row is not None and not latest_row.empty:
        summary_cols[4].metric("最新评分", f"{float(latest_row['score']):.1f}")


def render_news_sentiment_detail(stock_code):
    st.subheader("新闻情绪")
    today = datetime.now().strftime("%Y-%m-%d")
    sentiment = get_news_sentiment(stock_code, today)
    if sentiment is None:
        recent_rows = list_news_sentiments(stock_code, limit=1)
        sentiment = recent_rows[-1] if recent_rows else None

    if sentiment is None:
        st.info("暂无该股票的新闻情绪记录，可在首页点击“立即分析今日新闻”。")
        return

    label = sentiment.get("sentiment", "无数据")
    score = float(sentiment.get("final_sentiment_score", sentiment.get("sentiment_score", 0)) or 0)
    news_count = int(sentiment.get("news_count", 0) or 0)
    color = sentiment_color(label)
    progress_value = int(((max(-1, min(1, score)) + 1) / 2) * 100)

    overview_cols = st.columns([1.1, 2, 1])
    overview_cols[0].markdown(
        f"""
        <div style="padding:0.95rem 1rem;border:1px solid rgba(148,163,184,0.28);border-radius:8px;background:rgba(255,255,255,0.72);">
            <div style="font-size:0.88rem;color:#64748b;margin-bottom:0.3rem;">今日情绪</div>
            <div style="font-size:2rem;font-weight:800;color:{color};line-height:1.1;">{escape(label)}</div>
            <div style="font-size:0.86rem;color:#64748b;margin-top:0.35rem;">日期：{escape(str(sentiment.get("trade_date", today)))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    overview_cols[1].markdown(
        f"""
        <div style="padding:0.95rem 1rem;border:1px solid rgba(148,163,184,0.28);border-radius:8px;background:rgba(255,255,255,0.72);">
            <div style="display:flex;justify-content:space-between;font-size:0.88rem;color:#64748b;margin-bottom:0.55rem;">
                <span>情绪分数</span><strong>{score:.2f}</strong>
            </div>
            <div style="height:12px;border-radius:999px;background:linear-gradient(90deg,#00AA00 0%,#e5e7eb 50%,#E84B4B 100%);position:relative;">
                <span style="position:absolute;left:{progress_value}%;top:50%;width:16px;height:16px;border-radius:50%;background:#111827;border:2px solid white;transform:translate(-50%,-50%);box-shadow:0 2px 8px rgba(15,23,42,0.28);"></span>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:0.78rem;color:#64748b;margin-top:0.42rem;">
                <span>-1 负面</span><span>0 中性</span><span>1 正面</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    overview_cols[2].metric("参考新闻条数", news_count)

    titles = sentiment.get("news_titles") or []
    reason = sentiment.get("analysis_reason") or "暂无分析原因"
    st.markdown("**当日新闻标题**")
    if not titles:
        st.caption("当日未匹配到相关新闻标题")
    for idx, item in enumerate(titles, 1):
        if isinstance(item, dict):
            title = item.get("title", "")
            url = item.get("url") or item.get("link")
        else:
            title = str(item)
            url = None
        with st.expander(f"{sentiment_icon(label)} {idx}. {title}", expanded=False):
            if url:
                st.markdown(f"[查看原文]({url})")
            else:
                st.caption("Tushare 当前新闻结果未提供原文链接")
            st.markdown(f"**AI分析原因：** {escape(str(reason))}")


def render_single_stock_score_chart(history_df):
    st.subheader("股票评分图")

    if history_df.empty:
        st.info("暂无该股票的评分数据")
        return

    chart_data = history_df.copy()
    chart_data["score_date"] = pd.to_datetime(chart_data["created_at"]).dt.normalize()
    chart_data = chart_data.drop_duplicates(subset=["score_date"], keep="last")
    chart_data["score_band"] = chart_data["score"].apply(score_band)
    if len(chart_data) < 2:
        st.caption("当前股票只有 1 个评分日期，因此图中只有一个柱/点。")

    chart = (
        alt.Chart(chart_data)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X("score_date:T", title="日期"),
            y=alt.Y("score:Q", title="评分", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color(
                "score_band:N",
                title="评分区间",
                scale=alt.Scale(
                    domain=["强烈关注（≥70）", "观察（40-69）", "回避（<40）"],
                    range=["#16a34a", "#f59e0b", "#dc2626"],
                ),
            ),
            tooltip=[
                alt.Tooltip("score_date:T", title="日期"),
                alt.Tooltip("score:Q", title="评分"),
                alt.Tooltip("level:N", title="等级"),
            ],
        )
        .properties(height=280)
    )
    st.altair_chart(chart, use_container_width=True)


def render_stock_score_history_table(history_df, stock_code):
    st.subheader("历史评分明细")

    if history_df.empty:
        st.info("暂无该股票的历史评分记录")
        return

    detail_df = history_df.copy()
    detail_df["score_date"] = pd.to_datetime(detail_df["created_at"]).dt.strftime("%Y-%m-%d")
    detail_df = detail_df.drop_duplicates(subset=["score_date"], keep="last")

    reasons_df = load_candidate_reasons(stock_code)
    if not reasons_df.empty:
        reasons_df = reasons_df.copy()
        reasons_df["score_date"] = pd.to_datetime(reasons_df["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        detail_df = detail_df.merge(
            reasons_df[["score_date", "reason"]],
            on="score_date",
            how="left",
        )
    else:
        detail_df["reason"] = ""

    detail_df["reason"] = detail_df["reason"].fillna("")
    view = detail_df[["score_date", "score", "level", "reason"]].tail(30).rename(columns={
        "score_date": "日期",
        "score": "评分",
        "level": "等级",
        "reason": "推荐理由",
    })
    st.dataframe(view, use_container_width=True, hide_index=True)


def render_history_trend(stock_code):
    history_df = load_history_data(stock_code)
    if history_df.empty:
        st.info("暂无该股票的历史评分记录")
    else:
        history_df["stock_name"] = history_df["stock_code"].apply(get_stock_name)
        history_df["created_at"] = pd.to_datetime(history_df["created_at"])
        history_df["score_date"] = history_df["created_at"].dt.date
        history_df = history_df.drop_duplicates(subset=["score_date"], keep="last")
        selected_name = history_df["stock_name"].iloc[-1]
        st.caption(f"{stock_code} {selected_name}")
        render_detail_trend_chart(stock_code, history_df)

def render_stock_detail_page(stock_code):
    stock_code = str(stock_code).zfill(6)
    history_df = load_history_data(stock_code)
    if not history_df.empty:
        history_df["stock_name"] = history_df["stock_code"].apply(get_stock_name)
        history_df["created_at"] = pd.to_datetime(history_df["created_at"])
        history_df = history_df.sort_values("created_at")
        latest_row = history_df.iloc[-1]
    else:
        latest_row = None

    render_stock_summary(stock_code, latest_row)
    render_single_stock_score_chart(history_df)
    render_news_sentiment_detail(stock_code)
    render_history_trend(stock_code)
    render_stock_score_history_table(history_df, stock_code)


def render_score_validation(validation_df):
    st.subheader("评分验证")

    if validation_df.empty:
        st.info("暂无评分验证数据，请运行 python report/score_validation.py")
    else:
        return_cols = ["return_1d", "return_3d", "return_5d", "return_10d"]
        validation_summary = build_validation_summary(validation_df, return_cols)
        validation_summary_view = validation_summary.copy()
        validation_summary_view["win_rate"] = validation_summary_view["win_rate"].apply(format_ratio)
        validation_summary_view["avg_return"] = validation_summary_view["avg_return"].apply(format_colored_return)
        validation_summary_view["max_drawdown"] = validation_summary_view["max_drawdown"].apply(format_colored_return)
        validation_summary_view = validation_summary_view.rename(columns={
            "horizon": "验证周期",
            "sample_count": "样本数",
            "win_rate": "胜率",
            "avg_return": "平均收益",
            "max_drawdown": "最大回撤",
        })
        st.markdown(validation_summary_view.to_html(escape=False, index=False), unsafe_allow_html=True)

        summary_df = validation_df.groupby("level")[return_cols].mean().reset_index()
        summary_df = summary_df.copy()
        for col in return_cols:
            summary_df[col] = summary_df[col].apply(format_colored_return)
        st.caption("按等级统计平均收益")
        st.markdown(summary_df.rename(columns=COLUMN_LABELS).to_html(escape=False, index=False), unsafe_allow_html=True)

        validation_view = validation_df[[
            "stock_code",
            "score",
            "level",
            "score_date",
            "return_1d",
            "return_3d",
            "return_5d",
            "return_10d",
            "evaluated_at",
        ]].head(30)
        validation_view = validation_view.copy()
        for col in return_cols:
            validation_view[col] = validation_view[col].apply(format_colored_return)
        st.markdown(display_table(validation_view).to_html(escape=False, index=False), unsafe_allow_html=True)


def render_personal_settings_page():
    st.subheader("个人设置")
    user_id = get_current_user_id()
    user = current_user()
    settings = get_current_user_settings()

    st.markdown("### 账号信息")
    account_cols = st.columns(4)
    account_cols[0].metric("用户名", user["username"])
    account_cols[1].metric("昵称", user["display_name"])
    account_cols[2].metric("角色", user["role"])
    account_cols[3].metric("最后登录", user.get("last_login_at") or "--")

    with st.form("user_settings_form"):
        st.markdown("### 策略参数")
        score_cols = st.columns(3)
        technical_weight = score_cols[0].number_input(
            "技术面权重",
            min_value=0.0,
            max_value=1.0,
            value=float(settings["score.technical_weight"]),
            step=0.05,
            format="%.2f",
        )
        fundamental_weight = score_cols[1].number_input(
            "基本面权重",
            min_value=0.0,
            max_value=1.0,
            value=float(settings["score.fundamental_weight"]),
            step=0.05,
            format="%.2f",
        )
        sentiment_weight = score_cols[2].number_input(
            "情绪面权重",
            min_value=0.0,
            max_value=1.0,
            value=float(settings["score.sentiment_weight"]),
            step=0.05,
            format="%.2f",
        )

        filter_cols = st.columns(3)
        min_amount_yi = filter_cols[0].number_input(
            "成交额门槛（亿元）",
            min_value=0.1,
            max_value=50.0,
            value=float(settings["filter.min_amount_yi"]),
            step=0.5,
        )
        volume_ratio = filter_cols[1].number_input(
            "量比门槛",
            min_value=0.5,
            max_value=5.0,
            value=float(settings["filter.volume_ratio"]),
            step=0.1,
        )
        ma_condition = filter_cols[2].selectbox(
            "均线条件",
            ["站上MA20", "MA5>MA20", "不限制"],
            index=["站上MA20", "MA5>MA20", "不限制"].index(settings["filter.ma_condition"])
            if settings["filter.ma_condition"] in ["站上MA20", "MA5>MA20", "不限制"] else 0,
        )

        st.markdown("### 推荐配置")
        rec_cols = st.columns(3)
        top_n = rec_cols[0].selectbox(
            "每日Top推荐数量",
            [5, 10, 20],
            index=[5, 10, 20].index(int(settings["recommend.top_n"]))
            if int(settings["recommend.top_n"]) in [5, 10, 20] else 0,
        )
        include_avoid = rec_cols[1].checkbox(
            "接收回避级别股票推荐",
            value=bool(settings["recommend.include_avoid"]),
        )
        enable_market_scan = rec_cols[2].checkbox(
            "开启全市场扫描",
            value=bool(settings["recommend.enable_market_scan"]),
        )

        st.markdown("### 通知设置")
        notify_cols = st.columns(2)
        email_enabled = notify_cols[0].checkbox(
            "邮件通知",
            value=bool(settings["notify.email_enabled"]),
        )
        score_change_threshold = notify_cols[1].number_input(
            "评分变化通知阈值",
            min_value=1,
            max_value=100,
            value=int(settings["notify.score_change_threshold"]),
            step=1,
        )

        action_cols = st.columns([1, 1, 4])
        submitted = action_cols[0].form_submit_button("保存设置")
        reset = action_cols[1].form_submit_button("恢复默认")

    if submitted:
        if technical_weight + fundamental_weight + sentiment_weight <= 0:
            st.error("三个评分权重之和必须大于 0")
            return
        save_user_settings(user_id, {
            "score.technical_weight": technical_weight,
            "score.fundamental_weight": fundamental_weight,
            "score.sentiment_weight": sentiment_weight,
            "filter.min_amount_yi": min_amount_yi,
            "filter.volume_ratio": volume_ratio,
            "filter.ma_condition": ma_condition,
            "recommend.top_n": top_n,
            "recommend.include_avoid": include_avoid,
            "recommend.enable_market_scan": enable_market_scan,
            "notify.email_enabled": email_enabled,
            "notify.score_change_threshold": score_change_threshold,
        })
        st.success("个人设置已保存，下次评分和扫描会使用新参数")
        st.rerun()

    if reset:
        reset_user_settings(user_id)
        st.success("已恢复默认设置")
        st.rerun()


def render_system_management_page():
    st.subheader("系统管理")
    user = current_user()
    if not user or user.get("role") != "admin":
        st.warning("只有管理员可以访问系统管理")
        return

    st.markdown("### 注册用户")
    users_df = pd.DataFrame(list_users())
    if not users_df.empty:
        st.dataframe(
            users_df[["id", "username", "display_name", "email", "role", "is_active", "created_at", "last_login_at"]],
            use_container_width=True,
            hide_index=True,
        )
        with st.form("toggle_user_active_form"):
            user_options = [
                f"{row['id']} {row['username']} ({'启用' if int(row['is_active']) else '禁用'})"
                for _, row in users_df.iterrows()
                if int(row["id"]) != get_current_user_id()
            ]
            selected = st.selectbox("选择用户", user_options)
            target_active = st.radio("账号状态", ["启用", "禁用"], horizontal=True)
            submitted = st.form_submit_button("更新账号状态")
            if submitted and selected:
                target_user_id = int(selected.split(" ", 1)[0])
                set_user_active(target_user_id, 1 if target_active == "启用" else 0)
                st.success("账号状态已更新")
                st.rerun()

    st.markdown("### 系统运行状态")
    scan_summary = get_latest_dynamic_pool_summary()
    status_cols = st.columns(4)
    if scan_summary:
        status_cols[0].metric("最近扫描日期", scan_summary.get("trade_date", "--"))
        status_cols[1].metric("扫描股票数", scan_summary.get("scanned_count", "--"))
        status_cols[2].metric("入选数量", scan_summary.get("selected_count", "--"))
        status_cols[3].metric("完成时间", scan_summary.get("completed_at", "--"))
    else:
        status_cols[0].metric("最近扫描日期", "--")
        status_cols[1].metric("扫描股票数", "--")
        status_cols[2].metric("入选数量", "--")
        status_cols[3].metric("完成时间", "--")
    st.caption("API调用次数暂未持久化统计，后续接入接口计数表后展示。")

    if st.button("手动触发全市场扫描", key="admin_run_market_scan"):
        with st.spinner("正在执行全市场扫描..."):
            result = run_dynamic_pool_scan()
        if result.get("ok"):
            st.success(result.get("message", "扫描完成"))
        else:
            st.info(result.get("message", "扫描已跳过"))


def render_backtest_page(stock_code):
    st.subheader("策略回测")
    user_id = get_current_user_id()

    price_df = load_backtest_price_data(stock_code)
    if price_df.empty:
        st.info("暂无可用回测行情数据")
        return

    min_date = price_df["trade_date"].min().date()
    max_date = price_df["trade_date"].max().date()

    with st.form("ma_backtest_form"):
        range_col, ma_col, cash_col = st.columns([1.4, 1, 1])
        date_range = range_col.date_input(
            "回测时间范围",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
        short_window = ma_col.number_input("短均线", min_value=2, max_value=120, value=5, step=1)
        long_window = ma_col.number_input("长均线", min_value=5, max_value=250, value=20, step=1)
        initial_cash = cash_col.number_input("初始资金", min_value=10000, value=100000, step=10000)
        commission = cash_col.number_input("手续费率", min_value=0.0, max_value=0.01, value=0.0003, step=0.0001, format="%.4f")
        slippage = cash_col.number_input("滑点", min_value=0.0, max_value=0.05, value=0.001, step=0.0005, format="%.4f")
        submitted = st.form_submit_button("运行回测")

    if not submitted:
        st.info("设置参数后点击「运行回测」查看策略结果")
        return

    if short_window >= long_window:
        st.warning("短均线必须小于长均线")
        return

    if not isinstance(date_range, tuple) or len(date_range) != 2:
        st.warning("请选择完整的开始和结束日期")
        return

    start_date, end_date = date_range
    backtest_df = price_df[
        (price_df["trade_date"].dt.date >= start_date)
        & (price_df["trade_date"].dt.date <= end_date)
    ].copy()

    if len(backtest_df) <= long_window:
        st.warning("回测区间数据不足，请扩大时间范围或降低长均线参数")
        return

    result_df, trade_df, metrics = run_ma_backtest(
        backtest_df,
        short_window=int(short_window),
        long_window=int(long_window),
        initial_cash=float(initial_cash),
        commission=float(commission),
        slippage=float(slippage),
    )
    save_backtest_result(
        user_id,
        stock_code,
        params={
            "start_date": start_date,
            "end_date": end_date,
            "short_window": int(short_window),
            "long_window": int(long_window),
            "initial_cash": float(initial_cash),
            "commission": float(commission),
            "slippage": float(slippage),
        },
        metrics=metrics,
    )

    metric_cols = st.columns(5)
    metric_cols[0].markdown(metric_html("策略累计收益率", format_colored_return(metrics["策略累计收益率"])), unsafe_allow_html=True)
    metric_cols[1].markdown(metric_html("买入持有收益率", format_colored_return(metrics["买入持有收益率"])), unsafe_allow_html=True)
    metric_cols[2].metric("夏普比率", f"{metrics['夏普比率']:.2f}")
    metric_cols[3].markdown(metric_html("最大回撤", format_colored_return(metrics["最大回撤"])), unsafe_allow_html=True)
    metric_cols[4].metric("交易次数", metrics["交易次数"])

    nav_df = result_df[["trade_date", "strategy_nav", "market_nav"]].melt(
        id_vars="trade_date",
        var_name="series",
        value_name="nav",
    )
    nav_df["series"] = nav_df["series"].map({
        "strategy_nav": "策略净值",
        "market_nav": "买入持有",
    })
    nav_chart = (
        alt.Chart(nav_df)
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X("trade_date:T", title="日期"),
            y=alt.Y("nav:Q", title="累计净值"),
            color=alt.Color(
                "series:N",
                title="曲线",
                scale=alt.Scale(domain=["策略净值", "买入持有"], range=["#2563eb", "#64748b"]),
            ),
            tooltip=[
                alt.Tooltip("trade_date:T", title="日期"),
                alt.Tooltip("series:N", title="曲线"),
                alt.Tooltip("nav:Q", title="净值", format=".4f"),
            ],
        )
        .properties(height=360)
    )
    st.altair_chart(nav_chart, use_container_width=True)

    drawdown_chart = (
        alt.Chart(result_df)
        .mark_area(color=A_SHARE_DOWN_COLOR, opacity=0.28)
        .encode(
            x=alt.X("trade_date:T", title="日期"),
            y=alt.Y("drawdown:Q", title="回撤", axis=alt.Axis(format="%")),
            tooltip=[
                alt.Tooltip("trade_date:T", title="日期"),
                alt.Tooltip("drawdown:Q", title="回撤", format=".2%"),
            ],
        )
        .properties(height=220)
    )
    st.altair_chart(drawdown_chart, use_container_width=True)

    if trade_df.empty:
        st.info("当前参数下没有实际成交")
    else:
        trade_view = trade_df.copy()
        trade_view["trade_date"] = trade_view["trade_date"].dt.strftime("%Y-%m-%d")
        trade_view["price"] = trade_view["price"].map(lambda value: f"{value:.2f}")
        trade_view["fee"] = trade_view["fee"].map(lambda value: f"{value:.2f}")
        trade_view["cash"] = trade_view["cash"].map(lambda value: f"{value:.2f}")
        trade_view = trade_view.rename(columns={
            "trade_date": "交易日期",
            "action": "操作",
            "price": "成交价",
            "shares": "股数",
            "fee": "手续费",
            "cash": "现金余额",
        })
        st.dataframe(trade_view, use_container_width=True, hide_index=True)

    recent_results = list_backtest_results(user_id, stock_code=stock_code, limit=10)
    if recent_results:
        st.markdown("### 我的最近回测")
        recent_view = []
        for item in recent_results:
            params = item.get("params", {})
            item_metrics = item.get("metrics", {})
            recent_view.append({
                "运行时间": item.get("created_at"),
                "区间": f"{params.get('start_date', '')} ~ {params.get('end_date', '')}",
                "均线": f"{params.get('short_window', '')}/{params.get('long_window', '')}",
                "策略收益": item_metrics.get("策略累计收益率"),
                "最大回撤": item_metrics.get("最大回撤"),
                "交易次数": item_metrics.get("交易次数"),
            })
        st.dataframe(pd.DataFrame(recent_view), use_container_width=True, hide_index=True)


def select_stock_in_sidebar(pool):
    options = [
        f"{item['code']} {item['name']}"
        for item in pool
    ] or STOCK_OPTIONS
    if not options:
        st.sidebar.info("当前没有可分析股票")
        return None
    default_option = next(
        (option for option in options if option.startswith("600519 ")),
        options[0],
    )
    selected_code = st.session_state.get("selected_stock_code")
    selected_option = next(
        (option for option in options if selected_code and option.startswith(f"{selected_code} ")),
        default_option,
    )
    if "selected_stock_option" not in st.session_state:
        st.session_state["selected_stock_option"] = selected_option
    elif st.session_state["selected_stock_option"] not in options:
        st.session_state["selected_stock_option"] = selected_option
    stock_option = st.sidebar.selectbox(
        "分析股票",
        options,
        key="selected_stock_option",
    )
    stock_code = stock_option.split(" ", 1)[0]
    st.session_state["selected_stock_code"] = stock_code
    return stock_code


st.set_page_config(page_title="AI量化交易系统", layout="wide")
apply_theme()
current_user_info = require_authentication()
STOCK_POOL = get_stock_pool(user_id=get_current_user_id())
initial_settings = get_current_user_settings()
if not initial_settings["recommend.enable_market_scan"]:
    STOCK_POOL = [item for item in STOCK_POOL if item.get("source") != "扫描"]
STOCK_NAMES = {
    item["code"]: item["name"]
    for item in STOCK_POOL
}
STOCK_METADATA = {
    item["code"]: {
        "industry": item.get("industry", "未分类"),
        "market_cap": item.get("market_cap", "未分类"),
    }
    for item in STOCK_POOL
}
STOCK_OPTIONS = [
    f"{item['code']} {item['name']}"
    for item in STOCK_POOL
]
render_user_header(current_user_info)

st.sidebar.title("导航")
nav_options = ["首页总览", "股票分析", "推荐池", "历史回测", "个人设置"]
if current_user_info.get("role") == "admin":
    nav_options.append("系统管理")
page = st.sidebar.radio(
    "选择功能",
    nav_options,
    key="page",
)
selected_industries, selected_market_caps = render_pool_filters()
st.session_state["selected_industries"] = selected_industries
st.session_state["selected_market_caps"] = selected_market_caps
filtered_stock_pool = filter_stock_pool(STOCK_POOL)
settings = get_current_user_settings()

st.title("AI量化交易系统")

if page == "个人设置":
    render_personal_settings_page()
    st.stop()
if page == "系统管理":
    render_system_management_page()
    st.stop()

df, validation_df = load_score_data()

if df.empty:
    st.warning("暂无评分数据，请先运行 python run_report.py")
    st.stop()

df = prepare_score_data(df)
df = apply_score_filters(df, filtered_stock_pool)
if not settings["recommend.include_avoid"]:
    df = df[df["level"] != "回避"].copy()

if df.empty:
    st.warning("当前筛选条件下暂无评分数据")
    if page == "首页总览":
        render_stock_pool_overview(STOCK_POOL)
    st.stop()

top5 = df.head(int(settings["recommend.top_n"]))

if page in ["股票分析", "历史回测"]:
    stock_code = select_stock_in_sidebar(filtered_stock_pool)

if page == "首页总览":
    render_metrics(df)
    render_market_scan_card()
    render_news_sentiment_controls()
    render_stock_pool_overview(STOCK_POOL)
    render_top5(top5)
    render_watchlist_manager()
elif page == "股票分析":
    if stock_code:
        render_stock_detail_page(stock_code)
    else:
        st.info("当前没有股票可分析，请先加入自选股或开启全市场扫描。")
elif page == "推荐池":
    render_top5(top5)
    render_candidate_pool()
elif page == "历史回测":
    if stock_code:
        render_backtest_page(stock_code)
        render_score_validation(validation_df)
    else:
        st.info("当前没有股票可回测，请先加入自选股或开启全市场扫描。")

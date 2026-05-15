import os
import sqlite3
from html import escape

import altair as alt
import pandas as pd
import streamlit as st


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in os.sys.path:
    os.sys.path.insert(0, PROJECT_ROOT)

from stock_pool import get_stock_pool

from database.watchlist import (
    delete_watchlist_stock,
    list_watchlist,
    set_watchlist_enabled,
    upsert_watchlist_stock,
)
from database.daily_candidates import list_latest_candidates
from strategy.score import score_level
from watchlist_pipeline import process_watchlist_stock

DB_NAME = os.path.join(PROJECT_ROOT, "quant.db")
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
}


def display_table(dataframe):
    return dataframe.rename(columns=COLUMN_LABELS)


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
        return STOCK_NAMES[code]

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
    return STOCK_METADATA.get(str(stock_code).zfill(6), {
        "industry": "未分类",
        "market_cap": "未分类",
    })


def industry_badge_html(industry, market_cap=None):
    label = escape(str(industry))
    if market_cap and str(market_cap) != "未分类":
        label = f"{label} · {escape(str(market_cap))}"
    return (
        "<span style='display:inline-flex;align-items:center;padding:0.18rem 0.54rem;"
        "border-radius:999px;background:#eef2ff;color:#3730a3;border:1px solid #c7d2fe;"
        f"font-size:0.82rem;font-weight:700;'>{label}</span>"
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
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql("""
    SELECT *
    FROM stock_scores
    WHERE id IN (
        SELECT MAX(id)
        FROM stock_scores
        GROUP BY stock_code
    )
    ORDER BY score DESC, created_at DESC
    """, conn)

    try:
        validation_df = pd.read_sql("""
        SELECT *
        FROM score_validation
        ORDER BY score_date DESC, score DESC
        """, conn)
    except Exception:
        validation_df = pd.DataFrame()

    conn.close()
    return df, validation_df


def load_history_data(stock_code):
    conn = sqlite3.connect(DB_NAME)
    history_df = pd.read_sql("""
    SELECT *
    FROM stock_scores
    WHERE stock_code = ?
    ORDER BY created_at
    """, conn, params=(stock_code,))
    conn.close()
    return history_df


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

    view = filtered_df[["code", "name", "industry", "market_cap"]].rename(columns={
        "code": "股票代码",
        "name": "股票名称",
        "industry": "行业",
        "market_cap": "市值",
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

    if "watchlist_pipeline_message" in st.session_state:
        message = st.session_state.pop("watchlist_pipeline_message")
        if message["ok"]:
            st.success(message["text"])
        else:
            st.warning(message["text"])

    watchlist_rows = list_watchlist()
    watchlist_df = pd.DataFrame(watchlist_rows)

    with st.form("add_watchlist_stock"):
        form_cols = st.columns(3)
        new_stock_code = form_cols[0].text_input("股票代码", placeholder="例如 600519")
        new_stock_name = form_cols[1].text_input("股票名称", placeholder="例如 贵州茅台")
        new_note = form_cols[2].text_input("备注", placeholder="可选")
        submitted = st.form_submit_button("加入自选股")

        if submitted:
            if not new_stock_code.strip() or not new_stock_name.strip():
                st.warning("请填写股票代码和股票名称")
            else:
                upsert_watchlist_stock(
                    new_stock_code.strip(),
                    new_stock_name.strip(),
                    note=new_note.strip() or None,
                )
                with st.spinner("正在采集行情并生成评分..."):
                    result = process_watchlist_stock(
                        new_stock_code.strip(),
                        new_stock_name.strip(),
                    )
                st.session_state["watchlist_pipeline_message"] = {
                    "ok": result["ok"],
                    "text": result["message"],
                }
                st.rerun()

    if watchlist_df.empty:
        st.info("暂无自选股")
    else:
        watchlist_df["enabled"] = watchlist_df["enabled"].map({1: "启用", 0: "停用"})
        st.dataframe(display_table(watchlist_df), use_container_width=True)

        action_cols = st.columns(4)
        action_stock_code = action_cols[0].text_input(
            "管理股票代码",
            placeholder="输入要管理的代码",
        )
        if action_cols[1].button("启用"):
            if action_stock_code.strip():
                set_watchlist_enabled(action_stock_code, 1)
                st.rerun()
            else:
                st.warning("请先输入要管理的股票代码")
        if action_cols[2].button("停用"):
            if action_stock_code.strip():
                set_watchlist_enabled(action_stock_code, 0)
                st.rerun()
            else:
                st.warning("请先输入要管理的股票代码")
        if action_cols[3].button("删除"):
            if action_stock_code.strip():
                delete_watchlist_stock(action_stock_code)
                st.rerun()
            else:
                st.warning("请先输入要管理的股票代码")


def render_top5(top5):
    st.subheader("今日Top5推荐")
    header_cols = st.columns([1.1, 1.5, 1.2, 0.8, 1.1, 1, 1])
    header_cols[0].markdown("**股票代码**")
    header_cols[1].markdown("**股票名称**")
    header_cols[2].markdown("**板块**")
    header_cols[3].markdown("**评分**")
    header_cols[4].markdown("**等级**")
    header_cols[5].markdown("**加入自选**")
    header_cols[6].markdown("**查看详情**")

    for _, row in top5.iterrows():
        stock_code = str(row["stock_code"]).zfill(6)
        stock_name = str(row["stock_name"])
        metadata = get_stock_metadata(stock_code)
        row_cols = st.columns([1.1, 1.5, 1.2, 0.8, 1.1, 1, 1])
        row_cols[0].write(stock_code)
        row_cols[1].write(stock_name)
        row_cols[2].markdown(industry_badge_html(metadata["industry"], metadata["market_cap"]), unsafe_allow_html=True)
        row_cols[3].write(row["score"])
        row_cols[4].markdown(level_badge_html(row["level"]), unsafe_allow_html=True)

        if row_cols[5].button("加入自选", key=f"top5_add_{stock_code}"):
            upsert_watchlist_stock(
                stock_code,
                stock_name,
                source="top5",
                note="来自今日Top5推荐",
            )
            st.success(f"{stock_code} {stock_name} 已加入自选股")

        row_cols[6].button(
            "查看详情",
            key=f"top5_detail_{stock_code}",
            on_click=open_stock_detail,
            args=(stock_code,),
        )


def render_candidate_pool():
    st.subheader("AI每日推荐池")

    candidate_rows = list_latest_candidates(enabled_only=False)
    if candidate_rows:
        candidate_df = pd.DataFrame(candidate_rows)
        candidate_df = enrich_level_from_score(candidate_df)
        candidate_df = map_candidate_status(candidate_df)
        candidate_df["industry"] = candidate_df["stock_code"].apply(lambda code: get_stock_metadata(code)["industry"])
        candidate_df["market_cap"] = candidate_df["stock_code"].apply(lambda code: get_stock_metadata(code)["market_cap"])
        st.dataframe(
            style_level_badges(candidate_df[[
                "trade_date",
                "stock_code",
                "stock_name",
                "industry",
                "market_cap",
                "score",
                "level",
                "reason",
                "source",
                "enabled",
            ]]),
            use_container_width=True,
            hide_index=True,
        )
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


def render_score_rank(dataframe):
    st.subheader("股票评分排行榜")
    st.dataframe(style_level_badges(dataframe), use_container_width=True)


def render_history_trend(stock_code):
    st.subheader("历史评分趋势")

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

        score_chart_df = history_df[["created_at", "score"]].copy()
        score_chart_df["chart_date"] = score_chart_df["created_at"].dt.normalize()

        price_df = load_price_data(stock_code)
        if price_df.empty:
            st.line_chart(history_df.set_index("created_at")["score"])
            st.info("暂无可用价格数据，当前仅展示评分趋势")
        else:
            min_date = score_chart_df["chart_date"].min()
            max_date = score_chart_df["chart_date"].max()
            price_chart_df = price_df[
                (price_df["trade_date"] >= min_date)
                & (price_df["trade_date"] <= max_date)
            ].copy()

            if price_chart_df.empty:
                st.line_chart(history_df.set_index("created_at")["score"])
                st.info("评分日期范围内暂无可用价格数据，当前仅展示评分趋势")
            else:
                score_line = (
                    alt.Chart(score_chart_df)
                    .mark_line(point=True, strokeWidth=3)
                    .encode(
                        x=alt.X("chart_date:T", title="日期"),
                        y=alt.Y("score:Q", title="评分", scale=alt.Scale(domain=[0, 100])),
                        color=alt.value("#2563eb"),
                        tooltip=[
                            alt.Tooltip("created_at:T", title="评分时间"),
                            alt.Tooltip("score:Q", title="评分"),
                        ],
                    )
                )
                price_line = (
                    alt.Chart(price_chart_df)
                    .mark_line(strokeWidth=2, strokeDash=[5, 3])
                    .encode(
                        x=alt.X("trade_date:T", title="日期"),
                        y=alt.Y("close:Q", title="收盘价", scale=alt.Scale(zero=False)),
                        color=alt.value("#f59e0b"),
                        tooltip=[
                            alt.Tooltip("trade_date:T", title="交易日期"),
                            alt.Tooltip("close:Q", title="收盘价", format=",.2f"),
                        ],
                    )
                )
                combined_chart = (
                    alt.layer(score_line, price_line)
                    .resolve_scale(y="independent")
                    .properties(height=420)
                )
                legend_cols = st.columns(2)
                legend_cols[0].markdown("<span style='color:#2563eb;font-weight:700;'>●</span> 评分（左轴）", unsafe_allow_html=True)
                legend_cols[1].markdown("<span style='color:#f59e0b;font-weight:700;'>●</span> 收盘价（右轴）", unsafe_allow_html=True)
                st.altair_chart(combined_chart, use_container_width=True)

        st.dataframe(
            style_level_badges(history_df[[
                "score_date",
                "created_at",
                "stock_code",
                "stock_name",
                "score",
                "level",
            ]].tail(20)),
            use_container_width=True,
        )


def render_score_validation(validation_df):
    st.subheader("评分验证")

    if validation_df.empty:
        st.info("暂无评分验证数据，请运行 python report/score_validation.py")
    else:
        return_cols = ["return_1d", "return_3d", "return_5d", "return_10d"]
        validation_summary = build_validation_summary(validation_df, return_cols)
        validation_summary_view = validation_summary.copy()
        validation_summary_view["win_rate"] = validation_summary_view["win_rate"].apply(format_ratio)
        validation_summary_view["avg_return"] = validation_summary_view["avg_return"].apply(format_return)
        validation_summary_view["max_drawdown"] = validation_summary_view["max_drawdown"].apply(format_return)
        validation_summary_view = validation_summary_view.rename(columns={
            "horizon": "验证周期",
            "sample_count": "样本数",
            "win_rate": "胜率",
            "avg_return": "平均收益",
            "max_drawdown": "最大回撤",
        })
        st.dataframe(
            style_level_badges(validation_summary_view),
            use_container_width=True,
            hide_index=True,
        )

        summary_df = validation_df.groupby("level")[return_cols].mean().reset_index()
        summary_df = summary_df.copy()
        for col in return_cols:
            summary_df[col] = summary_df[col].apply(format_return)
        st.caption("按等级统计平均收益")
        st.dataframe(style_level_badges(summary_df), use_container_width=True)

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
            validation_view[col] = validation_view[col].apply(format_return)
        st.dataframe(style_level_badges(validation_view), use_container_width=True)


def render_backtest_page(stock_code):
    st.subheader("策略回测")

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

    metric_cols = st.columns(5)
    metric_cols[0].metric("策略累计收益率", format_return(metrics["策略累计收益率"]))
    metric_cols[1].metric("买入持有收益率", format_return(metrics["买入持有收益率"]))
    metric_cols[2].metric("夏普比率", f"{metrics['夏普比率']:.2f}")
    metric_cols[3].metric("最大回撤", format_return(metrics["最大回撤"]))
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
        .mark_area(color="#dc2626", opacity=0.28)
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


def select_stock_in_sidebar(pool):
    options = [
        f"{item['code']} {item['name']}"
        for item in pool
    ] or STOCK_OPTIONS
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

st.sidebar.title("导航")
page = st.sidebar.radio(
    "选择功能",
    ["首页总览", "股票分析", "推荐池", "历史回测"],
    key="page",
)
selected_industries, selected_market_caps = render_pool_filters()
st.session_state["selected_industries"] = selected_industries
st.session_state["selected_market_caps"] = selected_market_caps
filtered_stock_pool = filter_stock_pool(STOCK_POOL)

st.title("AI量化交易系统")

df, validation_df = load_score_data()

if df.empty:
    st.warning("暂无评分数据，请先运行 python run_report.py")
    st.stop()

df = prepare_score_data(df)
df = apply_score_filters(df, filtered_stock_pool)

if df.empty:
    st.warning("当前筛选条件下暂无评分数据")
    if page == "首页总览":
        render_stock_pool_overview(STOCK_POOL)
    st.stop()

top5 = df.head(5)

if page in ["股票分析", "历史回测"]:
    stock_code = select_stock_in_sidebar(filtered_stock_pool)

if page == "首页总览":
    render_metrics(df)
    render_stock_pool_overview(STOCK_POOL)
    render_top5(top5)
    render_watchlist_manager()
elif page == "股票分析":
    render_score_chart(df)
    render_history_trend(stock_code)
    render_score_rank(df)
elif page == "推荐池":
    render_top5(top5)
    render_candidate_pool()
elif page == "历史回测":
    render_backtest_page(stock_code)
    render_score_validation(validation_df)

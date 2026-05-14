import os
import sqlite3

import pandas as pd
import streamlit as st

from stock_pool import STOCK_POOL


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.path.join(PROJECT_ROOT, "quant.db")
STOCKS_DIR = os.path.join(PROJECT_ROOT, "data", "stocks")
STOCK_NAMES = {
    item["code"]: item["name"]
    for item in STOCK_POOL
}
STOCK_OPTIONS = [
    f"{item['code']} {item['name']}"
    for item in STOCK_POOL
]


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


def format_return(value):
    if pd.isna(value):
        return "等待后续交易日"
    return f"{value * 100:.2f}%"

st.title("AI量化交易系统")

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

default_option = next(
    (option for option in STOCK_OPTIONS if option.startswith("600519 ")),
    STOCK_OPTIONS[0],
)
stock_option = st.selectbox(
    "选择股票",
    STOCK_OPTIONS,
    index=STOCK_OPTIONS.index(default_option),
)
stock_code = stock_option.split(" ", 1)[0]

history_df = pd.read_sql("""
SELECT *
FROM stock_scores
WHERE stock_code = ?
ORDER BY created_at
""", conn, params=(stock_code,))

try:
    validation_df = pd.read_sql("""
    SELECT *
    FROM score_validation
    ORDER BY score_date DESC, score DESC
    """, conn)
except Exception:
    validation_df = pd.DataFrame()

conn.close()

if df.empty:
    st.warning("暂无评分数据，请先运行 python run_report.py")
    st.stop()

df["stock_name"] = df["stock_code"].apply(get_stock_name)
df["display_name"] = df["stock_code"] + " " + df["stock_name"]
df["created_at"] = pd.to_datetime(df["created_at"])
latest_time = df["created_at"].max().strftime("%Y-%m-%d %H:%M:%S")

top5 = df.head(5)

metric_cols = st.columns(3)
metric_cols[0].metric("股票池数量", len(STOCK_POOL))
metric_cols[1].metric("已评分股票", len(df))
metric_cols[2].metric("最近更新时间", latest_time)

st.subheader("今日Top5推荐")

st.table(top5[[
    "stock_code",
    "stock_name",
    "score",
    "level"
]])

st.subheader("股票评分图")

min_chart_count = min(5, len(df))
top_n = st.slider(
    "显示股票数量",
    min_value=min_chart_count,
    max_value=len(df),
    value=min(10, len(df)),
)

chart_data = df.head(top_n)[[
    "display_name",
    "score"
]].set_index("display_name")

st.bar_chart(chart_data)

st.subheader("历史评分趋势")

if history_df.empty:
    st.info("暂无该股票的历史评分记录")
else:
    history_df["stock_name"] = history_df["stock_code"].apply(get_stock_name)
    history_df["created_at"] = pd.to_datetime(history_df["created_at"])
    history_df["score_date"] = history_df["created_at"].dt.date
    history_df = history_df.drop_duplicates(subset=["score_date"], keep="last")
    selected_name = history_df["stock_name"].iloc[-1]
    st.caption(f"{stock_code} {selected_name}")
    st.line_chart(
        history_df.set_index("created_at")["score"]
    )
    st.dataframe(
        history_df[["score_date", "created_at", "stock_code", "stock_name", "score", "level"]].tail(20),
        use_container_width=True,
    )

st.subheader("股票评分排行榜")

st.dataframe(df, use_container_width=True)

st.subheader("评分验证")

if validation_df.empty:
    st.info("暂无评分验证数据，请运行 python report/score_validation.py")
else:
    return_cols = ["return_1d", "return_3d", "return_5d", "return_10d"]
    summary_df = validation_df.groupby("level")[return_cols].mean().reset_index()
    summary_df = summary_df.copy()
    for col in return_cols:
        summary_df[col] = summary_df[col].apply(format_return)
    st.dataframe(summary_df, use_container_width=True)

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
    st.dataframe(validation_view, use_container_width=True)

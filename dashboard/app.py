import os
import sqlite3

import pandas as pd
import streamlit as st


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.path.join(PROJECT_ROOT, "quant.db")
STOCKS_DIR = os.path.join(PROJECT_ROOT, "data", "stocks")


def get_stock_name(stock_code):
    code = str(stock_code).zfill(6)

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

st.title("AI量化交易系统")

conn = sqlite3.connect(DB_NAME)

df = pd.read_sql("""
SELECT *
FROM stock_scores
ORDER BY created_at DESC
LIMIT 20
""", conn)

stock_code = st.text_input(
    "输入股票代码",
    "600519"
)

history_df = pd.read_sql("""
SELECT *
FROM stock_scores
WHERE stock_code = ?
ORDER BY created_at
""", conn, params=(stock_code,))

conn.close()

df["stock_name"] = df["stock_code"].apply(get_stock_name)
df["display_name"] = df["stock_code"] + " " + df["stock_name"]

top5 = df.head(5)

st.subheader("今日Top5推荐")

st.table(top5[[
    "stock_code",
    "stock_name",
    "score",
    "level"
]])

st.subheader("股票评分图")

chart_data = df[[
    "display_name",
    "score"
]].set_index("display_name")

st.bar_chart(chart_data)

st.subheader("历史评分趋势")

if history_df.empty:
    st.info("暂无该股票的历史评分记录")
else:
    history_df["stock_name"] = history_df["stock_code"].apply(get_stock_name)
    st.line_chart(
        history_df.set_index("created_at")["score"]
    )

st.subheader("股票评分排行榜")

st.dataframe(df)

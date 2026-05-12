import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator
import matplotlib

# ========== 中文字体支持 ==========
# Windows 常用中文字体回退
_chinese_fonts = ["Microsoft YaHei", "SimHei", "WenQuanYi Micro Hei", "Noto Sans CJK SC", "sans-serif"]
for _f in _chinese_fonts:
    try:
        matplotlib.font_manager.findfont(_f, fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = [_f] + plt.rcParams["font.sans-serif"]
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False  # 解决负号显示问题

# 读取数据
df = pd.read_csv("data/stocks/海康威视.csv")

# 日期解析 & 按时间正序排列
df["日期"] = pd.to_datetime(df["日期"])
df = df.sort_values("日期").reset_index(drop=True)

# 兼容中文字段
df["收盘"] = pd.to_numeric(df["收盘"])

# 计算均线
df["MA5"] = df["收盘"].rolling(5).mean()
df["MA20"] = df["收盘"].rolling(20).mean()

# 上期均线（用于判断交叉）
df["Prev_MA5"] = df["MA5"].shift(1)
df["Prev_MA20"] = df["MA20"].shift(1)

# 买卖信号（仅交叉时触发）
df["Signal"] = 0

buy = (df["Prev_MA5"] <= df["Prev_MA20"]) & (df["MA5"] > df["MA20"])
sell = (df["Prev_MA5"] >= df["Prev_MA20"]) & (df["MA5"] < df["MA20"])

df.loc[buy, "Signal"] = 1
df.loc[sell, "Signal"] = -1

print("=== 数据范围 ===")
print(f"起: {df['日期'].min().strftime('%Y-%m-%d')}  止: {df['日期'].max().strftime('%Y-%m-%d')}")
print(f"共 {len(df)} 个交易日")
print()

print(df[["日期", "收盘", "MA5", "MA20", "Signal"]].tail(20))

# ========== 绘图 ==========
plt.figure(figsize=(14, 7))

plt.plot(df["日期"], df["收盘"], label="收盘", linewidth=0.8)
plt.plot(df["日期"], df["MA5"], label="MA5", linewidth=0.8)
plt.plot(df["日期"], df["MA20"], label="MA20", linewidth=0.8)

# 标记买卖点
buy_df = df[df["Signal"] == 1]
sell_df = df[df["Signal"] == -1]
plt.scatter(buy_df["日期"], buy_df["收盘"], marker="^", color="red", s=60, label="买入", zorder=5)
plt.scatter(sell_df["日期"], sell_df["收盘"], marker="v", color="green", s=60, label="卖出", zorder=5)

# ---- X 轴日期优化 ----
ax = plt.gca()
ax.xaxis.set_major_locator(MaxNLocator(nbins=12))         # 最多 12 个刻度
ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
plt.xticks(rotation=30, ha="right")

plt.xlabel("日期")
plt.ylabel("价格")
plt.title("海康威视 MA 双均线策略")
plt.legend(loc="upper left")
plt.grid(True, alpha=0.3)
plt.tight_layout()
plt.show()

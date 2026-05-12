import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator
import matplotlib
import numpy as np

# ========== 中文字体支持 ==========
_chinese_fonts = ["Microsoft YaHei", "SimHei", "WenQuanYi Micro Hei", "Noto Sans CJK SC", "sans-serif"]
for _f in _chinese_fonts:
    try:
        matplotlib.font_manager.findfont(_f, fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = [_f] + plt.rcParams["font.sans-serif"]
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False

# ========== 配置 ==========
STOCK_CSV       = "data/stocks/海康威视.csv"
SENTIMENT_CSV   = ""          # 情绪CSV路径，空字符串=不启用情绪过滤
BUY_SCORE_MIN   = 60         # 买入所需最低情绪评分
SELL_SCORE_MAX   = -50        # 卖出触发的最高情绪评分
INITIAL_CASH    = 100_000
COMMISSION      = 0.0003
SLIPPAGE        = 0.001


def load_data(csv_path):
    df = pd.read_csv(csv_path)
    df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期").reset_index(drop=True)
    df["收盘"] = pd.to_numeric(df["收盘"])
    return df


def load_sentiment(csv_path):
    """
    加载情绪CSV（含 日期,评分 两列）
    返回: DataFrame with columns ["日期", "评分"]
    """
    if not csv_path:
        return None
    sdf = pd.read_csv(csv_path)
    sdf["日期"] = pd.to_datetime(sdf["日期"])
    # 取评分列（兼容 评分/score/情绪评分 等列名）
    score_col = None
    for c in sdf.columns:
        if "评分" in c or c.lower() == "score":
            score_col = c
            break
    if score_col is None:
        raise ValueError("情绪CSV中找不到评分列，请确认列名含'评分'或'score'")
    return sdf[["日期", score_col]].rename(columns={score_col: "评分"})


def compute_signals(df, sentiment_df=None):
    """
    计算买卖信号
    buy : MA5上穿MA20 且 (无情绪数据 或 评分 >= BUY_SCORE_MIN)
    sell: MA5下穿MA20 或 (有情绪数据 且 评分 <= SELL_SCORE_MAX)
    """
    df["MA5"]  = df["收盘"].rolling(5).mean()
    df["MA20"] = df["收盘"].rolling(20).mean()
    df["Prev_MA5"]  = df["MA5"].shift(1)
    df["Prev_MA20"] = df["MA20"].shift(1)

    # 合并情绪评分
    use_sentiment = sentiment_df is not None
    if use_sentiment:
        df = df.merge(sentiment_df, on="日期", how="left")
        df["评分"] = df["评分"].fillna(0)
    else:
        df["评分"] = 0

    # 均线交叉信号
    ma_buy  = (df["Prev_MA5"] <= df["Prev_MA20"]) & (df["MA5"] > df["MA20"])
    ma_sell = (df["Prev_MA5"] >= df["Prev_MA20"]) & (df["MA5"] < df["MA20"])

    # 情绪信号（仅当有情绪数据时启用）
    sentiment_buy  = df["评分"] >= BUY_SCORE_MIN  if use_sentiment else True
    sentiment_sell = df["评分"] <= SELL_SCORE_MAX if use_sentiment else False

    # 最终信号
    buy  = ma_buy & sentiment_buy
    sell = ma_sell | sentiment_sell

    df["Signal"] = 0
    df.loc[buy,  "Signal"] = 1
    df.loc[sell, "Signal"] = -1

    # 调试：打印情绪触发次数（仅启用情绪时）
    if use_sentiment:
        n_buy_filtered  = (ma_buy & ~sentiment_buy).sum()
        n_sell_extra     = (~ma_sell & sentiment_sell).sum()
        if n_buy_filtered > 0 or n_sell_extra > 0:
            print(f"[情绪过滤] 因评分不足屏蔽买入: {n_buy_filtered} 次  |  因强利空追加卖出: {n_sell_extra} 次")

    return df


def backtest(df, initial_cash=INITIAL_CASH, commission=COMMISSION, slippage=SLIPPAGE):
    cash    = float(initial_cash)
    shares  = 0
    position = 0

    records = []
    for _, row in df.iterrows():
        signal = row["Signal"]
        price  = row["收盘"]
        date   = row["日期"]

        if signal == 1 and position == 0:
            exec_price = price * (1 + slippage)
            cost       = exec_price * 100
            max_shares = int(cash // cost) * 100
            if max_shares > 0:
                fee = max_shares * exec_price * commission
                cash      -= max_shares * exec_price + fee
                shares    = max_shares
                position  = 1
                records.append({
                    "日期": date, "操作": "买入",
                    "价格": round(exec_price, 4), "股数": shares,
                    "手续费": round(fee, 2), "现金余额": round(cash, 2)
                })

        elif signal == -1 and position == 1:
            exec_price = price * (1 - slippage)
            fee = shares * exec_price * commission
            cash      += shares * exec_price - fee
            records.append({
                "日期": date, "操作": "卖出",
                "价格": round(exec_price, 4), "股数": shares,
                "手续费": round(fee, 2), "现金余额": round(cash, 2)
            })
            shares   = 0
            position = 0

    final_value = cash + (shares * df.iloc[-1]["收盘"] if shares > 0 else 0)
    return final_value, pd.DataFrame(records)


def compute_metrics(df, final_value, initial_cash, trade_records):
    total_return = (final_value - initial_cash) / initial_cash

    df["Return"] = df["收盘"].pct_change()
    # 重新计算 Position
    positions = []
    pos = 0
    for s in df["Signal"]:
        if s == 1:
            pos = 1
        elif s == -1:
            pos = 0
        positions.append(pos)
    df["Position"] = positions

    df["Strategy_Return"] = df["Position"].shift(1) * df["Return"]
    df["Cumulative_Market"]  = (1 + df["Return"]).cumprod()
    df["Cumulative_Strategy"] = (1 + df["Strategy_Return"]).cumprod()

    daily_returns = df["Strategy_Return"].dropna()
    sharpe = np.sqrt(252) * daily_returns.mean() / daily_returns.std() if daily_returns.std() > 0 else 0

    cummax     = df["Cumulative_Strategy"].cummax()
    drawdown   = (df["Cumulative_Strategy"] - cummax) / cummax
    max_drawdown = drawdown.min()

    bh_return = df["Cumulative_Market"].iloc[-1] - 1

    trade_count     = len(trade_records)
    signal_count    = int(df["Signal"].abs().sum())
    total_commission = trade_records["手续费"].sum() if trade_count > 0 else 0

    return {
        "策略累计收益率": total_return,
        "买入持有收益率": bh_return,
        "夏普比率": sharpe,
        "最大回撤": max_drawdown,
        "期末总资产": final_value,
        "初始资金": initial_cash,
        "策略净买入持有超额": total_return - bh_return,
        "交易次数": trade_count,
        "信号次数": signal_count,
        "总手续费": total_commission,
    }, df


def print_metrics(metrics):
    tc = metrics["交易次数"]
    sc = metrics["信号次数"]
    print("=" * 50)
    print(f"  策略累计收益率:   {metrics['策略累计收益率']*100:+,.2f}%")
    print(f"  买入持有收益率:   {metrics['买入持有收益率']*100:+,.2f}%")
    print(f"  超额收益:         {metrics['策略净买入持有超额']*100:+,.2f}%")
    print(f"  夏普比率:         {metrics['夏普比率']:.2f}")
    print(f"  最大回撤:         {metrics['最大回撤']*100:.2f}%")
    print(f"  交易次数:         {tc} 笔")
    if tc != sc:
        print(f"  信号触发:         {sc} 次（部分因资金/情绪过滤未成交）")
    print(f"  总手续费:         ¥{metrics['总手续费']:,.2f}")
    print(f"  期末总资产:       ¥{metrics['期末总资产']:,.2f}")
    print(f"  初始资金:         ¥{metrics['初始资金']:,.2f}")
    profit = metrics['期末总资产'] - metrics['初始资金']
    if profit > 0:
        fee_ratio = metrics['总手续费'] / profit * 100
        print(f"  手续费/收益:      {fee_ratio:.1f}%（手续费侵蚀收益）")
    print("=" * 50)


def plot_results(df, metrics):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

    ax1.plot(df["日期"], df["Cumulative_Market"],  label="买入持有（Buy & Hold）", linewidth=1)
    ax1.plot(df["日期"], df["Cumulative_Strategy"], label="MA+情绪策略", linewidth=1)

    buy_df  = df[df["Signal"] == 1]
    sell_df = df[df["Signal"] == -1]
    if len(buy_df) > 0:
        ax1.scatter(buy_df["日期"], df.loc[buy_df.index, "Cumulative_Strategy"],
                    marker="^", color="red", s=50, label="买入", zorder=5)
    if len(sell_df) > 0:
        ax1.scatter(sell_df["日期"], df.loc[sell_df.index, "Cumulative_Strategy"],
                    marker="v", color="green", s=50, label="卖出", zorder=5)

    ax1.set_ylabel("累计净值（起始=1）")
    ax1.set_title(f"MA+情绪回测  策略{metrics['策略累计收益率']*100:.1f}% | 买入持有{metrics['买入持有收益率']*100:.1f}%")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    cummax   = df["Cumulative_Strategy"].cummax()
    drawdown = (df["Cumulative_Strategy"] - cummax) / cummax * 100
    ax2.fill_between(df["日期"], drawdown, 0, color="red", alpha=0.3)
    ax2.plot(df["日期"], drawdown, color="red", linewidth=0.8)
    ax2.set_ylabel("回撤 (%)")
    ax2.set_xlabel("日期")
    ax2.set_title(f"策略回撤  最大回撤：{metrics['最大回撤']*100:.1f}%")
    ax2.grid(True, alpha=0.3)

    for ax in (ax1, ax2):
        ax.xaxis.set_major_locator(MaxNLocator(nbins=12))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    plt.xticks(rotation=30, ha="right")

    plt.tight_layout()
    plt.savefig("backtest_result.png", dpi=150, bbox_inches="tight")
    print("\n图表已保存至 backtest_result.png")


def main():
    print(f"加载数据: {STOCK_CSV}")
    df = load_data(STOCK_CSV)
    print(f"数据范围: {df['日期'].min().strftime('%Y-%m-%d')} ~ {df['日期'].max().strftime('%Y-%m-%d')}  ({len(df)} 个交易日)\n")

    # 加载情绪数据（如已准备好 CSV）
    sentiment_df = None
    if SENTIMENT_CSV:
        print(f"加载情绪数据: {SENTIMENT_CSV}")
        sentiment_df = load_sentiment(SENTIMENT_CSV)
        print(f"情绪数据覆盖: {len(sentiment_df)} 条\n")
    else:
        print("未配置情绪CSV，仅使用MA信号\n")

    df = compute_signals(df, sentiment_df)

    print("开始回测...")
    final_value, trade_records = backtest(df)
    print(f"共执行 {len(trade_records)} 笔交易\n")

    if len(trade_records) > 0:
        print("前 10 笔交易记录：")
        print(trade_records.head(10).to_string(index=False))
        print()

    metrics, df = compute_metrics(df, final_value, INITIAL_CASH, trade_records)
    print_metrics(metrics)
    plot_results(df, metrics)


if __name__ == "__main__":
    main()

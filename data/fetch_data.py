import akshare as ak
import pandas as pd
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from stock_pool import get_fetch_stocks

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "stocks")

DEFAULT_STOCKS = get_fetch_stocks()

__all__ = ['fetch_stock_data', 'DEFAULT_STOCKS', 'OUTPUT_DIR']

def fetch_stock_data(stocks=None, output_dir=None):
    """
    获取股票历史数据并保存为CSV（AKShare版）
    """
    if stocks is None:
        stocks = DEFAULT_STOCKS
    if output_dir is None:
        output_dir = OUTPUT_DIR

    os.makedirs(output_dir, exist_ok=True)
    print(f"输出目录: {output_dir}")

    success_count = 0

    try:
        for code, name in stocks.items():
            try:
                print(f"正在获取: {code} {name}")

                # AKShare 获取日K数据（前复权）
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date="20000101",
                    end_date="20500101",
                    adjust="qfq"
                )

                if df.empty:
                    print(f"{code}({name}) 无数据")
                    continue

                # 重命名列 → 与原 baostock 格式一致
                df = df.rename(columns={
                    "日期": "日期",
                    "开盘": "开盘",
                    "最高": "最高",
                    "最低": "最低",
                    "收盘": "收盘",
                    "成交量": "成交量",
                    "成交额": "成交额",
                    "涨跌幅": "涨跌幅",
                    "换手率": "换手率"
                })

                # 补充缺少的字段（兼容原有结构）
                df["前收盘"] = df["收盘"].shift(1)  # 用前一天收盘价模拟
                df["股票名称"] = name
                df["代码"] = code

                # 排序：最新日期在前
                df = df.sort_values("日期", ascending=False).reset_index(drop=True)

                # 最终列顺序（和你原版完全一样）
                final_cols = [
                    "日期", "股票名称", "代码", "开盘", "最高", "最低", "收盘",
                    "前收盘", "成交量", "成交额", "换手率", "涨跌幅"
                ]
                df = df[[col for col in final_cols if col in df.columns]]

                # 保存 CSV
                filename = f"{name}.csv"
                df.to_csv(
                    os.path.join(output_dir, filename),
                    index=False,
                    encoding="utf-8-sig"
                )
                success_count += 1

                print(f"{code}({name}) 成功，共 {len(df)} 条")

            except Exception as e:
                print(f"{code}({name}) 失败: {str(e)}")
                continue

        print("全部股票处理完成 ✅")

    except Exception as e:
        print(f"处理异常: {e}")
        return False

    return success_count > 0

def main():
    fetch_stock_data()

if __name__ == "__main__":
    main()

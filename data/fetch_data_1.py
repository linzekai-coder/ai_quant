import baostock as bs
import pandas as pd
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "stocks")

# 默认股票列表 (代码: 名称)
DEFAULT_STOCKS = {
    "sz.000001": "平安银行",
    "sh.600519": "贵州茅台",
    "sz.000858": "五粮液",
    "sz.002415": "海康威视"
}

__all__ = ['fetch_stock_data', 'DEFAULT_STOCKS', 'OUTPUT_DIR']

def fetch_stock_data(stocks=None, output_dir=None):
    """
    获取股票历史数据并保存为CSV
    
    Args:
        stocks: 字典{代码: 名称}，默认为 DEFAULT_STOCKS
        output_dir: 输出目录，默认为 OUTPUT_DIR
    """
    if stocks is None:
        stocks = DEFAULT_STOCKS
    if output_dir is None:
        output_dir = OUTPUT_DIR
    
    os.makedirs(output_dir, exist_ok=True)
    print(f"输出目录: {output_dir}")

    # 登录 baostock
    lg = bs.login()
    if lg.error_code != '0':
        print(f"登录失败: {lg.error_msg}")
        return False
    print("baostock 登录成功")

    login_success = True
    
    try:
        for code, name in stocks.items():
            try:
                rs = bs.query_history_k_data_plus(
                    code,
                    "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg",
                    start_date='2000-01-01',
                    end_date='2099-01-01',
                    frequency="d",
                    adjustflag="2"  # 2=前复权，1=后复权，3=不复权
                )
                if rs.error_code != '0':
                    print(f"{code}({name}) 查询失败: {rs.error_msg}")
                    continue

                data_list = []
                while rs.next():
                    data_list.append(rs.get_row_data())
                
                if not data_list:
                    print(f"{code}({name}) 无数据")
                    continue
                    
                df = pd.DataFrame(data_list, columns=rs.fields)

                # 数值列转 float
                for col in ["open", "high", "low", "close", "preclose", "volume", "amount", "turn", "pctChg"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")

                # 增加股票名称和代码列
                df["name"] = name
                df["code"] = code.split(".")[1]  # 去掉前缀，只保留代码

                # 日期列转 datetime，按降序排列（最新日期在前）
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date", ascending=False).reset_index(drop=True)

                # 列名改为中文，并调整顺序
                df = df.rename(columns={
                    "date": "日期",
                    "open": "开盘",
                    "high": "最高",
                    "low": "最低",
                    "close": "收盘",
                    "preclose": "前收盘",
                    "volume": "成交量",
                    "amount": "成交额",
                    "turn": "换手率",
                    "pctChg": "涨跌幅",
                })

                # 调整列顺序，让股票名称、代码在最前面
                col_order = ["日期", "股票名称", "代码", "开盘", "最高", "最低", "收盘", "前收盘", "成交量", "成交额", "换手率", "涨跌幅"]
                # 实际列名是英文，需要映射
                df = df.rename(columns={"name": "股票名称", "code": "代码"})
                final_cols = ["日期", "股票名称", "代码", "开盘", "最高", "最低", "收盘", "前收盘", "成交量", "成交额", "换手率", "涨跌幅"]
                df = df[[c for c in final_cols if c in df.columns]]

                # 文件名为股票名称
                filename = f"{name}.csv"
                df.to_csv(os.path.join(output_dir, filename), index=False, encoding="utf-8-sig")
                print(f"{code}({name}) 成功，共 {len(df)} 条")

            except Exception as e:
                print(f"{code}({name}) 失败: {e}")
                continue
        
        print("全部股票处理完成")
        
    except Exception as e:
        print(f"处理过程中发生错误: {e}")
        login_success = False
        
    finally:
        # 确保在函数结束时登出
        try:
            bs.logout()
            print("已退出登录")
        except Exception as e:
            print(f"登出时发生错误: {e}")
    
    return login_success

def main():
    """命令行入口"""
    fetch_stock_data()

if __name__ == "__main__":
    main()

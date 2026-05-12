import baostock as bs
import pandas as pd

print("环境正常")

print("登录 baostock...")
lg = bs.login()
print("登录状态:", lg.error_msg)

print("获取 000001（平安银行）日K数据...")
rs = bs.query_history_k_data_plus(
    "sh.000001",
    "date,code,open,high,low,close,volume,amount",
    start_date="2024-01-01",
    end_date="2024-01-10",
    frequency="d"
)
df = rs.get_data()
print(df)

bs.logout()
print("完成")

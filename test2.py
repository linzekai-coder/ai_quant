import akshare as ak
import pandas as pd

# 获取平安银行(000001) 日K数据
df = ak.stock_zh_a_hist(symbol="000001", period="daily", start_date="20240101", end_date="20241231", adjust="qfq")

# 打印前5行看看
print(df.head())

print("\n✅ 获取成功！数据量：", len(df))
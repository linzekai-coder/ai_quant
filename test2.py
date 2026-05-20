from data.tushare_provider import fetch_daily_price

# 获取平安银行(000001) 日K数据
df = fetch_daily_price("000001", start_date="20240101", end_date="20241231", adjust="qfq")

# 打印前5行看看
print(df.head())

print("\n✅ 获取成功！数据量：", len(df))

import sqlite3

import pandas as pd

conn = sqlite3.connect("quant.db")

df = pd.read_sql("""
SELECT * FROM stock_scores
ORDER BY id DESC
""", conn)

print(df)

conn.close()

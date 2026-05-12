import os
import sys

# 将当前文件所在目录添加到 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_data import fetch_stock_data

if __name__ == "__main__":
    fetch_stock_data()
    print("数据更新完成")

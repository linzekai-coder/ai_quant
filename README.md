# AI Quant

AI 股票评分、投资日报、Dashboard、模拟交易和风控系统。

## Setup

```powershell
python -m pip install -r requirements.txt
```

复制 `.env.example` 中的变量到当前终端环境或本地 `.env` 管理工具中。

## Common Commands

```powershell
python database/init_db.py
python run_report.py
python database/check_db.py
python -m streamlit run dashboard/app.py
python trading/signal.py
python trading/trader.py
```

交易模块默认只打印模拟动作，不会真实下单。Testnet 下单必须显式确认并配置环境变量。

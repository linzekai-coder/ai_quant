from datetime import datetime
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR in sys.path:
    sys.path.remove(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from binance.client import Client
from risk.risk_manager import MAX_DAILY_LOSS
from risk.trade_guard import check_single_order, validate_signal
from trading.signal import get_signal
from utils.logger import logger
from utils.notify import send_message


REAL_TRADING = False


def generate_trade_signal(symbol, score, level):
    if score >= 80:
        action = "BUY"
        reason = "评分较高，生成模拟买入指令"
    elif score >= 60:
        action = "WATCH"
        reason = "评分中等，继续观察"
    else:
        action = "AVOID"
        reason = "评分较低，暂不交易"

    return {
        "symbol": symbol,
        "score": score,
        "level": level,
        "action": action,
        "reason": reason,
        "mode": "SIMULATION",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def print_trade_signal(signal):
    logger.info("模拟交易指令: %s", signal)
    print("=== 模拟交易指令 ===")
    print(f"交易标的: {signal['symbol']}")
    print(f"评分: {signal['score']}")
    print(f"等级: {signal['level']}")
    print(f"动作: {signal['action']}")
    print(f"原因: {signal['reason']}")
    print(f"模式: {signal['mode']}")
    print(f"时间: {signal['created_at']}")


def connect_testnet():
    api_key = os.getenv("BINANCE_TESTNET_API_KEY")
    api_secret = os.getenv("BINANCE_TESTNET_API_SECRET")

    if not api_key or not api_secret:
        raise RuntimeError(
            "请先设置环境变量 BINANCE_TESTNET_API_KEY 和 BINANCE_TESTNET_API_SECRET"
        )

    client = Client(
        api_key,
        api_secret,
        testnet=True
    )

    account = client.get_account()

    logger.info("Binance Testnet 连接成功: %s", account["accountType"])
    print("连接成功")
    print(account["accountType"])

    balances = account["balances"]

    for b in balances:
        if float(b["free"]) > 0:
            print(b)

    return client


def buy_btc_testnet(client, amount=0.001):
    if not check_single_order(amount):
        print("超过最大下单金额")
        return None

    order = client.create_order(
        symbol="BTCUSDT",
        side="BUY",
        type="MARKET",
        quantity=amount
    )

    logger.info("Testnet 买入订单已提交: %s", order)
    print(order)
    return order


def execute_signal(signal):
    if signal == "BUY":
        logger.info("执行买入")
        print("执行买入")
    elif signal == "SELL":
        logger.info("执行卖出")
        print("执行卖出")
    else:
        logger.info("继续持有")
        print("继续持有")


def run_strategy():
    daily_loss = 0.03

    if daily_loss >= MAX_DAILY_LOSS:
        logger.error("禁止交易: daily_loss=%s, max_daily_loss=%s", daily_loss, MAX_DAILY_LOSS)
        print("禁止交易")
        sys.exit(0)

    signal = get_signal("000001")
    logger.info("策略信号: %s", signal)
    print(f"策略信号: {signal}")

    if not validate_signal(signal):
        print("非法交易信号")
        return

    execute_signal(signal)

    if "--confirm-buy" in sys.argv:
        if not REAL_TRADING:
            logger.info("当前模拟模式")
            print("当前模拟模式")
            return

        client = connect_testnet()
        buy_btc_testnet(client)


if __name__ == "__main__":
    try:
        run_strategy()
    except Exception as e:
        logger.error(str(e))
        send_message(f"系统错误: {e}")
        print(f"系统异常: {e}")

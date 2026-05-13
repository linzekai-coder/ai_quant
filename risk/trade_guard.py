from utils.logger import logger


MAX_SINGLE_ORDER = 10000
VALID_SIGNALS = [
    "BUY",
    "SELL",
    "HOLD"
]


def check_single_order(amount):
    if amount > MAX_SINGLE_ORDER:
        logger.error("超过最大下单金额")
        return False

    return True


def validate_signal(signal):
    if signal not in VALID_SIGNALS:
        logger.error("非法交易信号")
        return False

    return True

import time


MAX_POSITION_PERCENT = 0.1
MAX_DAILY_LOSS = 0.05
STOP_LOSS_PERCENT = 0.03
TAKE_PROFIT_PERCENT = 0.08
MAX_TRADES_PER_DAY = 5
COOLDOWN_SECONDS = 60
open_positions = []


def calculate_position(total_balance):
    return total_balance * MAX_POSITION_PERCENT


def should_stop_loss(entry_price, current_price):
    loss = (
        entry_price - current_price
    ) / entry_price

    return loss >= STOP_LOSS_PERCENT


def should_take_profit(
    entry_price,
    current_price
):
    profit = (
        current_price - entry_price
    ) / entry_price

    return profit >= TAKE_PROFIT_PERCENT


def should_stop_trading(daily_loss):
    return daily_loss >= MAX_DAILY_LOSS


def cooldown():
    print("进入冷却")
    time.sleep(COOLDOWN_SECONDS)


def can_buy(stock_code):
    if stock_code in open_positions:
        print("已有仓位")
        return False
    else:
        print("允许买入")
        return True


if __name__ == "__main__":
    daily_loss = 0.03

    if daily_loss >= MAX_DAILY_LOSS:
        print("今日停止交易")

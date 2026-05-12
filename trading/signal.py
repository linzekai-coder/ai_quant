import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPT_DIR in sys.path:
    sys.path.remove(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from strategy.score import calculate_score


def score_to_signal(score):
    if score >= 80:
        return "BUY"
    elif score <= 40:
        return "SELL"
    else:
        return "HOLD"


def get_signal(stock_code="000001"):
    score = calculate_score(stock_code)
    return score_to_signal(score)


if __name__ == "__main__":
    print(get_signal("000001"))

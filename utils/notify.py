import os

import requests

from utils.logger import logger


PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN")
PUSHPLUS_URL = "https://www.pushplus.plus/send"


def send_message(msg, title="AI量化系统告警"):
    if not PUSHPLUS_TOKEN:
        logger.warning("未设置 PUSHPLUS_TOKEN，跳过PushPlus告警")
        return False

    payload = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": msg,
        "template": "txt"
    }

    try:
        response = requests.post(PUSHPLUS_URL, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        if result.get("code") == 200:
            logger.info("PushPlus告警发送成功")
            return True

        logger.error("PushPlus告警发送失败: %s", result)
        return False
    except Exception as e:
        logger.error("PushPlus告警发送失败: %s", e)
        return False

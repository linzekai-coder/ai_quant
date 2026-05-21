from sqlalchemy import text

from database.connection import engine


DEFAULT_USER_SETTINGS = {
    "score.technical_weight": 0.5,
    "score.fundamental_weight": 0.2,
    "score.sentiment_weight": 0.3,
    "filter.min_amount_yi": 3.0,
    "filter.volume_ratio": 1.2,
    "filter.ma_condition": "站上MA20",
    "recommend.top_n": 5,
    "recommend.include_avoid": False,
    "recommend.enable_market_scan": True,
    "notify.email_enabled": False,
    "notify.score_change_threshold": 10,
    "layout.default_page": "首页总览",
}


def init_user_settings_table(conn=None):
    ddl = """
    CREATE TABLE IF NOT EXISTS user_settings (
        user_id INTEGER NOT NULL,
        setting_key TEXT NOT NULL,
        setting_value TEXT,
        PRIMARY KEY (user_id, setting_key)
    )
    """
    if conn is not None:
        conn.execute(ddl)
        return
    with engine.begin() as db:
        db.execute(text(ddl))


def _coerce_value(value, default):
    if isinstance(default, bool):
        return str(value).lower() in ("1", "true", "yes", "on")
    if isinstance(default, int) and not isinstance(default, bool):
        return int(float(value))
    if isinstance(default, float):
        return float(value)
    return value


def get_user_setting(user_id, setting_key, default=None):
    init_user_settings_table()
    with engine.connect() as db:
        row = db.execute(text("""
        SELECT setting_value
        FROM user_settings
        WHERE user_id = :user_id AND setting_key = :setting_key
        """), {"user_id": int(user_id), "setting_key": setting_key}).fetchone()
    return row[0] if row else default


def set_user_setting(user_id, setting_key, setting_value):
    init_user_settings_table()
    with engine.begin() as db:
        db.execute(text("""
        INSERT INTO user_settings (user_id, setting_key, setting_value)
        VALUES (:user_id, :setting_key, :setting_value)
        ON CONFLICT(user_id, setting_key) DO UPDATE SET
            setting_value = excluded.setting_value
        """), {
            "user_id": int(user_id),
            "setting_key": setting_key,
            "setting_value": str(setting_value),
        })


def list_user_settings(user_id):
    init_user_settings_table()
    with engine.connect() as db:
        rows = db.execute(text("""
        SELECT setting_key, setting_value
        FROM user_settings
        WHERE user_id = :user_id
        ORDER BY setting_key
        """), {"user_id": int(user_id)}).fetchall()
    return {row[0]: row[1] for row in rows}


def get_user_settings(user_id):
    saved = list_user_settings(user_id)
    settings = DEFAULT_USER_SETTINGS.copy()
    for key, value in saved.items():
        if key in settings:
            settings[key] = _coerce_value(value, settings[key])
        else:
            settings[key] = value
    return settings


def save_user_settings(user_id, settings):
    for key, value in settings.items():
        set_user_setting(user_id, key, str(value))


def reset_user_settings(user_id):
    init_user_settings_table()
    with engine.begin() as db:
        db.execute(text(
            "DELETE FROM user_settings WHERE user_id = :user_id"
        ), {"user_id": int(user_id)})

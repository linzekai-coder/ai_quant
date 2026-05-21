import os
import re
from datetime import datetime, timedelta

from sqlalchemy import text

try:
    import streamlit_authenticator as stauth
except ImportError:
    stauth = None

try:
    import bcrypt
except ImportError:
    bcrypt = None

from database.connection import engine, is_postgres


USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
MAX_LOGIN_FAILURES = 5
LOCK_MINUTES = 15


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_users_table(conn=None):
    id_type = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ddl = f"""
    CREATE TABLE IF NOT EXISTS users (
        id {id_type},
        username TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        email TEXT,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        is_active INTEGER NOT NULL DEFAULT 1,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        session_version INTEGER NOT NULL DEFAULT 0,
        failed_login_count INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT,
        created_at TEXT NOT NULL,
        last_login_at TEXT,
        remember_token_hash TEXT,
        remember_token_expires_at TEXT
    )
    """
    if conn is not None:
        conn.execute(ddl)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        return
    with engine.begin() as db:
        db.execute(text(ddl))
        try:
            db.execute(text("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0"))
        except Exception:
            pass


def hash_password(password):
    if stauth is not None:
        return stauth.Hasher.hash(password)
    if bcrypt is None:
        raise RuntimeError("缺少 bcrypt 依赖，请先安装 requirements.txt")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password, password_hash):
    if stauth is not None:
        return stauth.Hasher.check_pw(password, str(password_hash))
    if bcrypt is None:
        raise RuntimeError("缺少 bcrypt 依赖，请先安装 requirements.txt")
    return bcrypt.checkpw(password.encode("utf-8"), str(password_hash).encode("utf-8"))


def validate_username(username):
    return bool(USERNAME_RE.match(str(username or "")))


def validate_password_strength(password):
    text = str(password or "")
    return len(text) >= 8 and any(ch.isalpha() for ch in text) and any(ch.isdigit() for ch in text)


def _fetch_one(sql, params=None):
    init_users_table()
    with engine.connect() as db:
        row = db.execute(text(sql), params or {}).fetchone()
        return dict(row._mapping) if row else None


def _fetch_all(sql, params=None):
    init_users_table()
    with engine.connect() as db:
        return [dict(row._mapping) for row in db.execute(text(sql), params or {})]


def _execute(sql, params=None):
    init_users_table()
    with engine.begin() as db:
        return db.execute(text(sql), params or {}).rowcount


def get_user_by_username(username):
    return _fetch_one(
        "SELECT * FROM users WHERE username = :username",
        {"username": str(username).strip()},
    )


def get_user_by_id(user_id):
    return _fetch_one(
        "SELECT * FROM users WHERE id = :user_id",
        {"user_id": int(user_id)},
    )


def list_users():
    return _fetch_all("""
    SELECT id, username, display_name, email, role, is_active, created_at, last_login_at
    FROM users
    ORDER BY created_at DESC, id DESC
    """)


def set_user_active(user_id, is_active):
    _execute(
        "UPDATE users SET is_active = :is_active WHERE id = :user_id",
        {"is_active": int(is_active), "user_id": int(user_id)},
    )


def create_user(username, display_name, email, password, role="user", must_change_password=0):
    username = str(username).strip()
    display_name = str(display_name).strip()
    email = str(email or "").strip()
    if not validate_username(username):
        raise ValueError("用户名只允许字母、数字、下划线，长度 3-20 位")
    if not display_name:
        raise ValueError("昵称不能为空")
    if not validate_password_strength(password):
        raise ValueError("密码至少 8 位，且必须包含字母和数字")

    try:
        _execute("""
        INSERT INTO users (
            username,
            display_name,
            email,
            password_hash,
            role,
            is_active,
            must_change_password,
            session_version,
            created_at
        )
        VALUES (
            :username,
            :display_name,
            :email,
            :password_hash,
            :role,
            1,
            :must_change_password,
            0,
            :created_at
        )
        """, {
            "username": username,
            "display_name": display_name,
            "email": email,
            "password_hash": hash_password(password),
            "role": role,
            "must_change_password": int(must_change_password),
            "created_at": now_text(),
        })
    except Exception as exc:
        raise ValueError("用户名已存在") from exc
    return get_user_by_username(username)


def ensure_default_admin():
    init_users_table()
    row = _fetch_one("SELECT COUNT(*) AS count FROM users")
    if row and int(row["count"]) > 0:
        return None
    return create_user(
        username="admin",
        display_name="管理员",
        email="admin@example.local",
        password=os.getenv("ADMIN_PASSWORD") or "ChangeMe123",
        role="admin",
        must_change_password=1,
    )


def locked_until_datetime(user):
    value = user.get("locked_until")
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def record_login_failure(username):
    user = get_user_by_username(username)
    if not user:
        return
    failed_count = int(user.get("failed_login_count", 0) or 0) + 1
    locked_until = None
    if failed_count >= MAX_LOGIN_FAILURES:
        locked_until = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    _execute("""
    UPDATE users
    SET failed_login_count = :failed_count,
        locked_until = :locked_until
    WHERE username = :username
    """, {
        "failed_count": failed_count,
        "locked_until": locked_until,
        "username": username,
    })


def record_login_success(user_id):
    _execute("""
    UPDATE users
    SET failed_login_count = 0,
        locked_until = NULL,
        last_login_at = :last_login_at
    WHERE id = :user_id
    """, {"last_login_at": now_text(), "user_id": int(user_id)})


def authenticate_user(username, password):
    user = get_user_by_username(username)
    if not user:
        return None, "用户名或密码错误"
    if not int(user.get("is_active", 0)):
        return None, "账号已停用"
    locked_until = locked_until_datetime(user)
    if locked_until and locked_until > datetime.now():
        minutes = max(1, int((locked_until - datetime.now()).total_seconds() // 60) + 1)
        return None, f"账号已锁定，请 {minutes} 分钟后再试"
    if not verify_password(password, user["password_hash"]):
        record_login_failure(user["username"])
        return None, "用户名或密码错误"
    record_login_success(user["id"])
    return get_user_by_id(user["id"]), None


def update_user_password(user_id, password):
    if not validate_password_strength(password):
        raise ValueError("密码至少 8 位，且必须包含字母和数字")
    _execute("""
    UPDATE users
    SET password_hash = :password_hash,
        must_change_password = 0,
        session_version = session_version + 1,
        failed_login_count = 0,
        locked_until = NULL,
        remember_token_hash = NULL,
        remember_token_expires_at = NULL
    WHERE id = :user_id
    """, {"password_hash": hash_password(password), "user_id": int(user_id)})


def save_remember_token(user_id, token, expires_at):
    _execute("""
    UPDATE users
    SET remember_token_hash = :token_hash,
        remember_token_expires_at = :expires_at
    WHERE id = :user_id
    """, {
        "token_hash": hash_password(token),
        "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
        "user_id": int(user_id),
    })


def clear_remember_token(user_id):
    _execute("""
    UPDATE users
    SET remember_token_hash = NULL,
        remember_token_expires_at = NULL
    WHERE id = :user_id
    """, {"user_id": int(user_id)})


def authenticate_remember_token(user_id, token):
    user = get_user_by_id(user_id)
    if not user or not int(user.get("is_active", 0)):
        return None
    expires_at = user.get("remember_token_expires_at")
    token_hash = user.get("remember_token_hash")
    if not expires_at or not token_hash:
        return None
    try:
        expires_at_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if expires_at_dt <= datetime.now():
        clear_remember_token(user_id)
        return None
    if not verify_password(token, token_hash):
        return None
    return user

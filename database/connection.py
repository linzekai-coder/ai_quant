import os
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def get_database_url():
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    db_path = os.path.join(PROJECT_ROOT, "quant.db")
    return f"sqlite:///{db_path}"


DATABASE_URL = get_database_url()
DATABASE_DIALECT = make_url(DATABASE_URL).get_backend_name()

connect_args = {}
if DATABASE_DIALECT == "sqlite":
    connect_args["check_same_thread"] = False

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    future=True,
    connect_args=connect_args,
)


def is_postgres():
    return DATABASE_DIALECT.startswith("postgresql")


def placeholder():
    return "%s" if is_postgres() else "?"


@contextmanager
def raw_connection():
    conn = engine.raw_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_dbapi_connection():
    return engine.raw_connection()


def fetch_all(sql, params=None):
    with engine.connect() as conn:
        result = conn.execute(sql, params or {})
        return [dict(row._mapping) for row in result]


def fetch_one(sql, params=None):
    with engine.connect() as conn:
        result = conn.execute(sql, params or {}).fetchone()
        return dict(result._mapping) if result else None


def execute(sql, params=None):
    with engine.begin() as conn:
        result = conn.execute(sql, params or {})
        return result.rowcount

#!/usr/bin/env bash
set -euo pipefail

if [[ "${DATABASE_URL:-}" == postgresql* ]]; then
  python - <<'PY'
import os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"], future=True)
with open("deploy/postgres_schema.sql", "r", encoding="utf-8") as file_obj:
    schema_sql = file_obj.read()
with engine.begin() as conn:
    for statement in schema_sql.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(text(statement))
print("PostgreSQL schema initialized")
PY
  python - <<'PY'
from database.users import ensure_default_admin

admin = ensure_default_admin()
if admin:
    print("默认管理员已创建")
PY
else
  python database/init_db.py
fi
exec streamlit run dashboard/app.py \
  --server.address=0.0.0.0 \
  --server.port=8501 \
  --server.headless=true

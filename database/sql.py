from database.connection import is_postgres


def qmark(sql):
    if not is_postgres():
        return sql
    return sql.replace("?", "%s")


def rows_to_dicts(cursor):
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def row_to_dict(row, cursor=None):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    if cursor is None:
        try:
            return dict(row)
        except Exception:
            return None
    columns = [item[0] for item in cursor.description]
    return dict(zip(columns, row))

import os
from contextlib import contextmanager
from decimal import Decimal

import mysql.connector
import pandas as pd
from mysql.connector import Error, pooling

DB_HOST = os.getenv("DUTCHRAIL_DB_HOST", os.getenv("DB_HOST", "localhost"))
DB_PORT = int(os.getenv("DUTCHRAIL_DB_PORT", os.getenv("DB_PORT", "3306")))
DB_USER = os.getenv("DUTCHRAIL_DB_USER", os.getenv("DB_USER", "root"))
DB_PASSWORD = os.getenv("DUTCHRAIL_DB_PASSWORD", os.getenv("DB_PASSWORD", ""))
DB_NAME = os.getenv("DUTCHRAIL_DB_NAME", os.getenv("DB_NAME", "railway_dw"))

_POOL = None
_POOL_LOCK = __import__("threading").Lock()


def _pool_config():
    return {
        "pool_name": "dutchrail",
        "pool_size": 5,
        "pool_reset_session": True,
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "database": DB_NAME,
        "charset": "utf8mb4",
        "connection_timeout": 10,
    }


def _get_pool():
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = pooling.MySQLConnectionPool(**_pool_config())
    return _POOL


@contextmanager
def get_connection():
    conn = _get_pool().get_connection()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Error:
            pass


def test_connection():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
    return version


def reset_pool():
    global _POOL
    _POOL = None


def _frame(cursor, rows):
    columns = [col[0] for col in cursor.description] if cursor.description else []
    frame = pd.DataFrame(rows, columns=columns)
    for col in frame.columns:
        if frame[col].dtype == object:
            frame[col] = frame[col].map(
                lambda v: float(v) if isinstance(v, Decimal) else v
            )
    return frame


def run_query(sql, params=None):
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(sql, tuple(params) if params else ())
            rows = cursor.fetchall()
            return _frame(cursor, rows)
        finally:
            cursor.close()


def execute(sql, params=None):
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(sql, tuple(params) if params else ())
            conn.commit()
            return cursor.rowcount
        finally:
            cursor.close()


def fetch_scalar(sql, params=None):
    frame = run_query(sql, params)
    if frame.empty:
        return None
    return frame.iloc[0, 0]

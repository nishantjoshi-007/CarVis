# Pooled database access for the dashboard.
#
# A pool because Dash fires five callbacks per filter change and Postgres forks
# a backend process per connection. Degrades to the CSV instead of failing when
# there is no database. (decision.md D6, D15)

from __future__ import annotations

import atexit
import os
import threading
from typing import Any, LiteralString, cast

import pandas as pd
from psycopg_pool import ConnectionPool

# Set by Render (or by the local shell). Absent -> CSV fallback mode.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

_pool: ConnectionPool[Any] | None = None
_pool_lock = threading.Lock()
_availability: bool | None = None


def _get_pool() -> ConnectionPool[Any] | None:
    # open=False then opened explicitly, so a database that is down raises
    # here where we can catch it, not at import time.
    global _pool
    if _pool is not None:
        return _pool
    if not DATABASE_URL:
        return None

    with _pool_lock:
        if _pool is None:
            pool: ConnectionPool[Any] = ConnectionPool(
                DATABASE_URL,
                min_size=1,
                max_size=5,          # 5 concurrent chart callbacks
                timeout=10,
                open=False,
            )
            pool.open(wait=True, timeout=10)
            atexit.register(_close_pool)
            _pool = pool
    return _pool


def _close_pool() -> None:
    # Without this, psycopg_pool raises PythonFinalizationError on exit
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def is_available() -> bool:
    # Cached: this decides the data path for every callback, so re-probing on
    # each of five per interaction would be waste. Restart after fixing an outage.
    global _availability
    if _availability is not None:
        return _availability

    if not DATABASE_URL:
        _availability = False
        return False

    try:
        pool = _get_pool()
        if pool is None:
            _availability = False
            return False
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        _availability = True
    except Exception as exc:  # noqa: BLE001 -- any failure means "use the CSV"
        print(f"[carvis] database unavailable, falling back to CSV: {exc}", flush=True)
        _availability = False
    return _availability


def fetch_df(sql: str, params: dict | None = None) -> pd.DataFrame:
    # Parameters travel separately from the SQL text, so a value is never
    # parsed as SQL -- injection is structurally impossible, not escaped.
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("fetch_df called with no database configured")

    with pool.connection() as conn, conn.cursor() as cur:
        # cast: psycopg reserves the plain-string overload for LiteralString so
        # that runtime-built SQL stands out. Ours is assembled from the module
        # constants in queries.py; user values are bound, never interpolated.
        cur.execute(cast(LiteralString, sql), params or {})
        rows = cur.fetchall()
        if cur.description is None:
            raise RuntimeError("query returned no result set")
        columns = [d.name for d in cur.description]
    return pd.DataFrame(rows, columns=columns)

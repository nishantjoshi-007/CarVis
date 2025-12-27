"""
Database access for the dashboard: one pooled connection source, one helper
that runs SQL and hands back a DataFrame.

WHY A POOL
----------
Dash fires all five chart callbacks when a single filter moves. Without a pool
that is five connect/disconnect cycles per interaction -- and a PostgreSQL
connection is not a cheap object: the server forks a whole backend process for
each one. A pool opens a small number up front, lends them out, and queues
callers when they are all busy, which also stops the app exhausting the
server's max_connections (100 on Render's free tier) under concurrent users.

WHY IT DEGRADES INSTEAD OF FAILING
----------------------------------
If DATABASE_URL is unset, or Postgres is unreachable, is_available() returns
False and the dashboard falls back to reading the CSV (see queries.py). The
deployed app therefore cannot be broken by a database problem, and anyone who
clones the repo can run it with no database at all.
"""

from __future__ import annotations

import atexit
import os
import threading

import pandas as pd
from psycopg_pool import ConnectionPool

# Set by Render (or by the local shell). Absent -> CSV fallback mode.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()
_availability: bool | None = None


def _get_pool() -> ConnectionPool | None:
    """Create the pool on first use, once, even under concurrent callbacks.

    Built with open=False then opened explicitly so that a database which is
    down at import time raises here -- where we can catch it -- rather than at
    module import, which would take the whole app down with it.
    """
    global _pool
    if _pool is not None:
        return _pool
    if not DATABASE_URL:
        return None

    with _pool_lock:
        if _pool is None:
            pool = ConnectionPool(
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
    """Return the pool's connections to the server on shutdown.

    Without this, the pool's worker threads are still alive when the
    interpreter starts finalising and psycopg_pool raises a noisy
    PythonFinalizationError on the way out. Closing explicitly also releases
    the server-side backends immediately instead of leaving them to time out.
    """
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def is_available() -> bool:
    """True when the database can actually serve a query.

    Checked once and cached: this decides which data path every callback takes,
    and re-probing on each of five callbacks per interaction would be waste.
    Restart the app after fixing a database outage.
    """
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
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        _availability = True
    except Exception as exc:  # noqa: BLE001 -- any failure means "use the CSV"
        print(f"[carvis] database unavailable, falling back to CSV: {exc}", flush=True)
        _availability = False
    return _availability


def fetch_df(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Run a query and return the result as a DataFrame.

    Parameters are passed to the driver SEPARATELY from the SQL text, never
    interpolated into it. The value is therefore never parsed as SQL, which is
    what makes injection structurally impossible rather than merely escaped --
    and it handles quoting correctly for values like a model name containing an
    apostrophe.
    """
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("fetch_df called with no database configured")

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            rows = cur.fetchall()
            columns = [d.name for d in cur.description]
    return pd.DataFrame(rows, columns=columns)

"""
CarVis — measure what the indexes actually buy.

    python benchmark.py

For each representative dashboard query this drops the secondary indexes, runs
EXPLAIN ANALYZE, rebuilds them, and runs it again -- then prints the plan the
planner chose and the time it took, in both states.

WHY IT EXISTS
-------------
"I added indexes and it got faster" is a claim. This produces the evidence, on
this data. It also, deliberately, includes a query that indexes do NOT help --
because the honest finding is that an index only pays off when a filter is
selective, and a benchmark that only shows wins is marketing.

The PRIMARY KEY is never dropped: it is a correctness constraint, not a
performance index.
"""

from __future__ import annotations

import os
import re
import statistics
import sys
import time
from pathlib import Path

import psycopg

PROJECT_ROOT = Path(__file__).resolve().parent
INDEXES_PATH = PROJECT_ROOT / "db" / "indexes.sql"
DEFAULT_DSN = "postgresql://carvis:carvis@localhost:5432/carvis"

SECONDARY_INDEXES = [
    "idx_fact_prod_year", "idx_fact_manufacturer", "idx_fact_model",
    "idx_fact_category", "idx_fact_fuel_type", "idx_fact_manufacturer_year",
]

RUNS = 5  # median of N, so one noisy run cannot decide the result

QUERIES: list[tuple[str, str, str]] = [
    (
        "Selective: one manufacturer, 5-year window",
        "~2% of rows — the dashboard's most common filter",
        """
        SELECT COUNT(*), AVG(l.price_usd)
        FROM fact_car_listing l
        JOIN dim_manufacturer mf ON mf.manufacturer_id = l.manufacturer_id
        WHERE mf.manufacturer_name = 'LEXUS' AND l.prod_year BETWEEN 2015 AND 2020
        """,
    ),
    (
        "Bar chart: avg price by category",
        "the real dashboard query, filtered to three makes",
        """
        SELECT c.category_name, AVG(l.price_usd)
        FROM fact_car_listing l
        JOIN dim_category     c  ON c.category_id      = l.category_id
        JOIN dim_manufacturer mf ON mf.manufacturer_id = l.manufacturer_id
        WHERE mf.manufacturer_name IN ('BMW', 'HONDA', 'LEXUS')
        GROUP BY c.category_name
        """,
    ),
    (
        "Single rare year",
        "1.1% of rows — the best case for an index",
        "SELECT COUNT(*) FROM fact_car_listing WHERE prod_year = 1998",
    ),
    (
        "Broad filter (expected: no gain)",
        "74% of rows, and it reads price -- so the heap must be touched",
        "SELECT AVG(price_usd) FROM fact_car_listing WHERE prod_year >= 2010",
    ),
]


def plan_of(cur, sql: str) -> tuple[str, float]:
    """Run EXPLAIN ANALYZE and return (main scan node, execution ms)."""
    cur.execute("EXPLAIN (ANALYZE, TIMING ON) " + sql)
    lines = [r[0] for r in cur.fetchall()]
    text = "\n".join(lines)

    # Report the scan on fact_car_listing specifically. A join plan contains a
    # node per table, and the dimension scans are noise here -- the fact table
    # is the one the indexes are about.
    node = r"(Seq Scan|Index Scan|Index Only Scan|Bitmap Heap Scan|Bitmap Index Scan)"
    scan = next(
        (m.group(1) for line in lines
         if "fact_car_listing" in line and (m := re.search(node, line))),
        next((m.group(1) for line in lines if (m := re.search(node, line))), "?"),
    )
    ms = float(re.search(r"Execution Time: ([\d.]+) ms", text).group(1))
    return scan, ms


def measure(cur, sql: str) -> tuple[str, float]:
    cur.execute(sql)          # warm the cache so we compare plans, not disk luck
    cur.fetchall()
    results = [plan_of(cur, sql) for _ in range(RUNS)]
    return results[0][0], statistics.median(ms for _, ms in results)


def set_indexes(cur, present: bool) -> None:
    if present:
        cur.execute(INDEXES_PATH.read_text(encoding="utf-8"))
    else:
        for name in SECONDARY_INDEXES:
            cur.execute(f"DROP INDEX IF EXISTS {name}")
    cur.execute("ANALYZE fact_car_listing")   # stats must match reality in both states


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    print(f"benchmarking against {dsn.rsplit('@', 1)[-1]}  (median of {RUNS} runs)\n")

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            try:
                set_indexes(cur, present=False)
                without = {name: measure(cur, sql) for name, _, sql in QUERIES}

                set_indexes(cur, present=True)
                with_ix = {name: measure(cur, sql) for name, _, sql in QUERIES}
            finally:
                # Always leave the database in its normal, indexed state.
                set_indexes(cur, present=True)

    print(f"{'query':<44}{'no indexes':>22}{'with indexes':>24}{'change':>10}")
    print("-" * 100)
    for name, note, _ in QUERIES:
        b_plan, b_ms = without[name]
        a_plan, a_ms = with_ix[name]
        ratio = b_ms / a_ms if a_ms else 0
        verdict = f"{ratio:.1f}x" if ratio >= 1.15 else ("same" if ratio > 0.85 else f"{ratio:.1f}x")
        print(f"{name:<44}{b_plan + f' {b_ms:.2f}ms':>22}{a_plan + f' {a_ms:.2f}ms':>24}{verdict:>10}")
        print(f"  {note}")
    print("\nRead the PLAN column, not just the times. An index helps when a filter")
    print("is selective; when most of the table matches, PostgreSQL falls back to a")
    print("sequential scan on purpose, because random heap fetches for three quarters")
    print("of a table cost more than reading it straight through.")
    print("\n'Index Only Scan' means the query was answered from the index alone,")
    print("without touching the table at all -- possible only when every column it")
    print("needs is in the index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

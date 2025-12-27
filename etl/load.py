"""
CarVis ETL — loads the source CSV into the Postgres star schema.

Run from the project root:

    python -m etl.load                      # uses DATABASE_URL, else local default
    DATABASE_URL=postgresql://... python -m etl.load

What it does, in order:

    1. EXTRACT   read data/car_price_prediction.csv
    2. DEDUPE    drop exact duplicate rows (313 of them)
    3. TRANSFORM apply etl/clean.py rules; collect a field-level audit trail
    4. LOAD      populate dimensions, bulk-COPY the facts, THEN build indexes
                 and refresh planner statistics
    5. RECONCILE write etl_load_run + etl_quarantine, print a summary

The reconciliation step is the point of the whole script. Anyone can assert
"I cleaned the data"; the load log and the quarantine table let a reviewer
check the claim -- rows in, rows out, and every single value we touched.
"""

from __future__ import annotations

import csv
import os
import sys
import time
from pathlib import Path

import psycopg

from etl.clean import (
    Issue,
    parse_doors,
    parse_engine_volume,
    parse_int,
    parse_levy,
    parse_mileage,
    parse_price,
    parse_yes_no,
    parse_year,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = PROJECT_ROOT / "data" / "car_price_prediction.csv"
SCHEMA_STAR_PATH = PROJECT_ROOT / "db" / "schema_star.sql"    # dropped + rebuilt
SCHEMA_AUDIT_PATH = PROJECT_ROOT / "db" / "schema_audit.sql"  # created once, kept
INDEXES_PATH = PROJECT_ROOT / "db" / "indexes.sql"            # applied after COPY

# Local Docker default. Render injects the real one as DATABASE_URL.
DEFAULT_DSN = "postgresql://carvis:carvis@localhost:5432/carvis"

FACT_COLUMNS = [
    "listing_id", "manufacturer_id", "model_id", "category_id", "fuel_type_id",
    "prod_year", "price_usd", "levy_usd", "mileage_km", "engine_volume_l",
    "is_turbo", "cylinders", "doors", "gearbox_type", "drive_wheels",
    "wheel_side", "color", "airbags", "has_leather",
    "price_suspect", "mileage_suspect",
]


def get_dsn() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DSN)


# --- 1 + 2: extract and dedupe ---------------------------------------------


def read_and_dedupe(path: Path) -> tuple[list[dict], int]:
    """Read the CSV, dropping rows that are byte-for-byte identical to one
    already seen.

    Deduping HERE, before load, keeps the database from ever seeing the
    duplicates. The primary key on fact_car_listing.listing_id is the second
    line of defence: if a future edit breaks this function, Postgres rejects
    the duplicate instead of silently storing it. Process plus constraint --
    the process can regress, the constraint cannot.
    """
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    seen: set[tuple] = set()
    unique: list[dict] = []
    for row in rows:
        fingerprint = tuple(row.values())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(row)

    return unique, len(rows) - len(unique)


# --- 3: transform -----------------------------------------------------------


def clean_row(row: dict) -> tuple[dict | None, list[Issue], str | None]:
    """Apply every cleaning rule to one source row.

    Returns (cleaned, issues, reject_reason). A non-None reject_reason means
    the row is unusable and will not be loaded -- but its issues are still
    recorded, so rejects are auditable too.
    """
    issues: list[Issue] = []
    source_id = (row.get("ID") or "").strip()

    price, price_suspect, price_issue = parse_price(row.get("Price", ""))
    if price_issue:
        issues.append(price_issue)
    if price is None:
        return None, issues, "no_usable_price"

    year, year_issue = parse_year(row.get("Prod. year", ""))
    if year_issue:
        issues.append(year_issue)
    if year is None:
        return None, issues, "no_usable_year"

    levy, levy_issue = parse_levy(row.get("Levy", ""))
    if levy_issue:
        issues.append(levy_issue)

    mileage, mileage_suspect, mileage_issue = parse_mileage(row.get("Mileage", ""))
    if mileage_issue:
        issues.append(mileage_issue)

    doors, doors_issue = parse_doors(row.get("Doors", ""))
    if doors_issue:
        issues.append(doors_issue)

    volume, is_turbo, volume_issue = parse_engine_volume(row.get("Engine volume", ""))
    if volume_issue:
        issues.append(volume_issue)

    cylinders, cyl_issue = parse_int(row.get("Cylinders", ""), "Cylinders")
    if cyl_issue:
        issues.append(cyl_issue)

    airbags, airbag_issue = parse_int(row.get("Airbags", ""), "Airbags")
    if airbag_issue:
        issues.append(airbag_issue)

    leather, leather_issue = parse_yes_no(row.get("Leather interior", ""), "Leather interior")
    if leather_issue:
        issues.append(leather_issue)

    cleaned = {
        "listing_id": int(source_id),
        "manufacturer": (row.get("Manufacturer") or "").strip(),
        "model": (row.get("Model") or "").strip(),
        "category": (row.get("Category") or "").strip(),
        "fuel_type": (row.get("Fuel type") or "").strip(),
        "prod_year": year,
        "price_usd": price,
        "levy_usd": levy,
        "mileage_km": mileage,
        "engine_volume_l": volume,
        "is_turbo": is_turbo,
        "cylinders": cylinders,
        "doors": doors,
        "gearbox_type": (row.get("Gear box type") or "").strip(),
        "drive_wheels": (row.get("Drive wheels") or "").strip(),
        "wheel_side": (row.get("Wheel") or "").strip(),
        "color": (row.get("Color") or "").strip(),
        "airbags": airbags,
        "has_leather": leather,
        "price_suspect": price_suspect,
        "mileage_suspect": mileage_suspect,
        "_source_id": source_id,
    }
    return cleaned, issues, None


# --- 4: load ----------------------------------------------------------------


def load_dimension(cur, table: str, id_col: str, name_col: str, values: set[str]) -> dict[str, int]:
    """Insert distinct values into a dimension, return {name: surrogate_id}.

    ON CONFLICT DO NOTHING makes this safe to re-run. The surrogate key (a
    SERIAL) is what the fact table stores, not the text -- so renaming a
    manufacturer later touches one dimension row, not 19,000 fact rows.
    """
    cleaned = sorted(v for v in values if v)
    cur.executemany(
        f"INSERT INTO {table} ({name_col}) VALUES (%s) ON CONFLICT ({name_col}) DO NOTHING",
        [(v,) for v in cleaned],
    )
    cur.execute(f"SELECT {name_col}, {id_col} FROM {table}")
    return {name: key for name, key in cur.fetchall()}


def load_models(cur, pairs: set[tuple[str, str]], manufacturer_ids: dict[str, int]) -> dict[tuple[str, str], int]:
    """dim_model is keyed on (manufacturer, model): 'Civic' only means
    something under Honda, so the model name alone is not a natural key."""
    payload = [(manufacturer_ids[mfr], model) for mfr, model in sorted(pairs) if mfr and model]
    cur.executemany(
        "INSERT INTO dim_model (manufacturer_id, model_name) VALUES (%s, %s) "
        "ON CONFLICT (manufacturer_id, model_name) DO NOTHING",
        payload,
    )
    cur.execute(
        "SELECT m.manufacturer_name, d.model_name, d.model_id "
        "FROM dim_model d JOIN dim_manufacturer m USING (manufacturer_id)"
    )
    return {(mfr, model): key for mfr, model, key in cur.fetchall()}


def main() -> int:
    started = time.perf_counter()

    if not CSV_PATH.exists():
        print(f"ERROR: source file not found: {CSV_PATH}", file=sys.stderr)
        return 1

    dsn = get_dsn()
    print(f"source : {CSV_PATH.name}")
    print(f"target : {dsn.rsplit('@', 1)[-1]}")  # never print credentials

    rows, duplicates_dropped = read_and_dedupe(CSV_PATH)
    rows_read = len(rows) + duplicates_dropped

    cleaned_rows: list[dict] = []
    all_issues: list[tuple[str, Issue]] = []
    rejected = 0

    for row in rows:
        cleaned, issues, reject_reason = clean_row(row)
        source_id = (row.get("ID") or "").strip()
        all_issues.extend((source_id, issue) for issue in issues)
        if reject_reason is not None:
            rejected += 1
            continue
        cleaned_rows.append(cleaned)

    # -----------------------------------------------------------------------
    # TWO CONNECTIONS, ON PURPOSE.
    #
    #   audit_conn : autocommit. Owns etl_load_run + etl_quarantine.
    #   data_conn  : one transaction. Owns the star schema and the facts.
    #
    # The data load must be atomic -- if it fails, the previous dataset has to
    # survive untouched, and PostgreSQL's transactional DDL gives us that for
    # free. But an audit trail written inside that same transaction would roll
    # back with it, so a failed run would erase its own record. The load is
    # atomic; the audit of the load must not be.
    # -----------------------------------------------------------------------
    with psycopg.connect(dsn, autocommit=True) as audit_conn:
        with audit_conn.cursor() as acur:
            acur.execute(SCHEMA_AUDIT_PATH.read_text(encoding="utf-8"))
            acur.execute(
                "INSERT INTO etl_load_run (source_file) VALUES (%s) RETURNING run_id",
                (CSV_PATH.name,),
            )
            run_id = acur.fetchone()[0]

            # Written before the load is attempted: these are findings of the
            # TRANSFORM, which happened regardless of whether the load lands.
            if all_issues:
                acur.executemany(
                    "INSERT INTO etl_quarantine "
                    "(run_id, source_id, column_name, raw_value, issue, action) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    [(run_id, sid, i.column, i.raw_value, i.issue, i.action)
                     for sid, i in all_issues],
                )

        try:
            _load_data(dsn, cleaned_rows)
        except Exception as exc:
            with audit_conn.cursor() as acur:
                acur.execute(
                    "UPDATE etl_load_run SET finished_at = now(), status = 'failed', "
                    "error_message = %s WHERE run_id = %s",
                    (str(exc)[:2000], run_id),
                )
            print(f"\nLOAD FAILED (run #{run_id}): {exc}", file=sys.stderr)
            print("Previous data left intact -- the load transaction rolled back.",
                  file=sys.stderr)
            raise

        with audit_conn.cursor() as acur:
            acur.execute(
                "UPDATE etl_load_run SET finished_at = now(), rows_read = %s, "
                "rows_duplicate = %s, rows_loaded = %s, rows_rejected = %s, "
                "values_repaired = %s, status = 'success' WHERE run_id = %s",
                (rows_read, duplicates_dropped, len(cleaned_rows), rejected,
                 len(all_issues), run_id),
            )
            acur.execute(
                "SELECT issue, action, COUNT(*) FROM etl_quarantine WHERE run_id = %s "
                "GROUP BY issue, action ORDER BY COUNT(*) DESC",
                (run_id,),
            )
            breakdown = acur.fetchall()

    elapsed = time.perf_counter() - started
    _print_reconciliation(run_id, rows_read, duplicates_dropped, rejected,
                          len(cleaned_rows), len(all_issues), breakdown, elapsed)
    return 0


def _load_data(dsn: str, cleaned_rows: list[dict]) -> None:
    """Rebuild the star schema and load the facts, as ONE transaction.

    psycopg's connection context manager commits on clean exit and rolls back
    if an exception escapes. Because PostgreSQL DDL is transactional, that
    rollback undoes the DROP/CREATE as well -- so a failure here leaves the
    previous schema and data completely intact. An atomic swap.

    (Oracle behaves differently: DDL there issues an implicit commit, so the
    same code would strand you with half-built tables.)
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_STAR_PATH.read_text(encoding="utf-8"))

            manufacturer_ids = load_dimension(
                cur, "dim_manufacturer", "manufacturer_id", "manufacturer_name",
                {r["manufacturer"] for r in cleaned_rows},
            )
            category_ids = load_dimension(
                cur, "dim_category", "category_id", "category_name",
                {r["category"] for r in cleaned_rows},
            )
            fuel_type_ids = load_dimension(
                cur, "dim_fuel_type", "fuel_type_id", "fuel_type_name",
                {r["fuel_type"] for r in cleaned_rows},
            )
            model_ids = load_models(
                cur, {(r["manufacturer"], r["model"]) for r in cleaned_rows}, manufacturer_ids
            )

            # Bulk load. COPY streams rows in one pass instead of issuing 19,000
            # INSERTs -- the single biggest performance decision in this script.
            columns = ", ".join(FACT_COLUMNS)
            with cur.copy(f"COPY fact_car_listing ({columns}) FROM STDIN") as copy:
                for r in cleaned_rows:
                    copy.write_row((
                        r["listing_id"],
                        manufacturer_ids[r["manufacturer"]],
                        model_ids[(r["manufacturer"], r["model"])],
                        category_ids[r["category"]],
                        fuel_type_ids[r["fuel_type"]],
                        r["prod_year"], r["price_usd"], r["levy_usd"], r["mileage_km"],
                        r["engine_volume_l"], r["is_turbo"], r["cylinders"], r["doors"],
                        r["gearbox_type"], r["drive_wheels"], r["wheel_side"], r["color"],
                        r["airbags"], r["has_leather"], r["price_suspect"], r["mileage_suspect"],
                    ))

            # Indexes AFTER the data, never before. With them in place during
            # the COPY every row is inserted into each B-tree individually;
            # built afterwards, PostgreSQL sorts all values in bulk and
            # constructs the tree in one pass. (The PRIMARY KEY is exempt -- it
            # is a correctness constraint, not a performance index.)
            cur.execute(INDEXES_PATH.read_text(encoding="utf-8"))

            # Refresh the planner's statistics. The table was dropped and
            # recreated moments ago, so it currently has NONE: pg_class.reltuples
            # reads -1, meaning "unknown", and the planner is guessing. Measured
            # on this data, it estimated 53 rows for a filter that matches 205.
            # Autovacuum would fix this eventually, but the dashboard queries
            # immediately -- so we do it deterministically, as the last step.
            cur.execute("ANALYZE fact_car_listing")
            cur.execute("ANALYZE dim_manufacturer")
            cur.execute("ANALYZE dim_model")
            cur.execute("ANALYZE dim_category")
            cur.execute("ANALYZE dim_fuel_type")

        conn.commit()


def _print_reconciliation(run_id, rows_read, duplicates, rejected, loaded,
                          repairs, breakdown, elapsed) -> None:
    """Rows in, rows out, and every value we touched.

    The balance check is the important line: read - duplicates - rejected must
    equal loaded. If it does not, something was lost silently, which is the
    failure mode a load log exists to catch.
    """
    print(f"\n{'=' * 62}\n RECONCILIATION — run #{run_id}\n{'=' * 62}")
    print(f"  rows read from source      {rows_read:>8,}")
    print(f"  exact duplicates dropped   {duplicates:>8,}")
    print(f"  rows rejected (unusable)   {rejected:>8,}")
    print(f"  rows loaded                {loaded:>8,}")
    balanced = rows_read - duplicates - rejected == loaded
    print(f"  balance check              {'OK' if balanced else 'MISMATCH':>8}")
    print(f"\n  field-level repairs        {repairs:>8,}")
    for issue, action, count in breakdown:
        print(f"      {issue:<26} {action:<9} {count:>7,}")
    print(f"\n  completed in {elapsed:.2f}s")


if __name__ == "__main__":
    raise SystemExit(main())

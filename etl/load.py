# CarVis ETL: CSV -> clean -> Postgres. Run from the project root.
#
#   python -m etl.load
#   DATABASE_URL=postgresql://... python -m etl.load
#
# extract -> dedupe -> transform -> load -> reconcile. Rationale: decision.md.

from __future__ import annotations

import csv
import os
import sys
import time
from pathlib import Path
from typing import LiteralString, cast

import psycopg

from etl.clean import (
    Issue,
    parse_doors,
    parse_engine_volume,
    parse_int,
    parse_levy,
    parse_mileage,
    parse_price,
    parse_year,
    parse_yes_no,
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
    # 313 exact duplicates. The PK on listing_id is the backstop if this
    # breaks: cleaning is a process and processes regress, a constraint cannot.
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
    # Returns (cleaned, issues, reject_reason). A reject still reports its
    # issues, so rejected rows stay auditable.
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
    # Returns {name: surrogate_id}. The fact table stores the key, not the
    # text, so renaming a manufacturer touches one row rather than 19,000.
    cleaned = sorted(v for v in values if v)
    # cast: psycopg types execute() to LiteralString so runtime-built SQL is
    # flagged. Table and column names here are module constants, never user
    # input -- values always travel as bound parameters.
    cur.executemany(
        cast(LiteralString,
             f"INSERT INTO {table} ({name_col}) VALUES (%s) "
             f"ON CONFLICT ({name_col}) DO NOTHING"),
        [(v,) for v in cleaned],
    )
    cur.execute(cast(LiteralString, f"SELECT {name_col}, {id_col} FROM {table}"))
    return dict(cur.fetchall())


def load_models(cur, pairs: set[tuple[str, str]],
                manufacturer_ids: dict[str, int]) -> dict[tuple[str, str], int]:
    # Keyed on (manufacturer, model): 'Civic' only means something under Honda.
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
        cleaned, issues, _reject_reason = clean_row(row)
        source_id = (row.get("ID") or "").strip()
        all_issues.extend((source_id, issue) for issue in issues)
        if cleaned is None:
            rejected += 1
            continue
        cleaned_rows.append(cleaned)

    # Two connections on purpose: the load is atomic, the audit of the load
    # must not be, or a failed run rolls back its own record. (D13)
    with psycopg.connect(dsn, autocommit=True) as audit_conn:
        with audit_conn.cursor() as acur:
            acur.execute(cast(LiteralString, SCHEMA_AUDIT_PATH.read_text(encoding="utf-8")))
            acur.execute(
                "INSERT INTO etl_load_run (source_file) VALUES (%s) RETURNING run_id",
                (CSV_PATH.name,),
            )
            row = acur.fetchone()
            if row is None:
                raise RuntimeError("INSERT ... RETURNING produced no row")
            run_id = row[0]

            # Findings of the transform, true whether or not the load lands
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
    # One transaction. PostgreSQL DDL is transactional, so a failure rolls
    # back the DROP/CREATE too and the previous data survives -- an atomic
    # swap. Oracle would differ: DDL there issues an implicit commit. (D13)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(cast(LiteralString, SCHEMA_STAR_PATH.read_text(encoding="utf-8")))

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

            # COPY streams in one pass instead of 19,000 INSERTs
            columns = ", ".join(FACT_COLUMNS)
            copy_sql = cast(LiteralString, f"COPY fact_car_listing ({columns}) FROM STDIN")
            with cur.copy(copy_sql) as copy:
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

            # Indexes after the data: bulk build beats per-row maintenance.
            # The PK is exempt -- it is a constraint, not a perf index. (D18)
            cur.execute(cast(LiteralString, INDEXES_PATH.read_text(encoding="utf-8")))

            # The table was recreated moments ago and has no statistics
            # (reltuples = -1), so the planner guesses. Autovacuum is too late:
            # the dashboard queries immediately. (D18)
            cur.execute("ANALYZE fact_car_listing")
            cur.execute("ANALYZE dim_manufacturer")
            cur.execute("ANALYZE dim_model")
            cur.execute("ANALYZE dim_category")
            cur.execute("ANALYZE dim_fuel_type")

        conn.commit()


def _print_reconciliation(run_id, rows_read, duplicates, rejected, loaded,
                          repairs, breakdown, elapsed) -> None:
    # Balance check is the line that matters: read - duplicates - rejected
    # must equal loaded, or something was lost silently.
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

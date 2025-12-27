# CarVis — Code Flow

What runs, in what order, and what calls what. Written so the project can be
picked back up cold.

Reasoning behind these choices lives in [`decision.md`](decision.md).

---

## Repository map

```
CarVis/
├── app.py                 ← ENTRY POINT 1: the Dash web app (callbacks only)
├── src/
│   ├── db.py              connection pool + fetch_df(); decides db-vs-CSV
│   ├── queries.py         every question the dashboard asks; SQL + CSV fallback
│   ├── layout.py          page layout; builds the control panel + chart slots
│   ├── slider.py          production-year range slider
│   └── checklist.py       fuel-type checklist
├── data/
│   └── car_price_prediction.csv    source data, 19,237 rows, 18 columns
├── db/
│   ├── schema_star.sql    DATA tables — dropped and rebuilt every run
│   ├── schema_audit.sql   CONTROL tables — created once, never dropped
│   └── indexes.sql        secondary indexes — applied AFTER the fact COPY
├── etl/
│   ├── __init__.py
│   ├── clean.py           pure cleaning rules — no I/O
│   └── load.py            ← ENTRY POINT 2: CSV → clean → Postgres
├── ml/
│   └── price_model.py     ← ENTRY POINT 3: trains the price forecaster
├── tests/
│   ├── test_clean.py      24 unit tests over the pure cleaning rules
│   └── test_price_model.py  11 tests, incl. guards against target leakage
├── benchmark.py           measures what the indexes actually buy (EXPLAIN)
├── start-carvis.sh        one-command local bootstrap
├── requirements.txt       runtime dependencies
├── requirements-dev.txt   pytest
├── decision.md            why the code is the way it is
├── quiz.md                interview prep: questions, answers, corrections
└── flow.md                this file
```

**Original coursework:** `app.py` (callbacks), `src/layout.py`, `src/slider.py`,
`src/checklist.py`, `data/`.
**New database layer:** `db/`, `etl/`, `src/db.py`, `src/queries.py`, `tests/`,
`benchmark.py`, `start-carvis.sh`.

---

## Entry point 1 — the web app

```
gunicorn app:server          # production (Render, and locally)
python app.py                # development, with the Dash dev server
```

`app.py` exposes `server = app.server` — the underlying Flask object. Gunicorn
serves that; `app.run_server()` is only for local development.

### Flow (after Phase 2)

```
app.py (import time)
  └─ queries.get_filter_options()   ──► reads the DIMENSION tables (or the CSV)
  └─ create_layout(...)             ──►  src/layout.py
                                           ├─ src/slider.py      year range slider
                                           └─ src/checklist.py   fuel-type checklist
  └─ prints which data path is active

user changes any filter
  └─ Dash fires all 5 callbacks in parallel
       ├─ update_scatter_plot()  ──► queries.scatter_mileage_price()
       ├─ update_pie_chart()     ──► queries.count_by_manufacturer()     [aggregated in SQL]
       ├─ update_bar_chart()     ──► queries.avg_price_by_category()     [aggregated in SQL]
       ├─ update_box_plot()      ──► queries.price_by_manufacturer()
       └─ update_histogram()     ──► queries.mileage_distribution()
                                          each one ▼
                                     db.is_available()?
                                       ├─ yes ─► db.fetch_df(sql, params)
                                       │           └─ pooled connection, bound params
                                       └─ no  ─► _csv_filtered()  (the original pandas path)
```

**What changed and why it matters:**

- The whole dataset is no longer loaded into a module-scope DataFrame, so each
  gunicorn worker no longer carries its own full copy in RAM.
- The bar and pie charts are aggregated by the database: 11 and ~65 rows come
  back instead of ~18,900.
- Filtering happens in a `WHERE` clause, not a boolean mask.
- The `" km"` string parsing is gone from the request path entirely — it now
  happens once, at load time, in `etl/clean.py`.
- The IQR fence is now `percentile_cont` in SQL, recomputed per query so it
  still describes the currently-viewed subset.

**The app never fails because of the database.** If `DATABASE_URL` is unset or
Postgres is unreachable, `db.is_available()` returns False (once, cached) and
every query takes the original pandas path.

---

## Entry point 2 — the ETL loader

```
python -m etl.load                       # localhost Postgres via the default DSN
DATABASE_URL=postgresql://... python -m etl.load
```

Run from the project root — `-m` matters, because `load.py` imports `etl.clean`
as a package.

### Execution order

```
etl/load.py :: main()
│
├─ 1. EXTRACT ─ read_and_dedupe(CSV_PATH)
│      • csv.DictReader over 19,237 rows
│      • drops rows byte-identical to one already seen  → 313 removed
│      • returns (unique_rows, duplicate_count)
│
├─ 2. TRANSFORM ─ clean_row(row) for each row
│      calls, in order, from etl/clean.py:
│         parse_price()           → value, suspect flag, issue   (REJECTS row if unusable)
│         parse_year()            → value, issue                 (REJECTS row if unusable)
│         parse_levy()            → value or None
│         parse_mileage()         → value or None, suspect flag
│         parse_doors()           → repaired value
│         parse_engine_volume()   → volume + is_turbo (splits one column into two)
│         parse_int()             → cylinders, airbags
│         parse_yes_no()          → has_leather
│      every rule returns an optional Issue(column, raw_value, issue, action);
│      issues accumulate for the audit trail, including for rejected rows
│
├─ 3. AUDIT CONNECTION ─ psycopg.connect(dsn, autocommit=True)
│      • executes db/schema_audit.sql   (IF NOT EXISTS + forward-only ALTERs)
│      • INSERT INTO etl_load_run → run_id      status defaults to 'running'
│      • INSERT the quarantine rows            findings of the TRANSFORM, which
│        happened whether or not the load lands
│      autocommit is the point: these writes must survive a rollback of step 4
│
├─ 4. DATA LOAD ─ _load_data(): psycopg.connect(dsn), ONE transaction
│      │    a failure anywhere below rolls back everything, including the DDL,
│      │    leaving the previous dataset intact — an atomic swap
│      │
│      ├─ SCHEMA ─ execute db/schema_star.sql   (drops + recreates data tables)
│      │
│      ├─ DIMENSIONS
│      │    load_dimension() ×3 → dim_manufacturer (65), dim_category (11),
│      │                          dim_fuel_type (7)
│      │    load_models()       → dim_model (1,601), keyed on (manufacturer_id, model_name)
│      │    each returns a {natural key → surrogate id} map used by the fact load
│      │
│      ├─ FACT ─ COPY fact_car_listing (...) FROM STDIN
│      │    18,924 rows in one statement; natural keys swapped for surrogate ids
│      │
│      ├─ INDEXES ─ execute db/indexes.sql
│      │    AFTER the COPY, never before: an index present during a bulk load is
│      │    maintained row by row, whereas building it afterwards sorts every
│      │    value once and constructs the tree in a single pass. The PRIMARY KEY
│      │    is exempt — it is a constraint, not a performance index.
│      │
│      └─ ANALYZE ─ on the fact table and all four dimensions
│           the table was dropped and recreated moments ago, so it has NO
│           statistics: pg_class.reltuples reads -1 and the planner guesses
│           (measured: estimated 53 rows for a filter matching 205). Autovacuum
│           would fix this eventually; the dashboard queries immediately.
│
├─ 5. CLOSE RUN ─ on the AUDIT connection
│      success → UPDATE etl_load_run SET counts, status='success'
│      failure → UPDATE etl_load_run SET status='failed', error_message=…
│                then re-raise. The failure is recorded; the data is untouched.
│
└─ 6. RECONCILE ─ print the summary; balance check asserts
      rows_read − duplicates − rejected == rows_loaded
```

### What a run prints

```
  rows read from source        19,237
  exact duplicates dropped        313
  rows rejected (unusable)          0
  rows loaded                  18,924
  balance check                    OK

  field-level repairs          25,656
      excel_date_coercion        repaired   18,800
      missing_marker             nulled      5,709
      zero_means_missing         nulled        714
      implausibly_low            flagged       349
      implausibly_high           flagged        67
      zero_or_negative           nulled         10
      int32_max_sentinel         nulled          7
```

---

## Database objects

| Table | Rows | Role |
|---|---:|---|
| `dim_manufacturer` | 65 | make |
| `dim_model` | 1,601 | model, unique per manufacturer |
| `dim_category` | 11 | Sedan, Jeep, Hatchback … |
| `dim_fuel_type` | 7 | Petrol, Diesel, Hybrid … |
| `fact_car_listing` | 18,924 | one row per listing; PK = source ID |
| `etl_load_run` | 1 per run | reconciliation counts |
| `etl_quarantine` | 25,656 | every value changed, and why |

**Foreign keys:** `fact_car_listing` → all four dimensions.
**Indexes:** primary keys and unique constraints only. Foreign-key columns are
deliberately unindexed until Phase 3 (see `decision.md` D10).

---

## Phase plan

| Phase | Scope | Status |
|---|---|---|
| **1** | Star schema + ETL + docs | **done** |
| **1.5** | Split audit tables onto their own transaction (D13, D14) | **done** |
| **2** | Query layer, connection pool, rewire callbacks, CSV fallback (D15, D16) | **done** |
| **2.5** | `start-carvis.sh` + `requirements.txt` for the DB packages (D17) | **done** |
| **3** | Indexes after load, `ANALYZE`, `benchmark.py` (D18) | **done** |
| **4** | 24 pytest tests over `etl/clean.py` (D19) | **done** |
| **5** | scikit-learn price model + predicted-vs-actual chart (D20) | **done** |
| **6** | Deploy to Render Postgres | optional — demo runs locally |

### Verified behaviour

| Claim | How it was checked |
|---|---|
| Load is atomic | Injected a PK collision mid-`COPY`; all 18,924 previous rows survived |
| Failures are recorded | The same run appears as `status='failed'` with the database's own error message |
| History accumulates | `etl_load_run` retains every run across reloads |
| Both data paths work | Ran the five queries with and without `DATABASE_URL` |
| Row-count difference is explained | 1,407 CSV − 59 duplicates − 58 zero-mileage = 1,290 in the database |
| Statistics really are absent after a load | `pg_class.reltuples = -1`; estimate 53 rows vs 205 actual. After `ANALYZE`: 13,939 vs 13,938 |
| Indexes help selective filters | 1.1% filter: Seq Scan 2.22 ms → Index Only Scan 0.07 ms (32x) |
| Indexes are correctly ignored otherwise | 74% filter: Seq Scan in both states, identical time |
| Cleaning rules behave as specified | 24 pytest tests, all passing |
| Model has no target leakage | asserted in tests; `levy_usd`, `model_name`, `price_usd` all excluded from features |
| Model beats a median baseline | MAE $12,019 → $5,904 on a held-out 20% |
| Prediction chart matches the data | 361 points plotted for BMW/Petrol/2010-2020; SQL count agrees |

---

## Running it locally

One command does everything — checks the environment, starts Postgres, loads
the data if the database is empty, and serves the app:

```bash
./start-carvis.sh
```

| Flag | Effect |
|---|---|
| `--reload` | rebuild the database from the CSV before starting |
| `--no-db` | skip Postgres entirely; run on the CSV fallback |
| `--port 8080` | serve somewhere other than 8000 |
| `--stop` | stop the database container |

`start-carvis.sh` **checks** prerequisites and never installs them: every
failure exits with the exact command or link needed to fix it. Installing
Docker or Python on someone's behalf needs sudo, differs per distro, and
changes their machine in ways they did not ask for.

It also polls `pg_isready` instead of sleeping a fixed number of seconds —
the container starts listening before the server can actually answer queries,
and a fixed sleep is either too short on a cold start or wasted time on a warm
one.

### Doing it by hand

```bash
docker start carvis-pg          # or docker run … postgres:17 (see the script)
./.venv/bin/python -m etl.load
DATABASE_URL=postgresql://carvis:carvis@localhost:5432/carvis \
    ./.venv/bin/gunicorn app:server

docker exec -it carvis-pg psql -U carvis -d carvis    # inspect
```

Local connection string: `postgresql://carvis:carvis@localhost:5432/carvis`

# CarVis — Decision Log

Why the code is the way it is. Each entry records the decision, what else was
on the table, and the reasoning — including the cases where a different context
would justify a different answer.

Read this alongside [`flow.md`](flow.md), which covers *what calls what*.

---

## Context

CarVis began as a Dash dashboard reading a 19,237-row CSV directly into pandas.
It is being extended with a PostgreSQL storage layer so the data is cleaned,
modelled, and queried in the database rather than re-parsed on every callback.

**The original project** (`app.py`, `src/`, the IQR outlier filter, the five
linked charts) is unchanged coursework. **Everything under `db/` and `etl/`**
is the new layer.

---

## D1 — PostgreSQL over SQLite

**Decision:** PostgreSQL 17, in Docker locally, managed Postgres on Render.

**Alternatives:** SQLite (zero setup, a single file); DuckDB (excellent for
analytics of exactly this shape).

**Why:** deployment decided it. The app runs on Render, where a web service's
filesystem is ephemeral — a SQLite file would be wiped on every redeploy, so
the database would silently reset. Render offers managed Postgres, which makes
this a configuration change rather than a platform change.

DuckDB is arguably the better *technical* fit for a read-only analytical
workload at this size, and it is worth naming as a road not taken. Postgres
wins here because it survives redeploys and because client-server RDBMS
semantics — connections, pooling, EXPLAIN, constraints — are what this layer
exists to demonstrate.

**Tradeoff:** more moving parts than a file. Mitigated by D6.

---

## D2 — Raw parameterised SQL, no ORM

**Decision:** queries are written as SQL text, with parameters bound by the
driver.

**Alternatives:** SQLAlchemy ORM; SQLAlchemy Core query builder; pandas
`read_sql` with generated SQL.

**Why:** this is a read-only analytical workload. Every query is a filtered
aggregation that feeds a chart, and the result goes straight into a DataFrame.
There are no domain objects with a lifecycle — no create/update/delete, no
identity map, no lazy-loaded relationships — which is precisely what an ORM
exists to manage. Using one here would mean writing `GROUP BY` aggregations
through an abstraction designed for row-at-a-time object manipulation.

Writing the SQL directly also keeps the query shape under our control: we can
run `EXPLAIN` on the exact statement the app sends, see which index the planner
chose, and tune it. ORM-generated SQL is a layer of indirection between the
code and the plan.

**When the answer flips:** if CarVis grew user accounts, saved searches, or any
write path, an ORM would start earning its keep — migrations, validation, and
safety on CRUD are real benefits. The deciding question is not "ORM or SQL", it
is "does this application manage entity lifecycles, or does it read and
aggregate?" This one reads and aggregates.

**Not a security tradeoff:** every parameter is bound by the driver, never
interpolated into the string.

---

## D3 — psycopg3 (`psycopg`), not psycopg2

**Decision:** psycopg 3.3, with `psycopg_pool` for connection pooling.

**Revised.** The first plan called for `psycopg2-binary`. Changed for three
reasons:

1. **Python 3.14.** psycopg2 is in maintenance mode and its binary wheels lag
   new interpreter releases. psycopg3 installed cleanly on 3.14.4 first try.
2. **Native `COPY` support.** The loader streams 18,924 rows through a single
   `COPY` instead of issuing that many `INSERT`s — see D8.
3. **It is the actively developed successor**, with better type adaptation and
   server-side parameter binding.

**Tradeoff:** psycopg2 has a decade more material written about it. That is a
reason to expect familiarity, not a reason to choose a library in maintenance
mode for new code.

---

## D4 — Star schema (one fact, four dimensions)

**Decision:** `fact_car_listing` referencing `dim_manufacturer`, `dim_model`,
`dim_category`, `dim_fuel_type`.

**Alternatives:** one wide flat table; full third-normal-form; a junk dimension
for the low-cardinality descriptive columns.

**Why *not* performance:** at 18,924 rows, a flat table would be marginally
*faster*, because joins cost work. Anyone claiming a star schema speeds up a
19k-row dataset is repeating something they read. The real reasons:

- **Integrity.** `LEXUS` is stored once, not 19,000 times. A misspelling is a
  one-row fix.
- **Consistency.** The dashboard's filter lists are read from the dimensions,
  so the options offered can never drift from the facts stored.
- **Extensibility.** `dim_manufacturer` can gain a country or luxury-tier
  column without rewriting the fact table.

**Why `dim_model` is keyed on (manufacturer, model):** "Civic" only means
something under Honda. The source has 1,590 distinct model *strings* but 1,601
distinct manufacturer–model *pairs* — that gap is names reused across makers,
and it is exactly why the model name alone is not a natural key.

**Deliberately left flat:** `gearbox_type`, `drive_wheels`, `wheel_side`,
`color`, `doors` sit on the fact table as text. They are low-cardinality
descriptive attributes that nothing joins on. A junk dimension is the textbook
move and would be defensible; four more tables for four filters nobody uses
would trade real clarity for theoretical purity.

---

## D5 — NULL for corrupt values, flags for implausible ones

**Decision:** a value that was never real becomes `NULL`. A value that is real
but extreme is kept and flagged.

| Source problem | Rows | Action | Why |
|---|---:|---|---|
| `Mileage = 2147483647` | 7 | NULL | 2³¹−1 — signed 32-bit INT_MAX. An overflow or sentinel, not an odometer reading |
| `Mileage = 0` | 714 | NULL | No used car has zero kilometres; this is missing data encoded as zero |
| `Levy = '-'` | 5,709 | NULL | A string sentinel in a numeric column |
| `Engine volume` empty/0 | 10 | NULL | No displacement recorded |
| `Doors = '04-May'` | 18,800 | **Repaired** | Recoverable — see D7 |
| `Mileage > 1,000,000` | 67 | **Flagged** | Implausible, but not provably corrupt |
| `Price < 100` or `> 1,000,000` | 349 + 67 | **Flagged** | Placeholder prices, but price is the measure the dashboard exists to show |

**Why not cap the INT_MAX rows:** capping 2,147,483,647 to a ceiling asserts
that the car drove that distance. Nobody ever recorded that. Substituting an
invented number for a corrupt one is not cleaning — it is manufacturing data
that will be averaged and charted as though it were observed. `NULL` asserts
nothing, which is precisely what is known.

Capping (winsorising) is a legitimate *modelling* choice for genuine outliers
that would distort a fit. It is never a repair for corruption.

**Why NULL and not 0:** `AVG()` ignores NULLs; it averages in zeros. Measured
on the loaded data:

```
avg levy, NULLs ignored   906.30
avg levy, NULLs as zero   632.89     <- understated by 30%
```

Loading the missing 30% as zero would have put a wrong number on the dashboard.
It would also destroy the missing-data signal, because the gap between
`COUNT(levy_usd)` and `COUNT(*)` is what reports how much is absent.

**Why flag rather than delete:** a car with an implausible price still has a
valid year, make, model, and mileage. Dropping the row discards 17 good fields
to remove one bad one. The flag lets each query decide.

---

## D6 — The CSV path stays as a fallback

**Decision:** the app uses Postgres when `DATABASE_URL` is set and falls back to
reading the CSV when it is not.

**Why:** the deployed dashboard is live and must not break. A fallback means
the database layer can be developed, deployed, and rolled back without the
public URL ever going down. It also keeps the project runnable by anyone who
clones it without standing up a database.

---

## D7 — Repairing the Excel date corruption

**Decision:** map `04-May → 4-5` and `02-Mar → 2-3`; log every repair.

**What happened:** the `Doors` column holds door-count ranges. Somewhere
upstream the CSV was opened in Excel, which interpreted `4-5` and `2-3` as
dates and rewrote them. All 18,924 rows are affected; only three distinct
values survive (`>5` escaped because no date can be made of it).

**Why the repair is legitimate here and would not always be:** the domain has
three values and the mapping is unambiguous, so the original is recoverable.
Had `Doors` ranged 1–10, several inputs would have collapsed onto the same
date and the information would be **permanently destroyed**. That is the real
lesson: this was recoverable by luck, not by design.

**Where it should have been caught:** at ingest, by a validation step asserting
that a column named `Doors` contains door counts. Detecting it during analysis
means every downstream consumer already read the bad values.

---

## D8 — `COPY` for the fact load, `executemany` for the rest

**Decision:** stream the 18,924 fact rows through a single `COPY FROM STDIN`;
use `executemany` for the small dimension and quarantine inserts.

**Why:** `COPY` is Postgres's bulk-ingest path — one statement, one parse, one
network round trip, minimal per-row overhead. Row-by-row `INSERT`s pay parse
and round-trip cost 18,924 times. Total load time is ~1.8 seconds including
schema rebuild.

The dimensions are 65, 1,601, 11, and 7 rows; `executemany` is clearer there
and the difference is unmeasurable. Use the bulk path where the volume is.

---

## D9 — Full refresh, not incremental

**Decision:** every run drops and rebuilds the schema, then reloads.

**Why:** the source is a static CSV export with no change feed, no updated-at
column, and no deletes to track. Incremental loading solves a problem this
dataset does not have, and an upsert path that is never exercised is a path
that is never tested.

**When it flips:** a daily delta feed, a source too large to reload inside the
batch window, or a requirement to preserve history would all call for
incremental loads with change detection.

---

## D10 — No secondary indexes yet

**Decision:** Phase 1 creates none beyond the automatic PRIMARY KEY / UNIQUE
indexes.

**Why:** Postgres indexes primary keys and unique constraints automatically,
but **does not index foreign-key columns**. The dashboard filters on exactly
those columns. Leaving them unindexed means Phase 3 can measure `EXPLAIN`
before and after — a real measurement on this data rather than an assertion
that indexes help.

---

## D11 — Two levels of duplicate defence

**Decision:** dedupe in the loader **and** enforce a primary key on the source
ID.

**Why:** the CSV contains 313 exact duplicate rows. Deduping in the pipeline
keeps them out. The primary key means that if a later change breaks the dedupe
step, the database rejects the duplicate rather than silently storing it.
Cleaning is a process, and processes regress; a constraint is a guarantee.

**Verified safe:** no ID in the source appears twice with *different* data —
checked before choosing the source ID as the key. Had there been conflicts, a
surrogate key plus a quarantine rule would have been required instead.

---

## D12 — Pure functions for cleaning

**Decision:** every rule in `etl/clean.py` is a pure function — string in,
value plus optional `Issue` out. No database, no I/O, no globals.

**Why:** this is the part of a pipeline that can be unit-tested without
infrastructure, and the part whose behaviour must be explainable rule by rule.
Keeping the rules separate from orchestration means `load.py` reads as a
sequence of steps and `clean.py` reads as a specification.

---

## D13 — Audit tables live outside the data transaction

**Decision:** `etl_load_run` and `etl_quarantine` are defined in
`db/schema_audit.sql`, created once with `IF NOT EXISTS`, never dropped, and
written on a **separate autocommit connection**. The star schema is rebuilt in
`db/schema_star.sql` inside one transaction.

**This fixes two bugs in the first version**, both the same mistake in
different clothes — treating operational metadata as though it were data:

1. **A failed run erased its own record.** The run row was inserted inside the
   load transaction, so a mid-load failure rolled it back. The load is atomic
   *by design*; the audit of the load must not be, or failures are invisible.
2. **A successful run erased the previous run's history.** The audit tables
   were dropped and recreated along with the star schema, so "when did this
   last load, and how clean was it?" was unanswerable after the next run.

**Verified, not assumed.** A deliberate primary-key collision was injected
mid-`COPY`. Result: the run was recorded as `failed` with the actual database
error, and all 18,924 rows from the previous load survived untouched.

**The property this relies on is engine-specific.** PostgreSQL DDL is
transactional, so the rollback undoes the `DROP`/`CREATE` too. **Oracle issues
an implicit commit on DDL** — the identical script there would commit at the
first `CREATE TABLE` and strand you with half-built tables.

---

## D14 — `CREATE TABLE IF NOT EXISTS` is not a migration

**Decision:** columns added to an already-released table get an explicit
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

**Found the hard way.** `error_message` was added to the `etl_load_run`
definition, but the table already existed from an earlier run — so
`CREATE TABLE IF NOT EXISTS` skipped the statement **in full**, the new column
never appeared, and the failure path broke at the moment it was needed. The
statement is all-or-nothing: it does not reconcile an existing table with a
changed definition.

This is how schema drift starts, and it is silent. The `ALTER` statements at
the foot of `schema_audit.sql` are forward-only and safe to re-run against
either a fresh or an existing database.

---

## D15 — A connection pool, sized to the callback count

**Decision:** `psycopg_pool.ConnectionPool`, `min_size=1`, `max_size=5`, opened
lazily on first use and closed via `atexit`.

**Why:** Dash fires all five chart callbacks when one filter moves. Without a
pool that is five connect/disconnect cycles per interaction, and a PostgreSQL
connection is not cheap — the server **forks a backend process** for each one.
`max_size=5` matches the five concurrent callbacks; beyond that, callers queue
rather than pile more load onto the server. It also bounds the app's share of
`max_connections` (100 on Render's free tier) when several people have the
dashboard open.

**Opened with `open=False` then explicitly:** a database that is down at import
time must raise where it can be caught, not at module import, which would take
the whole app down instead of falling back to the CSV.

**Closed on exit:** otherwise the pool's worker threads are still running at
interpreter finalisation and psycopg raises a `PythonFinalizationError` on the
way out.

---

## D16 — Push down what the visualisation allows, and no more

**Decision:** aggregate charts are computed in SQL; distribution charts return
rows.

| Chart | Where computed | Rows returned |
|---|---|---|
| Average price by category | `GROUP BY` in SQL | 11 |
| Count by manufacturer | `GROUP BY` in SQL | ~65 |
| Scatter (mileage × price) | SQL filters, Plotly plots | row-level |
| Box (price by manufacturer) | SQL filters, Plotly plots | row-level |
| Histogram (mileage) | SQL filters, Plotly bins | row-level |

The bar chart previously shipped every matching row across the wire to compute
eleven averages. Now eleven rows come back. But a box plot needs the
distribution and a scatter needs the points — those cannot be aggregated away,
and claiming otherwise would be dishonest about what changed. The win there is
that the `WHERE` clause runs server-side instead of in Python.

**The larger memory win:** `df = pd.read_csv(...)` used to run at module scope,
so every gunicorn worker process held a full copy of the dataset. Workers now
hold query results.

**The IQR fence moved into SQL** (`percentile_cont`), recomputed per query so
the fence describes the cars currently being viewed — matching the original
behaviour. One deliberate difference: rows with NULL mileage are now **kept**,
because after cleaning, NULL means "mileage unknown" and such a car still has a
valid price, year, and category the price charts should count.

**The numbers changed, and the arithmetic is exact.** For one filter slice:

```
CSV path   1,407 rows
             -59  exact duplicates
             -58  zero-mileage rows (now NULL, excluded from mileage charts)
           ------
DB path    1,290 rows
```

**Why the old fence could never have caught this:** on the raw data that
slice's IQR fence runs from **−175,238 km** to 430,289 km. Zero sits well
inside a fence whose lower bound is a negative odometer reading, so every
"0 means missing" row passed straight through. Statistical outlier detection
cannot catch semantic corruption — that needs domain rules.

---

## D17 — `start-carvis.sh` checks prerequisites, never installs them

**Decision:** one bash script bootstraps the whole local stack — environment
checks, container, data load, app — and every failure exits with the exact
command needed to fix it.

**Why it refuses to install anything:** installing Docker or Python for someone
needs sudo, differs across distributions, and modifies a machine in ways its
owner did not ask for. A script that says *"docker is not installed → here is
the install link"* is more useful and far less dangerous than one that guesses.

**Why bash rather than Python:** the script's first job is checking whether the
Python environment exists, so it cannot depend on that environment. Bash also
has no install step of its own, which is the point.

**Why it polls `pg_isready` instead of `sleep 10`:** the container starts
listening on the port before the server can answer queries, so a fixed sleep is
either too short on a cold start or wasted time on a warm one. Cold start
measured at ~1 second on an existing container; the poll allows 60.

**Idempotent by construction:** it starts a stopped container, reuses a running
one, creates one only if none exists, and loads data only when the fact table
is empty or `--reload` is passed. Running it twice in a row is safe.

---

## D18 — Indexes built after the load; `ANALYZE` as the final step

**Decision:** `db/indexes.sql` runs *after* the fact `COPY`, inside the same
transaction, followed by `ANALYZE` on every table.

**Why after.** An index has to be maintained on every write. With these in
place during the `COPY`, all 18,924 rows would be inserted into each B-tree
individually, in sorted position, causing random writes and page splits. Built
afterwards, PostgreSQL reads every value at once, sorts in bulk, and constructs
the tree bottom-up in one pass. This is the standard warehouse move: drop the
indexes, bulk load, rebuild.

**What is deliberately NOT deferred:** the `PRIMARY KEY` on `listing_id`. It is
not a performance index — it is the constraint that rejects duplicate rows if
the loader's dedupe step ever breaks (D11). Deferring it would trade a
correctness guarantee for a little load speed. **Keep constraint-backing
indexes; defer the performance ones.**

**Why `ANALYZE` is not optional here.** The planner does not read your data — it
estimates costs from sampled statistics. Because the loader drops and recreates
the fact table, that table starts each run with *no statistics at all*.
Measured immediately after a load:

```
pg_class.reltuples = -1        ← "unknown"
actual rows        = 18,924
estimate for prod_year = 1998:    53 rows      ← 4x wrong
actual:                          205 rows
```

After `ANALYZE`, the estimate is 18,924 and the same filter estimates 13,939
against an actual 13,938. Autovacuum would eventually correct this, but the
dashboard queries *immediately* after a load — so we do it deterministically.

**Which columns.** PostgreSQL indexes `PRIMARY KEY` and `UNIQUE` columns
automatically but **does not index foreign keys**, and the dashboard filters on
exactly those: `manufacturer_id`, `model_id`, `category_id`, `fuel_type_id`,
plus `prod_year`. The composite `(manufacturer_id, prod_year)` serves the most
common pairing; column order matters, because an index can serve a filter on
its *leading* column alone but not a trailing one.

**Measured, not assumed** (`benchmark.py`, median of 5 runs):

| Query | Selectivity | Without indexes | With indexes | Change |
|---|---|---|---|---|
| One manufacturer, 5-year window | ~2% | Seq Scan 4.10 ms | Bitmap Heap Scan 0.40 ms | **10.4x** |
| Single rare year (1998) | 1.1% | Seq Scan 2.22 ms | Index Only Scan 0.07 ms | **32.2x** |
| Bar chart, three manufacturers | ~15% | Seq Scan 5.90 ms | Seq Scan 6.40 ms | same |
| `AVG(price)` where year >= 2010 | 74% | Seq Scan 5.34 ms | Seq Scan 5.34 ms | same |

**The last two rows are the honest part and are kept on purpose.** An index only
pays off when a filter is selective. At 74% the planner keeps the sequential
scan even though an index exists — correctly, because random heap fetches for
three quarters of a table cost more than reading it straight through. A
benchmark that only reports wins is marketing.

---

## D19 — Unit tests over the cleaning rules

**Decision:** `tests/test_clean.py`, 24 tests, run with `./.venv/bin/pytest -q`.
`pytest` lives in `requirements-dev.txt`, not `requirements.txt` — the app does
not need it at runtime.

**Why only `clean.py`:** it is the part of the pipeline that is pure — string
in, value plus optional `Issue` out, no database, no files (D12). That design
choice is worth nothing unless it is exercised, and "the rules are testable"
was a claim until there were tests.

**What the tests encode:** every case is a real defect from the source, so the
suite doubles as executable documentation of the data's problems. The two
classes that matter most are `TestCorruptValuesBecomeNull` and
`TestImplausibleValuesAreKeptAndFlagged` — together they pin down D5, the rule
that a value which was never real becomes NULL while a value that is merely
extreme survives with a flag.

`TestEveryRuleToleratesEmptyInput` guards the failure mode that would hurt
most: a parser raising on missing input kills the entire load.

---

## D20 — Price forecasting with scikit-learn

**Decision:** `ml/price_model.py` trains a gradient-boosting regressor on the
cleaned data in Postgres, and the dashboard shows a **predicted-vs-actual**
chart that honours the existing filters.

**Why it exists:** the project's own README advertised "price forecasts" and the
CV described "using scikit-learn to forecast prices", but there was no model in
the code — no scikit-learn import anywhere. This closes that gap. The dataset is
Kaggle's *car price prediction challenge*, so a price model is the project's
original intent, not a bolt-on.

**Trains on the database, not the CSV.** The model sees data *after*
`etl/clean.py` has run, so the mileage sentinels are NULL rather than
2,147,483,647 and the Excel-damaged door values are repaired. Feeding a model
the raw file would teach it that some cars have driven two billion kilometres.

### Features deliberately excluded

| Excluded | Reason |
|---|---|
| `levy_usd` | **Target leakage.** Vehicle tax tracks vehicle value, so the model would partly be reading the answer. It is also 30% NULL. |
| `model_name` | 1,601 categories. One-hot encoding adds more columns than the signal justifies at 18k rows. |
| rows where `price_suspect` | Placeholder prices ($1, $26M) are not observations. Training on them teaches noise. |

Leakage is the specific failure worth guarding: a model that quietly reads its
own target reports an excellent score and is useless. `tests/test_price_model.py`
asserts each exclusion so a future edit cannot silently undo it.

**`log1p` on the target.** Prices are heavily right-skewed, so the model learns
`log(price)` and predictions are converted back with `expm1`. Metrics are always
reported in dollars, never in log space, because a log-space error is not
something anyone can act on.

**Median imputation for missing numerics** rather than dropping the rows: a car
with unknown mileage still has a valid year, make, and engine.

### Measured, with a baseline

| Model | MAE | RMSE | R² |
|---|---:|---:|---:|
| Baseline (always predict the median) | $12,019 | $23,544 | −0.036 |
| Ridge regression | $10,445 | $22,082 | 0.089 |
| **Gradient boosting** | **$5,904** | **$17,024** | **0.459** |

18,574 rows after excluding suspect prices; 80/20 split, `random_state=42`.

**The baseline is the point.** An R² of 0.459 means little on its own; halving
the error of a median predictor is a real result. And it is honestly modest —
used-car price is driven by condition, service history, accident record, and
trim, none of which are in this dataset. Saying that is stronger than implying
the model is better than it is.

**The artifact is gitignored.** `ml/price_model.joblib` (373 KB) is regenerable
output, so it is rebuilt rather than committed; `start-carvis.sh` trains it if it
is missing. If the file is absent the chart shows a message instead of failing —
the same degrade-don't-crash principle as the CSV fallback (D6).

---

## Revision history

| Change | From | To | Reason |
|---|---|---|---|
| Database driver | psycopg2-binary | psycopg3 | Python 3.14 wheel support, native COPY, actively maintained (D3) |
| Connection layer | SQLAlchemy engine as a pool | psycopg_pool | One database library instead of two; removes the "why import SQLAlchemy and not use the ORM" ambiguity |
| Schema file | one `schema.sql` | `schema_star.sql` + `schema_audit.sql` | Audit tables must survive both a rollback and a successful reload (D13) |
| Audit writes | inside the load transaction | separate autocommit connection | A failed run was rolling back its own record (D13) |
| New audit columns | `CREATE TABLE IF NOT EXISTS` | explicit `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` | IF NOT EXISTS silently skipped the whole table, so the column never appeared (D14) |
| Index timing | (none existed) | created after the `COPY`, never before | Index maintenance per row during a bulk load; bulk build is one sorted pass (D18) |
| Benchmark's broad-filter query | `COUNT(*)` | `AVG(price_usd)` | `COUNT(*)` was answered by an Index Only Scan without touching the heap, so it never exercised the selectivity point it was there to show |
| Price forecasting | absent, though the README and CV claimed it | `ml/price_model.py` + a dashboard chart | Closed a real gap between what the project said and what it did (D20) |

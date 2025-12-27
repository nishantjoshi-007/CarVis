-- =============================================================================
-- CarVis — ETL control tables   (AUDIT tables)
-- =============================================================================
-- Every statement is IF NOT EXISTS. These tables are created once and then
-- NEVER dropped, which is the whole point of separating them from
-- db/schema_star.sql.
--
-- WHY THEY ARE SEPARATE -- two distinct bugs this fixes:
--
--   1. They must survive a ROLLBACK. The data load runs as one transaction so
--      that a failure leaves the previous dataset intact (atomic swap). But if
--      the run log lived inside that transaction, a failed run would roll back
--      its own record and the failure would be invisible. The load is atomic;
--      the AUDIT of the load must not be. The loader therefore writes these
--      tables on a separate autocommit connection.
--
--   2. They must survive a SUCCESSFUL run. If the audit tables were dropped and
--      recreated with the star schema, every run would erase the history of
--      the one before it, and "when did this last load, and how clean was it?"
--      would be unanswerable. Load history accumulates.
--
-- This is the standard control-table pattern: operational metadata lives
-- outside the data it describes.
-- =============================================================================


-- One row per execution of etl/load.py.
CREATE TABLE IF NOT EXISTS etl_load_run (
    run_id          SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    source_file     TEXT NOT NULL,
    rows_read       INT,     -- lines in the CSV, excluding the header
    rows_duplicate  INT,     -- exact duplicate rows discarded before load
    rows_loaded     INT,     -- rows that reached fact_car_listing
    rows_rejected   INT,     -- rows dropped entirely (unusable)
    values_repaired INT,     -- individual FIELDS nulled, repaired, or flagged
    status          TEXT NOT NULL DEFAULT 'running',  -- running | success | failed
    error_message   TEXT     -- populated when status = 'failed'
);


-- One row per FIELD-level problem, not per record. A car with a bad mileage
-- and a missing levy produces two rows here. This is what turns "I cleaned the
-- data" into something a reviewer can verify -- the Informatica reject-file
-- equivalent.
CREATE TABLE IF NOT EXISTS etl_quarantine (
    quarantine_id BIGSERIAL PRIMARY KEY,
    run_id        INT  NOT NULL REFERENCES etl_load_run(run_id),
    source_id     TEXT,           -- listing's source ID (text: may be unusable)
    column_name   TEXT NOT NULL,
    raw_value     TEXT,           -- exactly what the source contained
    issue         TEXT NOT NULL,  -- machine-readable reason code
    action        TEXT NOT NULL   -- nulled | repaired | flagged | rejected
);

CREATE INDEX IF NOT EXISTS idx_quarantine_run   ON etl_quarantine(run_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_issue ON etl_quarantine(issue);


-- ---------------------------------------------------------------------------
-- MIGRATIONS -- forward-only, idempotent
-- ---------------------------------------------------------------------------
-- CREATE TABLE IF NOT EXISTS is NOT a migration. If the table already exists
-- with an older shape, the statement is skipped in full and any newly added
-- column silently never appears -- which is exactly how this file lost
-- error_message the first time, and how schema drift starts.
--
-- Anything added to a table AFTER its first release needs an explicit ALTER.
-- These are safe to re-run and safe to run against a fresh database.
ALTER TABLE etl_load_run ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE etl_load_run ADD COLUMN IF NOT EXISTS values_repaired INT;

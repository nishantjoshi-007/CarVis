-- CarVis ETL control tables. Created once, never dropped. (decision.md D13)
--
-- Separate from the star schema for two reasons, both the same mistake:
--   * they must survive a ROLLBACK, or a failed run erases its own record
--   * they must survive a SUCCESSFUL run, or each load wipes the last one's
--     history
-- The loader writes them on a separate autocommit connection.

CREATE TABLE IF NOT EXISTS etl_load_run (
    run_id          SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    source_file     TEXT NOT NULL,
    rows_read       INT,
    rows_duplicate  INT,
    rows_loaded     INT,
    rows_rejected   INT,
    values_repaired INT,
    status          TEXT NOT NULL DEFAULT 'running',  -- running | success | failed
    error_message   TEXT
);

-- One row per FIELD-level problem, not per record. The Informatica reject-file
-- equivalent: what turns "I cleaned the data" into something verifiable.
CREATE TABLE IF NOT EXISTS etl_quarantine (
    quarantine_id BIGSERIAL PRIMARY KEY,
    run_id        INT  NOT NULL REFERENCES etl_load_run(run_id),
    source_id     TEXT,
    column_name   TEXT NOT NULL,
    raw_value     TEXT,
    issue         TEXT NOT NULL,
    action        TEXT NOT NULL   -- nulled | repaired | flagged | rejected
);

CREATE INDEX IF NOT EXISTS idx_quarantine_run   ON etl_quarantine(run_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_issue ON etl_quarantine(issue);

-- CREATE TABLE IF NOT EXISTS is not a migration: if the table already exists
-- with an older shape the statement is skipped in full and a new column never
-- appears. Anything added after first release needs an explicit ALTER. (D14)
ALTER TABLE etl_load_run ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE etl_load_run ADD COLUMN IF NOT EXISTS values_repaired INT;

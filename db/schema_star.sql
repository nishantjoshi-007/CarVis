-- =============================================================================
-- CarVis — star schema for the car listings dataset   (DATA tables)
-- =============================================================================
-- Run with:  psql "$DATABASE_URL" -f db/schema_star.sql
-- Or via the loader, which executes it before loading (etl/load.py).
--
-- SCOPE. This file owns the DATA tables only. They are dropped and rebuilt on
-- every run (full refresh -- see decision.md D9). The ETL control tables live
-- in db/schema_audit.sql and are deliberately NEVER dropped: load history has
-- to outlive the data it describes, otherwise a failed run erases its own
-- evidence and every successful run wipes the audit trail of the last one.
--
-- MODEL: one fact table (the measurable events -- a listing with a price)
--        surrounded by dimensions (the things you slice BY).
--
-- Why dimensions at only ~19k rows? Not speed. At this size a flat table would
-- be marginally faster, because joins cost work. The reasons are integrity
-- ("LEXUS" stored once, not 19,000 times), consistency (the dashboard's filter
-- lists are read from the dimensions, so they cannot drift from the facts), and
-- extensibility (a dimension can gain columns without touching the fact table).
--
-- TRANSACTION NOTE: PostgreSQL DDL is transactional. Every DROP and CREATE
-- below participates in the caller's transaction, so a failure part-way
-- through the load leaves the previous schema and data completely intact --
-- an atomic swap. (Oracle would differ: DDL there issues an implicit commit.)
-- =============================================================================

-- Dropped children-first so the foreign keys never block us.
DROP TABLE IF EXISTS fact_car_listing  CASCADE;
DROP TABLE IF EXISTS dim_model         CASCADE;
DROP TABLE IF EXISTS dim_manufacturer  CASCADE;
DROP TABLE IF EXISTS dim_category      CASCADE;
DROP TABLE IF EXISTS dim_fuel_type     CASCADE;


-- ---------------------------------------------------------------------------
-- DIMENSIONS
-- ---------------------------------------------------------------------------

-- 65 rows.
CREATE TABLE dim_manufacturer (
    manufacturer_id   SERIAL PRIMARY KEY,
    manufacturer_name TEXT NOT NULL UNIQUE
);

-- 1,601 rows. The natural key is the PAIR (manufacturer, model): "Civic" is
-- only meaningful under Honda. The source has 1,590 distinct model STRINGS but
-- 1,601 distinct manufacturer-model PAIRS -- that gap is names reused across
-- makers, and it is exactly why the model name alone is not a key.
CREATE TABLE dim_model (
    model_id        SERIAL PRIMARY KEY,
    manufacturer_id INT  NOT NULL REFERENCES dim_manufacturer(manufacturer_id),
    model_name      TEXT NOT NULL,
    UNIQUE (manufacturer_id, model_name)
);

-- 11 rows: Sedan, Jeep, Hatchback, ...
CREATE TABLE dim_category (
    category_id   SERIAL PRIMARY KEY,
    category_name TEXT NOT NULL UNIQUE
);

-- 7 rows: Petrol, Diesel, Hybrid, CNG, LPG, Hydrogen, Plug-in Hybrid.
CREATE TABLE dim_fuel_type (
    fuel_type_id   SERIAL PRIMARY KEY,
    fuel_type_name TEXT NOT NULL UNIQUE
);


-- ---------------------------------------------------------------------------
-- FACT
-- ---------------------------------------------------------------------------
-- listing_id is the source system's own ID used directly as the primary key.
-- Deliberate: the CSV holds 313 exact duplicate rows, and a PK makes the
-- DATABASE reject them even if a future edit breaks the dedupe step in the
-- loader. Cleaning is a process and processes regress; a constraint cannot.
-- (Verified safe: no ID in the source appears twice with different data.)
--
-- NULL means "we do not know", never "zero". Rules live in etl/clean.py.
CREATE TABLE fact_car_listing (
    listing_id      BIGINT  PRIMARY KEY,
    manufacturer_id INT     NOT NULL REFERENCES dim_manufacturer(manufacturer_id),
    model_id        INT     NOT NULL REFERENCES dim_model(model_id),
    category_id     INT     NOT NULL REFERENCES dim_category(category_id),
    fuel_type_id    INT     NOT NULL REFERENCES dim_fuel_type(fuel_type_id),

    prod_year       SMALLINT      NOT NULL,
    price_usd       NUMERIC(12,2) NOT NULL,
    levy_usd        NUMERIC(12,2),           -- NULL: source held '-' (30% of rows)
    mileage_km      INTEGER,                 -- NULL: 0, or the INT_MAX sentinel
    engine_volume_l NUMERIC(4,1),            -- NULL: blank or 0 in source
    is_turbo        BOOLEAN NOT NULL DEFAULT FALSE,
    cylinders       SMALLINT,
    doors           TEXT,                    -- repaired from Excel date damage
    gearbox_type    TEXT,
    drive_wheels    TEXT,
    wheel_side      TEXT,
    color           TEXT,
    airbags         SMALLINT,
    has_leather     BOOLEAN,

    -- Quality flags. The value is kept (it may be real) but callers can exclude
    -- it from averages by choice. More honest than deleting the whole row.
    price_suspect   BOOLEAN NOT NULL DEFAULT FALSE,
    mileage_suspect BOOLEAN NOT NULL DEFAULT FALSE
);

-- NOTE: no secondary indexes here, on purpose. PostgreSQL indexes PRIMARY KEY
-- and UNIQUE columns automatically but does NOT index foreign-key columns --
-- which is exactly what the dashboard filters on. Left unindexed so Phase 3
-- can measure EXPLAIN before and after, on this data, rather than assert that
-- indexes help.

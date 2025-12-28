-- CarVis star schema: the DATA tables. Dropped and rebuilt on every run.
-- The ETL control tables live in schema_audit.sql and are never dropped.
-- Reasoning for the model: decision.md D4, D9.
--
-- PostgreSQL DDL is transactional, so every statement here participates in the
-- caller's transaction: a failure part-way through a load leaves the previous
-- schema and data intact. Oracle would differ -- DDL there implicitly commits.

DROP TABLE IF EXISTS fact_car_listing  CASCADE;
DROP TABLE IF EXISTS dim_model         CASCADE;
DROP TABLE IF EXISTS dim_manufacturer  CASCADE;
DROP TABLE IF EXISTS dim_category      CASCADE;
DROP TABLE IF EXISTS dim_fuel_type     CASCADE;


-- 65 rows
CREATE TABLE dim_manufacturer (
    manufacturer_id   SERIAL PRIMARY KEY,
    manufacturer_name TEXT NOT NULL UNIQUE
);

-- 1,601 rows. Keyed on the PAIR: "Civic" only means something under Honda.
-- The source has 1,590 distinct model strings but 1,601 distinct pairs.
CREATE TABLE dim_model (
    model_id        SERIAL PRIMARY KEY,
    manufacturer_id INT  NOT NULL REFERENCES dim_manufacturer(manufacturer_id),
    model_name      TEXT NOT NULL,
    UNIQUE (manufacturer_id, model_name)
);

-- 11 rows
CREATE TABLE dim_category (
    category_id   SERIAL PRIMARY KEY,
    category_name TEXT NOT NULL UNIQUE
);

-- 7 rows
CREATE TABLE dim_fuel_type (
    fuel_type_id   SERIAL PRIMARY KEY,
    fuel_type_name TEXT NOT NULL UNIQUE
);


-- listing_id is the source ID used directly as the PK, so the database rejects
-- the 313 duplicate rows even if the loader's dedupe step breaks. Verified
-- safe: no ID appears twice with different data. (D11)
--
-- NULL means "we do not know", never zero. Rules in etl/clean.py.
CREATE TABLE fact_car_listing (
    listing_id      BIGINT  PRIMARY KEY,
    manufacturer_id INT     NOT NULL REFERENCES dim_manufacturer(manufacturer_id),
    model_id        INT     NOT NULL REFERENCES dim_model(model_id),
    category_id     INT     NOT NULL REFERENCES dim_category(category_id),
    fuel_type_id    INT     NOT NULL REFERENCES dim_fuel_type(fuel_type_id),

    prod_year       SMALLINT      NOT NULL,
    price_usd       NUMERIC(12,2) NOT NULL,
    levy_usd        NUMERIC(12,2),           -- NULL: source held '-'
    mileage_km      INTEGER,                 -- NULL: 0 or the INT_MAX sentinel
    engine_volume_l NUMERIC(4,1),
    is_turbo        BOOLEAN NOT NULL DEFAULT FALSE,
    cylinders       SMALLINT,
    doors           TEXT,                    -- repaired from Excel date damage
    gearbox_type    TEXT,
    drive_wheels    TEXT,
    wheel_side      TEXT,
    color           TEXT,
    airbags         SMALLINT,
    has_leather     BOOLEAN,

    -- Value kept, but callers can exclude it from averages. More honest than
    -- deleting a row over one bad field.
    price_suspect   BOOLEAN NOT NULL DEFAULT FALSE,
    mileage_suspect BOOLEAN NOT NULL DEFAULT FALSE
);

-- Secondary indexes live in db/indexes.sql and are applied AFTER the load.

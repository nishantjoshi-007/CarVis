-- Secondary indexes, applied by etl/load.py AFTER the fact COPY. (decision.md D18)
--
-- After, because an index is maintained on every write: in place during the
-- COPY, all 18,924 rows are inserted into each B-tree individually. Built
-- afterwards, PostgreSQL sorts every value once and builds the tree in one pass.
--
-- The PRIMARY KEY is deliberately NOT deferred -- it is the constraint that
-- rejects duplicates, not a performance index.
--
-- These columns because PostgreSQL indexes PRIMARY KEY and UNIQUE columns
-- automatically but NOT foreign keys, which is what the dashboard filters on.
--
-- They help SELECTIVE filters only. Measured (benchmark.py):
--   prod_year = 1998    1.1% of rows   Index Only Scan, 32x faster
--   prod_year >= 2010    74% of rows   Seq Scan, index correctly unused

CREATE INDEX IF NOT EXISTS idx_fact_prod_year    ON fact_car_listing(prod_year);
CREATE INDEX IF NOT EXISTS idx_fact_manufacturer ON fact_car_listing(manufacturer_id);
CREATE INDEX IF NOT EXISTS idx_fact_model        ON fact_car_listing(model_id);
CREATE INDEX IF NOT EXISTS idx_fact_category     ON fact_car_listing(category_id);
CREATE INDEX IF NOT EXISTS idx_fact_fuel_type    ON fact_car_listing(fuel_type_id);

-- Manufacturer plus year is the dashboard's most common pairing. Column order
-- matters: an index serves a filter on its leading column, not a trailing one.
CREATE INDEX IF NOT EXISTS idx_fact_manufacturer_year
    ON fact_car_listing(manufacturer_id, prod_year);

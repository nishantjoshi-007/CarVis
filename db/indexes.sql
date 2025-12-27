-- =============================================================================
-- CarVis — secondary indexes
-- =============================================================================
-- Applied by etl/load.py AFTER the fact table is populated, never before.
--
-- WHY AFTER THE LOAD
-- ------------------
-- An index has to be maintained on every write. With these in place during the
-- COPY, all 18,924 rows would be inserted into each B-tree one at a time, in
-- sorted position, causing random writes and page splits. Building afterwards
-- lets PostgreSQL read every value at once, sort in bulk, and construct the
-- tree bottom-up in a single pass.
--
-- This is the standard warehouse-loading move: drop the indexes, bulk load,
-- rebuild.
--
-- WHAT IS *NOT* DEFERRED
-- ----------------------
-- The PRIMARY KEY on fact_car_listing.listing_id stays in place for the COPY.
-- It is not a performance index -- it is the constraint that rejects duplicate
-- rows if the loader's dedupe step ever breaks. Deferring it would trade a
-- correctness guarantee for a little load speed. Keep constraint-backing
-- indexes; defer the performance ones.
--
-- WHY THESE COLUMNS
-- -----------------
-- PostgreSQL indexes PRIMARY KEY and UNIQUE columns automatically, but it does
-- NOT index foreign keys -- and the dashboard filters on exactly those. Every
-- column below is one the dashboard's WHERE clause touches.
--
-- HONEST SCOPE: these help SELECTIVE filters. A filter matching most of the
-- table is faster with a sequential scan, and the planner will correctly
-- ignore these indexes in that case. Measured on this data (benchmark.py):
--   prod_year = 1998    ->  1.1% of rows  ->  Bitmap Index Scan, ~5.7x faster
--   prod_year >= 2010   -> 73.7% of rows  ->  Seq Scan, index correctly unused
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_fact_prod_year   ON fact_car_listing(prod_year);
CREATE INDEX IF NOT EXISTS idx_fact_manufacturer ON fact_car_listing(manufacturer_id);
CREATE INDEX IF NOT EXISTS idx_fact_model        ON fact_car_listing(model_id);
CREATE INDEX IF NOT EXISTS idx_fact_category     ON fact_car_listing(category_id);
CREATE INDEX IF NOT EXISTS idx_fact_fuel_type    ON fact_car_listing(fuel_type_id);

-- Composite: manufacturer plus year is the dashboard's most common pairing
-- ("BMWs from 2015 onward"). Column order matters -- manufacturer first,
-- because an index can be used for a leading-column filter alone but not for a
-- trailing one. This serves manufacturer-only queries too, which is why the
-- single-column manufacturer index above is arguably redundant; it is kept
-- because the planner picks the cheaper of the two per query.
CREATE INDEX IF NOT EXISTS idx_fact_manufacturer_year
    ON fact_car_listing(manufacturer_id, prod_year);

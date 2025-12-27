"""
Every question the dashboard asks of the data, in one place.

TWO PATHS, ONE INTERFACE
------------------------
Each public function returns the DataFrame its chart needs. If a database is
configured and reachable it runs SQL; otherwise it falls back to the original
pandas-over-CSV logic. The callbacks in app.py do not know or care which
happened.

WHAT IS PUSHED DOWN AND WHAT IS NOT
-----------------------------------
Aggregate charts (pie, bar) are computed in the database and return one row per
category -- 11 rows instead of 18,924. Distribution charts (scatter, box,
histogram) genuinely need row-level data to plot, so they cannot be aggregated
away; those return rows, and the win there is the WHERE clause filtering
server-side rather than in Python.

That distinction is the honest version of "push computation to the data": push
what the visualisation lets you push, and be clear about what it does not.
"""

from __future__ import annotations

import pandas as pd

from src import db

CSV_PATH = "./data/car_price_prediction.csv"

# --- SQL building blocks ----------------------------------------------------

# Joining the four dimensions back to readable names. The dashboard's controls
# work in names ("LEXUS"), the fact table stores surrogate keys (17) -- this is
# where the star schema is actually paid for.
_JOINS = """
    FROM fact_car_listing l
    JOIN dim_manufacturer mf ON mf.manufacturer_id = l.manufacturer_id
    JOIN dim_model        md ON md.model_id        = l.model_id
    JOIN dim_category     c  ON c.category_id      = l.category_id
    JOIN dim_fuel_type    ft ON ft.fuel_type_id    = l.fuel_type_id
"""

# The mileage outlier fence, expressed in SQL.
#
# The original pandas code computed an IQR fence on the FILTERED subset and
# dropped anything outside it. percentile_cont is the SQL equivalent, and it is
# recomputed per query for the same reason: the fence should describe the cars
# you are currently looking at, not the whole table.
#
# One deliberate difference: rows whose mileage is NULL are KEPT. Post-cleaning,
# NULL means "unknown mileage", and such a car still has a valid price, year and
# category that the price charts should count. The original data had no NULLs --
# those rows carried 0 or the INT_MAX sentinel and were silently discarded by
# the fence.
_BASE_CTE = """
WITH base AS (
    -- price cast to float8 here rather than at each call site: NUMERIC comes
    -- back as Decimal, which Plotly will not chart.
    SELECT l.listing_id, l.price_usd::float8 AS price_usd, l.mileage_km, l.prod_year,
           l.engine_volume_l::float8 AS engine_volume_l, l.cylinders, l.airbags,
           l.is_turbo, l.has_leather, l.doors, l.gearbox_type, l.drive_wheels,
           l.price_suspect,
           mf.manufacturer_name, md.model_name, c.category_name, ft.fuel_type_name
    {joins}
    WHERE {filters}
),
bounds AS (
    SELECT percentile_cont(0.25) WITHIN GROUP (ORDER BY mileage_km) AS q1,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY mileage_km) AS q3
    FROM base
    WHERE mileage_km IS NOT NULL
),
filtered AS (
    SELECT b.*
    FROM base b
    CROSS JOIN bounds bd
    WHERE b.mileage_km IS NULL
       OR b.mileage_km BETWEEN bd.q1 - 2 * (bd.q3 - bd.q1)
                           AND bd.q3 + 2 * (bd.q3 - bd.q1)
)
"""


def _build_filters(manufacturers, years, models, fuel_types) -> tuple[str, dict]:
    """Turn the dashboard's control state into a WHERE clause plus parameters.

    Values are collected into a dict and bound by the driver. `= ANY(%(x)s)`
    takes a Python list directly, which avoids hand-assembling an IN (...) list
    -- the classic place SQL injection gets introduced.
    """
    clauses: list[str] = ["TRUE"]
    params: dict = {}

    if manufacturers:
        clauses.append("mf.manufacturer_name = ANY(%(manufacturers)s)")
        params["manufacturers"] = list(manufacturers)
    if years:
        clauses.append("l.prod_year BETWEEN %(year_min)s AND %(year_max)s")
        params["year_min"], params["year_max"] = int(years[0]), int(years[1])
    if models:
        clauses.append("md.model_name = ANY(%(models)s)")
        params["models"] = list(models)
    if fuel_types:
        clauses.append("ft.fuel_type_name = ANY(%(fuel_types)s)")
        params["fuel_types"] = list(fuel_types)

    return " AND ".join(clauses), params


def _sql(select_clause: str, manufacturers, years, models, fuel_types) -> tuple[str, dict]:
    filters, params = _build_filters(manufacturers, years, models, fuel_types)
    cte = _BASE_CTE.format(joins=_JOINS, filters=filters)
    return cte + select_clause, params


# --- CSV fallback -----------------------------------------------------------

_csv_cache: pd.DataFrame | None = None


def _csv_frame() -> pd.DataFrame:
    """The pre-database code path, kept intact as a fallback (decision.md D6)."""
    global _csv_cache
    if _csv_cache is None:
        _csv_cache = pd.read_csv(CSV_PATH)
    return _csv_cache


def _csv_filtered(manufacturers, years, models, fuel_types) -> pd.DataFrame:
    df = _csv_frame().copy()
    if manufacturers:
        df = df[df["Manufacturer"].isin(manufacturers)]
    if years:
        df = df[(df["Prod. year"] >= years[0]) & (df["Prod. year"] <= years[1])]
    if models:
        df = df[df["Model"].isin(models)]
    if fuel_types:
        df = df[df["Fuel type"].isin(fuel_types)]

    df["Mileage"] = df["Mileage"].str.replace(" km", "", regex=False).astype("int64")
    q1, q3 = df["Mileage"].quantile(0.25), df["Mileage"].quantile(0.75)
    iqr = q3 - q1
    df = df[(df["Mileage"] >= q1 - 2 * iqr) & (df["Mileage"] <= q3 + 2 * iqr)]

    return df.rename(columns={
        "Manufacturer": "manufacturer_name", "Model": "model_name",
        "Category": "category_name", "Fuel type": "fuel_type_name",
        "Price": "price_usd", "Mileage": "mileage_km", "Prod. year": "prod_year",
    })


# --- Filter options (populated once, at app start) --------------------------


def get_filter_options() -> dict:
    """The values offered by the dropdowns, sliders and checklists.

    Read from the DIMENSIONS rather than from the facts. That is the point of
    having them: the options the user is offered cannot drift out of sync with
    the data stored, and it is four small scans instead of one big DISTINCT.
    """
    if not db.is_available():
        df = _csv_frame()
        return {
            "manufacturers": sorted(df["Manufacturer"].dropna().unique()),
            "models": sorted(df["Model"].dropna().unique()),
            "fuel_types": sorted(df["Fuel type"].dropna().unique()),
            "min_year": int(df["Prod. year"].min()),
            "max_year": int(df["Prod. year"].max()),
        }

    return {
        "manufacturers": db.fetch_df(
            "SELECT manufacturer_name FROM dim_manufacturer ORDER BY 1"
        )["manufacturer_name"].tolist(),
        "models": db.fetch_df(
            "SELECT DISTINCT model_name FROM dim_model ORDER BY 1"
        )["model_name"].tolist(),
        "fuel_types": db.fetch_df(
            "SELECT fuel_type_name FROM dim_fuel_type ORDER BY 1"
        )["fuel_type_name"].tolist(),
        **db.fetch_df(
            "SELECT MIN(prod_year) AS min_year, MAX(prod_year) AS max_year "
            "FROM fact_car_listing"
        ).iloc[0].to_dict(),
    }


# --- One function per chart -------------------------------------------------


def scatter_mileage_price(manufacturers, years, models, fuel_types) -> pd.DataFrame:
    """Row-level: a scatter plot needs the individual points."""
    if not db.is_available():
        return _csv_filtered(manufacturers, years, models, fuel_types)[["mileage_km", "price_usd"]]
    sql, params = _sql(
        "SELECT mileage_km, price_usd FROM filtered WHERE mileage_km IS NOT NULL",
        manufacturers, years, models, fuel_types,
    )
    return db.fetch_df(sql, params)


def count_by_manufacturer(manufacturers, years, models, fuel_types) -> pd.DataFrame:
    """AGGREGATED in the database: returns one row per manufacturer."""
    if not db.is_available():
        df = _csv_filtered(manufacturers, years, models, fuel_types)
        return (df.groupby("manufacturer_name").size()
                  .reset_index(name="listings").sort_values("listings", ascending=False))
    sql, params = _sql(
        "SELECT manufacturer_name, COUNT(*) AS listings FROM filtered "
        "GROUP BY manufacturer_name ORDER BY listings DESC",
        manufacturers, years, models, fuel_types,
    )
    return db.fetch_df(sql, params)


def avg_price_by_category(manufacturers, years, models, fuel_types) -> pd.DataFrame:
    """AGGREGATED in the database: 11 rows back instead of ~18,900.

    This is the clearest example of pushing computation to the data -- the
    pandas version transferred every matching row across the wire in order to
    compute eleven averages.
    """
    if not db.is_available():
        df = _csv_filtered(manufacturers, years, models, fuel_types)
        return (df.groupby("category_name")["price_usd"].mean()
                  .reset_index(name="avg_price"))
    sql, params = _sql(
        "SELECT category_name, ROUND(AVG(price_usd)::numeric, 2)::float8 AS avg_price "
        "FROM filtered GROUP BY category_name ORDER BY avg_price DESC",
        manufacturers, years, models, fuel_types,
    )
    return db.fetch_df(sql, params)


def price_by_manufacturer(manufacturers, years, models, fuel_types) -> pd.DataFrame:
    """Row-level: a box plot needs the distribution, not a summary."""
    if not db.is_available():
        return _csv_filtered(manufacturers, years, models, fuel_types)[
            ["manufacturer_name", "price_usd"]]
    sql, params = _sql(
        "SELECT manufacturer_name, price_usd FROM filtered",
        manufacturers, years, models, fuel_types,
    )
    return db.fetch_df(sql, params)


def mileage_distribution(manufacturers, years, models, fuel_types) -> pd.DataFrame:
    """Row-level: Plotly bins the histogram client-side."""
    if not db.is_available():
        return _csv_filtered(manufacturers, years, models, fuel_types)[["mileage_km"]]
    sql, params = _sql(
        "SELECT mileage_km FROM filtered WHERE mileage_km IS NOT NULL",
        manufacturers, years, models, fuel_types,
    )
    return db.fetch_df(sql, params)


def price_prediction_frame(manufacturers, years, models, fuel_types) -> pd.DataFrame:
    # Feature columns for ml/price_model.py, plus the actual price to compare
    # against. Suspect prices are excluded here for the same reason they are
    # excluded from training: they are placeholders, not observations.
    if not db.is_available():
        return pd.DataFrame()          # the model needs cleaned data; CSV is raw
    sql, params = _sql(
        "SELECT prod_year, mileage_km, engine_volume_l, cylinders, airbags, "
        "       is_turbo, has_leather, doors, gearbox_type, drive_wheels, "
        "       manufacturer_name, category_name, fuel_type_name, price_usd "
        "FROM filtered WHERE NOT price_suspect",
        manufacturers, years, models, fuel_types,
    )
    return db.fetch_df(sql, params)

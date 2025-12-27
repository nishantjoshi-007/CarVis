# Price forecasting for CarVis. Trains on the CLEANED data in Postgres, so the
# ETL rules in etl/clean.py are what the model sees. Rationale: decision.md D20.
#
#   python -m ml.price_model            train, report metrics, save the model
#   python -m ml.price_model --compare  also score the baseline and linear model

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psycopg
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = PROJECT_ROOT / "ml" / "price_model.joblib"
DEFAULT_DSN = "postgresql://carvis:carvis@localhost:5432/carvis"
RANDOM_STATE = 42

NUMERIC_FEATURES = ["prod_year", "mileage_km", "engine_volume_l", "cylinders", "airbags"]
CATEGORICAL_FEATURES = ["manufacturer_name", "category_name", "fuel_type_name",
                        "gearbox_type", "drive_wheels", "doors"]
BOOLEAN_FEATURES = ["is_turbo", "has_leather"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES + BOOLEAN_FEATURES
TARGET = "price_usd"

# EXCLUDED ON PURPOSE — see decision.md D20:
#   levy_usd    vehicle tax tracks vehicle value, so it leaks the target
#   model_name  1,601 categories; one-hot would add more columns than rows help
#   price_suspect rows  placeholder prices ($1, $26M) are not real observations

TRAINING_QUERY = f"""
SELECT {", ".join(f"l.{c}" for c in NUMERIC_FEATURES + BOOLEAN_FEATURES)},
       l.doors,
       mf.manufacturer_name, c.category_name, ft.fuel_type_name,
       l.gearbox_type, l.drive_wheels,
       l.price_usd::float8 AS price_usd
FROM fact_car_listing l
JOIN dim_manufacturer mf ON mf.manufacturer_id = l.manufacturer_id
JOIN dim_category     c  ON c.category_id      = l.category_id
JOIN dim_fuel_type    ft ON ft.fuel_type_id    = l.fuel_type_id
WHERE NOT l.price_suspect
"""


def build_pipeline(model):
    # Median imputation for the numerics: mileage and engine volume are NULL
    # where the source was corrupt, and dropping those rows would throw away
    # every other valid field on the car.
    numeric = Pipeline([("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler())])
    categorical = Pipeline([
        ("impute", SimpleImputer(strategy="most_frequent")),
        ("encode", OneHotEncoder(handle_unknown="ignore", min_frequency=10, sparse_output=False)),
    ])
    pre = ColumnTransformer([
        ("num", numeric, NUMERIC_FEATURES),
        ("cat", categorical, CATEGORICAL_FEATURES),
        ("bool", "passthrough", BOOLEAN_FEATURES),
    ])
    return Pipeline([("prep", pre), ("model", model)])


def load_training_data(dsn: str) -> pd.DataFrame:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(TRAINING_QUERY)
            rows = cur.fetchall()
            cols = [d.name for d in cur.description]
    df = pd.DataFrame(rows, columns=cols)
    for col in BOOLEAN_FEATURES:
        df[col] = df[col].astype(float)
    return df


def score(name: str, pipeline, X_train, X_test, y_train, y_test) -> dict:
    # Prices are heavily right-skewed, so the model learns log(price) and the
    # prediction is converted back. Metrics are always reported in dollars.
    pipeline.fit(X_train, np.log1p(y_train))
    predicted = np.expm1(pipeline.predict(X_test))
    return {
        "name": name,
        "mae": mean_absolute_error(y_test, predicted),
        "rmse": root_mean_squared_error(y_test, predicted),
        "r2": r2_score(y_test, predicted),
    }


def train(dsn: str, compare: bool = False):
    df = load_training_data(dsn)
    X, y = df[FEATURES], df[TARGET]
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=RANDOM_STATE)

    print(f"rows: {len(df):,}  train: {len(X_train):,}  test: {len(X_test):,}")
    print(f"price  median ${y.median():,.0f}   mean ${y.mean():,.0f}\n")

    candidates = [("gradient boosting", HistGradientBoostingRegressor(random_state=RANDOM_STATE))]
    if compare:
        # A baseline that always predicts the median is the number every other
        # model has to beat. Without it, an R2 has nothing to be judged against.
        candidates = [
            ("baseline (median)", DummyRegressor(strategy="median")),
            ("ridge regression", Ridge(alpha=1.0)),
        ] + candidates

    results = [score(name, build_pipeline(m), X_train, X_test, y_train, y_test)
               for name, m in candidates]

    print(f"{'model':<22}{'MAE':>12}{'RMSE':>12}{'R2':>8}")
    print("-" * 54)
    for r in results:
        print(f"{r['name']:<22}{'$' + format(r['mae'], ',.0f'):>12}"
              f"{'$' + format(r['rmse'], ',.0f'):>12}{r['r2']:>8.3f}")

    best = build_pipeline(HistGradientBoostingRegressor(random_state=RANDOM_STATE))
    best.fit(X[FEATURES], np.log1p(y))          # refit on everything before saving
    joblib.dump({"pipeline": best, "features": FEATURES}, MODEL_PATH)
    print(f"\nsaved {MODEL_PATH.name} ({MODEL_PATH.stat().st_size / 1024:.0f} KB)")
    return results


_cached = None


def load_model():
    # Returns None when the model has not been trained yet; callers degrade
    # rather than crash, same principle as the CSV fallback (D6).
    global _cached
    if _cached is None:
        if not MODEL_PATH.exists():
            return None
        _cached = joblib.load(MODEL_PATH)
    return _cached


def predict(df: pd.DataFrame) -> np.ndarray | None:
    bundle = load_model()
    if bundle is None or df.empty:
        return None
    frame = df.copy()
    for col in BOOLEAN_FEATURES:
        frame[col] = frame[col].astype(float)
    return np.expm1(bundle["pipeline"].predict(frame[bundle["features"]]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the CarVis price model")
    parser.add_argument("--compare", action="store_true",
                        help="also score a median baseline and ridge regression")
    args = parser.parse_args()
    try:
        train(os.environ.get("DATABASE_URL", DEFAULT_DSN), compare=args.compare)
    except psycopg.OperationalError as exc:
        print(f"cannot reach the database: {exc}", file=sys.stderr)
        print("start it with ./start-carvis.sh, or set DATABASE_URL", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

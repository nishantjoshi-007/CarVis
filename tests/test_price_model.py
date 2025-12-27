# Guards on the price model. The leakage tests matter most: they encode the
# feature-exclusion decision so a future edit cannot quietly undo it (D20).
import pandas as pd
import pytest
from ml import price_model as pm
class TestNoTargetLeakage:
    def test_levy_is_not_a_feature(self):
        # Vehicle tax tracks vehicle value, so it leaks the target
        assert "levy_usd" not in pm.FEATURES
    def test_price_is_not_a_feature(self):
        assert pm.TARGET not in pm.FEATURES
    def test_model_name_is_not_a_feature(self):
        # 1,601 categories: one-hot would add more columns than signal
        assert "model_name" not in pm.FEATURES
    def test_training_query_excludes_placeholder_prices(self):
        assert "NOT l.price_suspect" in pm.TRAINING_QUERY
class TestFeatureSet:
    def test_features_are_the_three_groups_combined(self):
        assert set(pm.FEATURES) == set(
            pm.NUMERIC_FEATURES + pm.CATEGORICAL_FEATURES + pm.BOOLEAN_FEATURES)
    def test_no_duplicate_features(self):
        assert len(pm.FEATURES) == len(set(pm.FEATURES))
    def test_pipeline_builds(self):
        from sklearn.dummy import DummyRegressor
        assert pm.build_pipeline(DummyRegressor()) is not None
class TestPredictDegradesGracefully:
    def test_empty_frame_returns_none(self):
        assert pm.predict(pd.DataFrame()) is None
@pytest.mark.skipif(not pm.MODEL_PATH.exists(),
                    reason="model not trained; run python -m ml.price_model")
class TestTrainedModel:
    def test_predicts_one_row_per_input(self):
        row = {
            "prod_year": 2015, "mileage_km": 120000.0, "engine_volume_l": 2.0,
            "cylinders": 4, "airbags": 8, "is_turbo": True, "has_leather": True,
            "doors": "4-5", "gearbox_type": "Automatic", "drive_wheels": "Rear",
            "manufacturer_name": "BMW", "category_name": "Sedan",
            "fuel_type_name": "Petrol",
        }
        out = pm.predict(pd.DataFrame([row, row]))
        assert len(out) == 2
    def test_prediction_is_a_plausible_price(self):
        row = {
            "prod_year": 2015, "mileage_km": 120000.0, "engine_volume_l": 2.0,
            "cylinders": 4, "airbags": 8, "is_turbo": True, "has_leather": True,
            "doors": "4-5", "gearbox_type": "Automatic", "drive_wheels": "Rear",
            "manufacturer_name": "BMW", "category_name": "Sedan",
            "fuel_type_name": "Petrol",
        }
        assert 500 < pm.predict(pd.DataFrame([row]))[0] < 500_000
    def test_missing_mileage_is_imputed_not_fatal(self):
        # 19 of 361 BMW rows have NULL mileage; the model must still score them
        row = {
            "prod_year": 2015, "mileage_km": None, "engine_volume_l": 2.0,
            "cylinders": 4, "airbags": 8, "is_turbo": False, "has_leather": True,
            "doors": "4-5", "gearbox_type": "Automatic", "drive_wheels": "Rear",
            "manufacturer_name": "BMW", "category_name": "Sedan",
            "fuel_type_name": "Petrol",
        }
        assert pm.predict(pd.DataFrame([row]))[0] > 0

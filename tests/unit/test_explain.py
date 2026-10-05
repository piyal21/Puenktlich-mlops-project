import numpy as np
import pytest

from dbdelay.features.build import build_features
from dbdelay.features.spec import FEATURE_COLUMNS
from dbdelay.registry.artifacts import ModelBundle, parse_bundle
from dbdelay.serving.explain import FACTOR_TEXT, TOP_K, factor_text, top_factors
from tests.builders import signal_frames
from tests.bundles import bundle_manifest

SAMPLE_VALUES: dict[str, object] = {
    "eva": "8000105",
    "train_type": "ICE",
    "line_key": "RE:RE1",
    "destination_key": "Berlin Hbf",
    "stop_index": 7,
    "hour_local": 8,
    "minute_of_day": 490,
    "weekday": 4,
    "is_weekend": False,
    "month": 3,
    "is_public_holiday": True,
}


@pytest.fixture(scope="module")
def bundle(bundle_files: dict[str, bytes]) -> ModelBundle:
    return parse_bundle(bundle_manifest(bundle_files), bundle_files)


def test_every_feature_has_text_for_both_directions() -> None:
    assert set(FACTOR_TEXT) == set(FEATURE_COLUMNS)
    for feature, value in SAMPLE_VALUES.items():
        for direction in ("up", "down"):
            text = factor_text(feature, direction, value)
            assert text
            assert "{" not in text


def test_values_are_human_readable() -> None:
    assert "Fridays" in factor_text("weekday", "up", 4)
    assert "08:00" in factor_text("hour_local", "down", 8)
    assert "March" in factor_text("month", "up", 3)
    assert "ICE" in factor_text("train_type", "up", "ICE")
    assert "OTHER" not in factor_text("train_type", "up", "OTHER")
    assert "Public holiday" in factor_text("is_public_holiday", "up", True)


def test_top_factors_follow_contribution_size_and_sign(bundle: ModelBundle) -> None:
    features = build_features(signal_frames()["test"].head(25), bundle.spec)
    factors = top_factors(bundle.booster, features)
    contrib = np.asarray(bundle.booster.predict(features, pred_contrib=True))[:, :-1]
    names = [str(n) for n in bundle.booster.feature_name()]
    assert len(factors) == len(features)
    for row, row_factors in zip(contrib, factors, strict=True):
        assert 1 <= len(row_factors) <= TOP_K
        sizes = [abs(row[names.index(f.feature)]) for f in row_factors]
        assert sizes == sorted(sizes, reverse=True)
        assert sizes[0] == pytest.approx(np.abs(row).max())
        for f in row_factors:
            assert (row[names.index(f.feature)] > 0) == (f.direction == "up")


def test_signal_model_explains_with_stop_index(bundle: ModelBundle) -> None:
    # The signal data's late risk is driven by stop_index (tests/builders.signal_day).
    features = build_features(signal_frames()["test"].head(25), bundle.spec)
    top = [row[0].feature for row in top_factors(bundle.booster, features)]
    assert top.count("stop_index") > len(top) / 2


def test_no_rows_no_factors(bundle: ModelBundle) -> None:
    features = build_features(signal_frames()["test"].head(0), bundle.spec)
    assert top_factors(bundle.booster, features) == []

import json
from pathlib import Path

import pytest

from dbdelay.config import get_settings
from dbdelay.data.stations import load_stations
from dbdelay.features.spec import FeatureSpec
from dbdelay.storage import ObjectStore
from dbdelay.training.baseline import BaselineModel
from dbdelay.training.run_baseline import main, run_baseline
from dbdelay.training.split import snapshot_prefix
from tests.builders import MARCH_CONFIG, put_march_silver

REPO = Path(__file__).resolve().parents[2]
STATIONS = load_stations(REPO / "configs" / "stations.yaml")


def test_run_baseline_writes_spec_model_and_metrics(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    report = run_baseline(s3_store, MARCH_CONFIG, STATIONS, tmp_path)

    assert set(report.splits) == {"valid", "test"}
    assert report.splits["valid"].overall.n == 24
    assert report.splits["test"].overall.n == 28
    prefix = snapshot_prefix(report.snapshot_id) + "baseline/"
    spec = FeatureSpec.from_json(s3_store.get_bytes(prefix + "feature_spec.json"))
    assert spec.spec_hash == report.spec_hash
    BaselineModel.from_json(s3_store.get_bytes(prefix + "baseline.json"))
    stored = json.loads(s3_store.get_bytes(prefix + "metrics.json"))
    assert stored["snapshot_id"] == report.snapshot_id


def test_run_baseline_is_deterministic(s3_store: ObjectStore, tmp_path: Path) -> None:
    put_march_silver(s3_store)
    first = run_baseline(s3_store, MARCH_CONFIG, STATIONS, tmp_path / "a")
    second = run_baseline(s3_store, MARCH_CONFIG, STATIONS, tmp_path / "b")
    assert first == second


def test_main_runs_end_to_end(
    s3_store: ObjectStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_BUCKET", s3_store.bucket)
    monkeypatch.setenv("STATIONS_FILE", str(REPO / "configs" / "stations.yaml"))
    get_settings.cache_clear()
    put_march_silver(s3_store)
    config = tmp_path / "training.yaml"
    config.write_text(
        "window_months: 1\nend_date: 2026-03-31\ntest_days: 7\nvalid_days: 7\n"
        "exclude_data_gaps: true\nfeatures: {min_count: 1}\nbaseline: {min_count: 2}\n"
        "evaluation: {ece_bins: 10, slice_min_rows: 1}\n",
        encoding="utf-8",
    )
    assert main(["--config", str(config)]) == 0
    keys = list(s3_store.iter_keys("gold/training_sets/"))
    assert sum(key.endswith("baseline/metrics.json") for key in keys) == 1

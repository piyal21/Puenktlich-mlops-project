from datetime import date
from pathlib import Path

import pytest

from dbdelay.errors import ConfigError
from dbdelay.training.config import load_training_config

REPO = Path(__file__).resolve().parents[2]

VALID = """
window_months: 9
end_date: null
test_days: 14
valid_days: 14
exclude_data_gaps: true
features: {min_count: 200}
baseline: {min_count: 50}
evaluation: {ece_bins: 10, slice_min_rows: 500}
seed: 42
lightgbm:
  num_threads: 8
  num_boost_round: 2000
  early_stopping_rounds: 50
  params: {objective: binary, feature_fraction: 0.9}
  grid: {num_leaves: [31, 127], learning_rate: [0.05, 0.1], min_data_in_leaf: [100, 500]}
risk_thresholds: {medium: 0.20, high: 0.45}
gate:
  min_brier_improvement_vs_baseline: 0.05
  max_brier_regression_vs_champion: 0.0
  max_auc_drop_vs_champion: 0.005
  max_slice_auc_drop: 0.02
  min_test_rows: 20000
registry: {model_name: puenktlich-delay, experiment: puenktlich-delay}
release: {reference_sample_rows: 50000}
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "training.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_repo_config_loads_with_spec_defaults() -> None:
    cfg = load_training_config(REPO / "configs" / "training.yaml")
    assert cfg.window_months == 9
    assert cfg.end_date is None
    assert (cfg.test_days, cfg.valid_days) == (14, 14)
    assert cfg.exclude_data_gaps is True
    assert cfg.features.min_count == 200
    assert cfg.baseline.min_count == 50
    assert (cfg.evaluation.ece_bins, cfg.evaluation.slice_min_rows) == (10, 500)


def test_end_date_is_parsed(tmp_path: Path) -> None:
    cfg = load_training_config(_write(tmp_path, VALID.replace("null", "2026-08-31")))
    assert cfg.end_date == date(2026, 8, 31)


@pytest.mark.parametrize(
    "broken",
    [
        VALID.replace("test_days: 14", "test_days: 0"),
        VALID.replace("ece_bins: 10", "ece_bins: 1"),
        VALID + "surprise: 1\n",
        VALID.replace("window_months: 9\n", ""),
        "window_months: [unclosed",
    ],
)
def test_invalid_config_raises_config_error(tmp_path: Path, broken: str) -> None:
    with pytest.raises(ConfigError):
        load_training_config(_write(tmp_path, broken))


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_training_config(tmp_path / "nope.yaml")


def test_repo_config_has_phase4_sections() -> None:
    cfg = load_training_config(REPO / "configs" / "training.yaml")
    assert cfg.seed == 42
    assert cfg.lightgbm.grid.num_leaves == (31, 127)
    assert cfg.lightgbm.params["objective"] == "binary"
    assert (cfg.risk_thresholds.medium, cfg.risk_thresholds.high) == (0.20, 0.45)
    assert cfg.gate.min_brier_improvement_vs_baseline == 0.05
    assert cfg.gate.min_test_rows == 20000
    assert cfg.registry.model_name == "puenktlich-delay"
    assert cfg.release.reference_sample_rows == 50000


def test_valid_text_loads(tmp_path: Path) -> None:
    cfg = load_training_config(_write(tmp_path, VALID))
    assert cfg.lightgbm.grid.learning_rate == (0.05, 0.1)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("learning_rate: [0.05, 0.1]", "learning_rate: [0, 0.1]"),
        ("num_leaves: [31, 127]", "num_leaves: []"),
        ("{objective: binary, feature_fraction: 0.9}", "{objective: binary, seed: 1}"),
        ("{objective: binary, feature_fraction: 0.9}", "{objective: regression}"),
        ("{medium: 0.20, high: 0.45}", "{medium: 0.5, high: 0.45}"),
        ("min_test_rows: 20000", "min_test_rows: 0"),
        ("model_name: puenktlich-delay", "model_name: Bad Name"),
        ("seed: 42", "seed: -1"),
    ],
)
def test_bad_phase4_values_raise_config_error(tmp_path: Path, old: str, new: str) -> None:
    assert old in VALID
    with pytest.raises(ConfigError):
        load_training_config(_write(tmp_path, VALID.replace(old, new)))

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

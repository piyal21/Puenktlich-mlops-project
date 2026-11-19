from pathlib import Path

import pytest

from dbdelay.errors import ConfigError
from dbdelay.monitoring.config import load_monitoring_config

REPO = Path(__file__).resolve().parents[2]


def test_loads_repo_config() -> None:
    cfg = load_monitoring_config(REPO / "configs" / "monitoring.yaml")
    assert cfg.drift_threshold == 0.1
    assert cfg.drift_share_max == 0.30
    assert cfg.brier_ratio_max == 1.10
    assert cfg.consecutive_days == 3
    assert cfg.min_labeled_rows == 2000
    assert cfg.ece_bins == 10
    assert cfg.history_max_days == 366


def test_rejects_unknown_key(tmp_path: Path) -> None:
    text = (REPO / "configs" / "monitoring.yaml").read_text(encoding="utf-8") + "\nextra: 1\n"
    path = tmp_path / "monitoring.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_monitoring_config(path)


def test_missing_file_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_monitoring_config(tmp_path / "nope.yaml")

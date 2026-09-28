from pathlib import Path

import pytest

from dbdelay.data.stations import alias_map, load_stations
from dbdelay.errors import ConfigError

REPO_STATIONS = Path(__file__).resolve().parents[2] / "configs" / "stations.yaml"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "stations.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_repo_station_list_loads() -> None:
    stations = load_stations(REPO_STATIONS)
    assert len(stations) == 30
    berlin = next(s for s in stations if s.eva == "8098160")
    assert berlin.hf_aliases == ("8011160",)


def test_alias_map_points_aliases_and_evas_to_the_canonical_eva() -> None:
    mapping = alias_map(load_stations(REPO_STATIONS))
    assert mapping["8011160"] == "8098160"
    assert mapping["8098160"] == "8098160"
    assert len(mapping) == 31


@pytest.mark.parametrize(
    "body",
    [
        'schema_version: 1\nstations:\n  - {eva: "08000105", name: "F", state: "HE"}\n',
        'schema_version: 1\nstations:\n  - {eva: "8000105", name: "F", state: "HE", x: 1}\n',
        "schema_version: 2\nstations: []\n",
        'schema_version: 1\nstations:\n  - {eva: "8000105", name: "F", state: "HE"}\n'
        '  - {eva: "8000261", name: "M", state: "BY", hf_aliases: ["8000105"]}\n',
    ],
)
def test_invalid_station_files_raise_config_error(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError):
        load_stations(_write(tmp_path, body))


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_stations(tmp_path / "nope.yaml")

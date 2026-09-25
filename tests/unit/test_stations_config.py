"""Guards for the frozen station list (configs/stations.yaml)."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

STATIONS_FILE = Path(__file__).resolve().parents[2] / "configs" / "stations.yaml"
GERMAN_STATES = {
    "BB", "BE", "BW", "BY", "HB", "HE", "HH", "MV",
    "NI", "NW", "RP", "SH", "SL", "SN", "ST", "TH",
}  # fmt: skip
EVA_PATTERN = re.compile(r"^[1-9][0-9]{6}$")  # same rule as the silver contract
MAX_STATIONS = 30  # DB API budget: 50 calls/min → ~30 stations per 15-min run


@pytest.fixture(scope="module")
def stations() -> list[dict[str, Any]]:
    config = yaml.safe_load(STATIONS_FILE.read_text(encoding="utf-8"))
    assert config["schema_version"] == 1
    return list(config["stations"])


def test_every_station_has_eva_name_state_and_optional_aliases(
    stations: list[dict[str, Any]],
) -> None:
    for station in stations:
        assert {"eva", "name", "state"} <= set(station) <= {"eva", "name", "state", "hf_aliases"}


def test_evas_and_aliases_use_api_form_and_are_unique(stations: list[dict[str, Any]]) -> None:
    # An alias maps a retired historical EVA onto the station's current one; it must never
    # collide with another station or alias, or two stations would share history.
    evas = [s["eva"] for s in stations] + [a for s in stations for a in s.get("hf_aliases", [])]
    assert all(isinstance(e, str) and EVA_PATTERN.fullmatch(e) for e in evas), evas
    assert len(set(evas)) == len(evas)


def test_berlin_hbf_history_includes_its_retired_upper_level_eva(
    stations: list[dict[str, Any]],
) -> None:
    # EDA §8: DB merged 8011160 into 8098160 during 2026; live 8011160 is empty.
    berlin = next(s for s in stations if s["eva"] == "8098160")
    assert berlin["hf_aliases"] == ["8011160"]


def test_states_are_valid_and_all_covered(stations: list[dict[str, Any]]) -> None:
    states = {s["state"] for s in stations}
    assert states == GERMAN_STATES


def test_station_count_fits_the_api_budget(stations: list[dict[str, Any]]) -> None:
    assert 0 < len(stations) <= MAX_STATIONS

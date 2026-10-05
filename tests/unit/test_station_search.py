from pathlib import Path

import pytest

from dbdelay.data.stations import load_stations
from dbdelay.serving.stations import search_stations

STATIONS = load_stations(Path(__file__).resolve().parents[2] / "configs" / "stations.yaml")


def names(query: str) -> list[str]:
    return [s.name for s in search_stations(STATIONS, query)]


def test_empty_query_lists_all_sorted() -> None:
    result = names("")
    assert len(result) == len(STATIONS)
    assert result == sorted(result)


@pytest.mark.parametrize("query", ["München", "munchen", "MUENCHEN", "  münchen  "])
def test_station_search_umlaut_spellings(query: str) -> None:
    assert names(query) == ["München Hbf"]


def test_prefix_matches_come_first() -> None:
    assert "Hamburg Hbf" in names("hbf")
    assert names("ham")[0] == "Hamburg Hbf"


def test_punctuation_is_ignored() -> None:
    assert names("frankfurt main") == ["Frankfurt (Main) Hbf"]


@pytest.mark.parametrize("query", ["(", "%", "🚆", "zzz"])
def test_station_search_odd_input(query: str) -> None:
    assert names(query) in ([], names(""))

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from dbdelay.data.stations import Station
from dbdelay.live.hf_raw import hf_raw_paths, iter_hf_raw
from dbdelay.live.parity import build_hf_raw_silver

BASE = "https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1"


def _write(path: Path, rows: list[dict[str, object]]) -> Path:
    pd.DataFrame(rows).to_parquet(path)
    return path


def _row(
    ts: str, url: str, api: str, body: str | None = "<timetable/>", status: str | None = "200"
):
    return {
        "timestamp": pd.Timestamp(ts),
        "url": url,
        "api_name": api,
        "query_params": None,
        "response_data": body,
        "status_code": status,
        "error": None,
    }


def test_iter_hf_raw_filters_evas_maps_aliases_and_orders(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "raw.parquet",
        [
            _row("2026-08-24 16:04:26", f"{BASE}/plan/08000199/260824/15", "timetables/v1/plan"),
            _row("2026-08-24 15:59:34", f"{BASE}/fchg/08000199", "timetables/v1/fchg"),
            _row("2026-08-24 15:59:35", f"{BASE}/fchg/08011160", "timetables/v1/fchg"),
            _row("2026-08-24 15:59:36", f"{BASE}/fchg/08000105", "timetables/v1/fchg"),
            _row("2026-08-24 15:59:37", f"{BASE}/fchg/08000199", "timetables/v1/fchg", None, None),
            _row("2026-08-24 15:54:47", "https://x/fasta/v2/facilities", "fasta/v2/facilities"),
        ],
    )
    eva_map = {"8000199": "8000199", "8098160": "8098160", "8011160": "8098160"}
    got = list(iter_hf_raw(path, eva_map))
    assert [(kind, eva) for kind, eva, _, _ in got] == [
        ("fchg", "8000199"),
        ("fchg", "8098160"),  # retired Berlin EVA mapped to the current one
        ("plan", "8000199"),
    ]
    assert got[0][2] == datetime(2026, 8, 24, 15, 59, 34, tzinfo=UTC)
    assert got[0][3] == b"<timetable/>"


@dataclass
class _Entry:
    path: str


class _FakeApi:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def list_repo_tree(self, repo_id: str, *, path_in_repo: str, repo_type: str) -> list[_Entry]:
        assert repo_type == "dataset"
        self.asked.append(path_in_repo)
        return [_Entry(f"{path_in_repo}/a.parquet"), _Entry(f"{path_in_repo}/notes.txt")]


def test_hf_raw_paths_adds_next_day_with_unpadded_folders() -> None:
    api = _FakeApi()
    paths = hf_raw_paths(date(2026, 8, 31), api=api)
    assert api.asked == [
        "raw_data/year=2026/month=8/day=31",
        "raw_data/year=2026/month=9/day=1",
    ]
    assert paths == [
        "raw_data/year=2026/month=8/day=31/a.parquet",
        "raw_data/year=2026/month=9/day=1/a.parquet",
    ]


def test_build_hf_raw_silver_from_kiel_fixtures(tmp_path: Path) -> None:

    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    plan = (fixtures / "sample_plan.xml").read_text(encoding="utf-8")
    fchg = (fixtures / "sample_fchg.xml").read_text(encoding="utf-8")
    path = _write(
        tmp_path / "raw.parquet",
        [
            _row(
                "2026-09-25 15:00:00", f"{BASE}/plan/08000199/260925/17", "timetables/v1/plan", plan
            ),
            _row("2026-09-25 15:10:00", f"{BASE}/fchg/08000199", "timetables/v1/fchg", fchg),
        ],
    )
    stations = [Station(eva="8000199", name="Kiel Hbf", state="SH")]
    silver, stats = build_hf_raw_silver([path], stations, date(2026, 9, 25))
    # 9 planned departures of that hour + 1 fchg-only stop that carries planned time and train
    assert len(silver) == 10
    assert stats["responses"] == 2
    assert stats["parse_errors"] == 0
    assert silver["source"].eq("live").all()

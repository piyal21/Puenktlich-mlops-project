import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from dbdelay.data.live_report import live_silver_key
from dbdelay.data.silver import SILVER_ARROW_SCHEMA, to_parquet_bytes
from dbdelay.data.stations import Station
from dbdelay.live import parity
from dbdelay.live.conform import StopMerger, to_silver
from dbdelay.storage import ObjectStore
from dbdelay.timetables.parser import StopEvent

DAY = date(2026, 11, 15)
STATIONS = [Station(eva="8000199", name="Kiel Hbf", state="SH")]


def silver(n: int) -> pd.DataFrame:
    merger = StopMerger()
    for i in range(n):
        merger.add_plan(
            StopEvent(
                stop_id=f"-{i}-2611150900-1", ride_id=f"-{i}-2611150900", stop_index=1,
                eva="8000199", train_type="RE", train_number=str(i), line_number=None,
                planned_utc=datetime(2026, 11, 15, 9, i, tzinfo=UTC), changed_utc=None,
                cancelled=False, planned_platform=None, changed_platform=None,
                planned_path=("X",), changed_path=None, distant_endpoint=None,
            ),
            datetime(2026, 11, 15, 8, tzinfo=UTC),
        )  # fmt: skip
    return to_silver(merger.stops(), STATIONS, DAY)[0]


@pytest.fixture
def wired(s3_store: ObjectStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    hf = {"rows": 20}
    monkeypatch.setattr(parity, "hf_raw_paths", lambda day: ["raw_data/x.parquet"])
    monkeypatch.setattr(parity, "hf_download", lambda path, dest: (tmp_path / "x.parquet", "rev"))
    monkeypatch.setattr(parity, "load_stations", lambda path: STATIONS)
    monkeypatch.setattr(
        parity, "build_hf_raw_silver", lambda paths, stations, day: (silver(hf["rows"]), {})
    )
    monkeypatch.setattr(parity, "make_aws_s3_client", lambda region: s3_store._client)
    monkeypatch.setattr(parity, "OUT_DIR", tmp_path / "out")
    return hf


ARGS = ["--days", "2026-11-15", "--data-bucket", "puenktlich-test", "--region", "eu-north-1"]


def test_reconcile_passes_writes_local_and_s3_reports(
    wired: dict[str, Any], s3_store: ObjectStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s3_store.put_bytes(live_silver_key(DAY), to_parquet_bytes(silver(20), SILVER_ARROW_SCHEMA))
    assert parity.reconcile_main(ARGS) == 0
    local = json.loads((tmp_path / "out" / "2026-11-15.json").read_text(encoding="utf-8"))
    assert local["matched"] == 20
    stored = s3_store.get_bytes("silver/_quality/source=live/reconciliation/date=2026-11-15.json")
    assert json.loads(stored)["match_rate_right"] == 1.0
    assert "PASS 2026-11-15" in capsys.readouterr().out


def test_reconcile_below_threshold_exits_1(
    wired: dict[str, Any], s3_store: ObjectStore, capsys: pytest.CaptureFixture[str]
) -> None:
    s3_store.put_bytes(live_silver_key(DAY), to_parquet_bytes(silver(18), SILVER_ARROW_SCHEMA))
    assert parity.reconcile_main(ARGS) == 1  # 18 of 20 HF rows = 90 %
    assert "FAIL 2026-11-15" in capsys.readouterr().out


def test_reconcile_missing_live_day_exits_1(
    wired: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert parity.reconcile_main(ARGS) == 1
    assert "reconcile failed" in capsys.readouterr().err

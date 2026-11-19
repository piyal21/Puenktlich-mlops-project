import io
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from dbdelay.data.live_report import LiveQualityReport, live_quality_key, live_silver_key
from dbdelay.errors import ModelNotAvailableError
from dbdelay.features.build import build_features
from dbdelay.features.spec import FEATURE_COLUMNS, FeatureSpec
from dbdelay.monitoring.config import load_monitoring_config
from dbdelay.monitoring.history import HistoryEntry, Verdict
from dbdelay.monitoring.job import FLAG_KEY, HISTORY_KEY, MonitorOutcome, daily_key, run_monitor
from dbdelay.monitoring.triggers import RetrainRequest, ThresholdOverrides
from dbdelay.registry.artifacts import write_bundle
from dbdelay.registry.pointer import ObjectStorePointer
from dbdelay.storage import ObjectStore
from tests.builders import signal_day, signal_frames
from tests.bundles import bundle_manifest

CFG = load_monitoring_config(
    Path(__file__).resolve().parents[2] / "configs" / "monitoring.yaml"
).model_copy(update={"min_labeled_rows": 50})
DAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 17, 3, 30, tzinfo=UTC)


def _report(day: date, **kw: Any) -> LiveQualityReport:
    base: dict[str, Any] = {
        "day": day,
        "rows_out": 0,
        "drops": {},
        "late_rate": None,
        "cancelled_rate": None,
        "stations": [],
        "low_volume_hours": [],
        "fchg_runs_ok": {},
        "longest_fchg_gap_min": {},
        "plan_hours_missing": {},
        "never_seen_in_fchg": 0,
        "fchg_only_stops": 0,
        "empty_stations": [],
        "content_hash": "x",
    }
    return LiveQualityReport.model_validate(base | kw)


@pytest.fixture
def stores(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> ObjectStore:
    spec = FeatureSpec.model_validate_json(bundle_files["feature_spec.json"])
    train = signal_frames()["train"]
    ref = build_features(train, spec).reset_index(drop=True)
    ref["is_late"] = train["is_late"].to_numpy(dtype=bool)
    ref["p_late"] = np.linspace(0.05, 0.9, len(ref))
    buffer = io.BytesIO()
    ref.to_parquet(buffer, index=False)
    files = bundle_files | {"reference_sample.parquet": buffer.getvalue()}
    write_bundle(s3_store, bundle_manifest(files), files)
    ObjectStorePointer(s3_store).set("1", None)
    return s3_store


def _put_silver(store: ObjectStore, day: date, silver: pd.DataFrame, **report: Any) -> None:
    buffer = io.BytesIO()
    silver.to_parquet(buffer, index=False)
    store.put_bytes(live_silver_key(day), buffer.getvalue())
    store.put_bytes(live_quality_key(day), _report(day, **report).model_dump_json().encode())


def _put_day(store: ObjectStore, day: date, n: int = 200, **report: Any) -> pd.DataFrame:
    silver = signal_day(f"{day:%Y-%m-%d}", n)
    _put_silver(store, day, silver, **report)
    return silver


def _run(store: ObjectStore, *, now: datetime = NOW, **kw: Any) -> MonitorOutcome:
    cfg = kw.pop("cfg", CFG)
    return run_monitor(
        data=store,
        models=store,
        pointer=ObjectStorePointer(store),
        day=DAY,
        cfg=cfg,
        now=now,
        **kw,
    )


def _history(store: ObjectStore) -> list[HistoryEntry]:
    return [HistoryEntry.model_validate(e) for e in json.loads(store.get_bytes(HISTORY_KEY))]


def _entry(day: date, verdict: Verdict, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "day": day,
        "status": "ok",
        "model_version": "1",
        "rows": 500,
        "brier": 0.3,
        "roc_auc": 0.7,
        "ece": 0.1,
        "late_rate": 0.4,
        "mean_p_late": 0.2,
        "brier_ratio": 2.0,
        "drift_share": 0.0,
        "prediction_drift": 0.0,
        "verdict": verdict,
    }
    return HistoryEntry.model_validate(base | kw).model_dump(mode="json")


def _seed_history(store: ObjectStore, verdict: Verdict, **kw: Any) -> None:
    days = [DAY - timedelta(days=2), DAY - timedelta(days=1)]
    store.put_bytes(HISTORY_KEY, json.dumps([_entry(d, verdict, **kw) for d in days]).encode())


BAD = Verdict(drift_bad=False, performance_bad=True, drift_share_max=0.3, brier_ratio_max=1.1)
GOOD = Verdict(drift_bad=False, performance_bad=False, drift_share_max=0.3, brier_ratio_max=1.1)
STRICT = CFG.model_copy(update={"brier_ratio_max": 0.01})


def test_normal_day_writes_daily_and_history(stores: ObjectStore) -> None:
    _put_day(stores, DAY)
    outcome = _run(stores)
    r = outcome.result
    assert r.status == "ok"
    assert r.model_version == "1"
    assert r.performance.rows == 200
    assert r.champion_test_brier == 0.14
    assert r.performance.brier is not None
    assert r.brier_ratio == pytest.approx(r.performance.brier / 0.14)
    assert r.drift is not None
    assert {c.column for c in r.drift.columns} == set(FEATURE_COLUMNS)
    assert json.loads(stores.get_bytes(daily_key(DAY)))["day"] == "2026-11-16"
    assert [e.day for e in _history(stores)] == [DAY]
    assert outcome.flag_written is None


def test_no_silver_is_no_data(stores: ObjectStore) -> None:
    r = _run(stores).result
    assert r.status == "no_data"
    assert r.performance.rows == 0
    assert r.drift is None
    assert r.brier_ratio is None
    assert not r.verdict.drift_bad and not r.verdict.performance_bad


def test_no_champion_raises(s3_store: ObjectStore) -> None:
    with pytest.raises(ModelNotAvailableError):
        _run(s3_store)


def test_cancelled_rows_excluded(stores: ObjectStore) -> None:
    silver = signal_day(f"{DAY:%Y-%m-%d}", 200)
    silver.loc[:9, "is_late"] = pd.NA
    _put_silver(stores, DAY, silver)
    r = _run(stores).result
    assert r.excluded_rows["cancelled"] == 10
    assert r.performance.rows == 190


def test_gap_rows_excluded(stores: ObjectStore) -> None:
    silver = _put_day(stores, DAY, low_volume_hours=["2026-11-16T10"])
    local = silver["planned_departure_utc"].dt.tz_convert("Europe/Berlin")
    in_hour = int(((local.dt.hour == 10) & (local.dt.day == 16)).sum())
    assert in_hour > 0
    r = _run(stores).result
    assert r.excluded_rows["gap_hour"] == in_hour
    assert r.performance.rows == 200 - in_hour


def test_too_few_rows(stores: ObjectStore) -> None:
    _put_day(stores, DAY, n=20)
    r = _run(stores).result
    assert r.status == "too_few_rows"
    assert r.performance.rows == 20
    assert r.drift is None


def test_rerun_same_day_idempotent(stores: ObjectStore) -> None:
    _put_day(stores, DAY)
    _run(stores)
    _run(stores)
    assert [e.day for e in _history(stores)] == [DAY]


def test_flag_written_after_three_bad_days(stores: ObjectStore) -> None:
    _seed_history(stores, BAD)
    _put_day(stores, DAY)
    outcome = _run(stores, cfg=STRICT)
    assert outcome.flag_written is not None
    flag = RetrainRequest.model_validate_json(stores.get_bytes(FLAG_KEY))
    assert flag.reason == "performance"
    assert flag.state == "requested"
    assert not flag.forced


def test_flag_not_overwritten_while_requested(stores: ObjectStore) -> None:
    _seed_history(stores, BAD)
    _put_day(stores, DAY)
    first = _run(stores, cfg=STRICT).flag_written
    assert first is not None
    again = _run(stores, cfg=STRICT, now=NOW + timedelta(days=1))
    assert again.flag_written is None
    assert RetrainRequest.model_validate_json(stores.get_bytes(FLAG_KEY)) == first


def test_flag_replaced_once_done(stores: ObjectStore) -> None:
    _seed_history(stores, BAD)
    _put_day(stores, DAY)
    first = _run(stores, cfg=STRICT).flag_written
    assert first is not None
    done = first.model_copy(update={"state": "done"})
    stores.put_bytes(FLAG_KEY, done.model_dump_json().encode())
    assert _run(stores, cfg=STRICT, now=NOW + timedelta(days=1)).flag_written is not None


def test_forced_flag_keeps_history_verdicts(stores: ObjectStore) -> None:
    _seed_history(stores, GOOD, brier=0.1, brier_ratio=0.9, drift_share=0.2)
    _put_day(stores, DAY)
    outcome = _run(stores, overrides=ThresholdOverrides(drift_share_max=-1.0))
    assert outcome.flag_written is not None
    assert outcome.flag_written.forced
    assert all(not e.verdict.drift_bad for e in _history(stores))


def test_all_rows_in_gaps_is_too_few_rows(stores: ObjectStore) -> None:
    # Real case (2026-11-13, the first live day): every station-day was flagged as a gap.
    evas = ["8000105", "8000261"]  # SIGNAL_STATIONS
    stations = [{"eva": eva, "rows": 0, "volume_drop_days": ["2026-11-16"]} for eva in evas]
    silver = _put_day(stores, DAY, stations=stations)
    assert set(silver["eva"]) <= set(evas)
    r = _run(stores).result
    assert r.status == "too_few_rows"
    assert r.performance.rows == 0
    assert r.performance.brier is None
    assert r.excluded_rows["gap_station_day"] == 200

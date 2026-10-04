import json
from datetime import timedelta

import pandas as pd
import pytest

from dbdelay.errors import ModelNotAvailableError
from dbdelay.features.spec import RiskThresholds, require_columns
from dbdelay.registry.artifacts import ModelBundle, parse_bundle
from dbdelay.serving.scoring import (
    ScheduleInput,
    predict_one,
    risk_level,
    schedule_frame,
    score,
    score_board,
)
from tests.boards import NOW, departure
from tests.builders import signal_frames
from tests.bundles import bundle_manifest

THRESHOLDS = RiskThresholds(medium=0.2, high=0.45)


class StaticModels:
    def __init__(self, bundle: ModelBundle | None) -> None:
        self.bundle = bundle

    def get(self) -> ModelBundle:
        if self.bundle is None:
            raise ModelNotAvailableError("no champion model released yet")
        return self.bundle


@pytest.fixture(scope="module")
def bundle(bundle_files: dict[str, bytes]) -> ModelBundle:
    return parse_bundle(bundle_manifest(bundle_files), bundle_files)


def _inputs(n: int = 20) -> list[ScheduleInput]:
    rows = signal_frames()["test"].head(n)
    return [
        ScheduleInput(
            eva=str(r["eva"]),
            train_type=str(r["train_type"]),
            line_number=None if pd.isna(r["line_number"]) else str(r["line_number"]),
            final_destination=str(r["final_destination"]),
            stop_index=int(r["stop_index"]),
            planned_departure_utc=pd.Timestamp(r["planned_departure_utc"]).to_pydatetime(),
        )
        for r in rows.to_dict("records")
    ]


@pytest.mark.parametrize(
    ("p", "level"),
    [
        (0.0, "low"),
        (0.1999, "low"),
        (0.2, "medium"),
        (0.4499, "medium"),
        (0.45, "high"),
        (1.0, "high"),
    ],
)
def test_risk_level_boundaries(p: float, level: str) -> None:
    assert risk_level(p, THRESHOLDS) == level


def test_schedule_frame_meets_feature_contract() -> None:
    frame = schedule_frame(_inputs(3))
    require_columns(frame)
    assert str(frame["planned_departure_utc"].dt.tz) == "UTC"
    assert frame["stop_index"].dtype == "int16"


def test_score_matches_bundle_predict(bundle: ModelBundle) -> None:
    inputs = _inputs()
    predictions = score(bundle, inputs)
    expected = bundle.predict(schedule_frame(inputs))
    assert [p.p_late for p in predictions] == pytest.approx(expected.tolist(), abs=1e-4)
    assert all(1 <= len(p.factors) <= 3 for p in predictions)


def test_score_without_thresholds_is_unavailable(bundle: ModelBundle) -> None:
    spec = bundle.spec.model_copy(update={"risk_thresholds": None})
    no_thresholds = ModelBundle(
        bundle.manifest, bundle.booster, bundle.calibrator, spec, bundle.report
    )
    with pytest.raises(ModelNotAvailableError, match="risk thresholds"):
        score(no_thresholds, _inputs(1))


def test_board_without_model_keeps_rows() -> None:
    rows = [departure(5), departure(9)]
    result = score_board(StaticModels(None), rows, request_id="r1")
    assert result.model_version is None
    assert result.predictions == [None, None]


def test_board_skips_cancelled_and_logs_each_prediction(
    bundle: ModelBundle, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [
        departure(5),
        departure(9, is_cancelled=True, changed_departure_utc=None, delay_min=None),
        departure(12),
    ]
    result = score_board(StaticModels(bundle), rows, request_id="r1")
    assert result.model_version == "1"
    assert result.predictions[1] is None
    assert result.predictions[0] is not None
    assert result.predictions[2] is not None
    logs = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    lines = [entry for entry in logs if entry.get("message") == "prediction"]
    assert [entry["event_id"] for entry in lines] == [rows[0].event_id, rows[2].event_id]
    assert {entry["request_id"] for entry in lines} == {"r1"}
    assert all("latency_ms" in entry and entry["model_version"] == "1" for entry in lines)


def test_predict_one_needs_a_model(bundle: ModelBundle) -> None:
    item = ScheduleInput.from_board(departure(5))
    version, prediction = predict_one(StaticModels(bundle), item, request_id="r2")
    assert version == "1"
    assert 0.0 <= prediction.p_late <= 1.0
    with pytest.raises(ModelNotAvailableError):
        predict_one(StaticModels(None), item, request_id="r2")


def test_from_board_keeps_only_timetable_fields() -> None:
    row = departure(5, delay_min=12, changed_departure_utc=NOW + timedelta(minutes=17))
    item = ScheduleInput.from_board(row)
    assert item.planned_departure_utc == row.planned_departure_utc
    assert not hasattr(item, "delay_min")
    assert isinstance(schedule_frame([item]), pd.DataFrame)

from datetime import datetime

import pandas as pd
import pytest

from dbdelay.data.quality import QUARANTINE_REASONS, split_quarantine
from tests.builders import hf_frame, hf_row


def test_valid_rows_pass_through_unchanged() -> None:
    raw = hf_frame(
        hf_row(),
        hf_row(
            id="1-2603100815-2",
            delay_in_min=7,
            departure_change_time=datetime(2026, 3, 10, 8, 22),
        ),
    )
    ok, quarantined = split_quarantine(raw)
    pd.testing.assert_frame_equal(ok, raw)
    assert quarantined.empty
    assert "quarantine_reason" in quarantined.columns


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"id": "abc"}, "bad_id"),
        ({"id": None}, "bad_id"),
        ({"id": "1-2603100815-0"}, "bad_id"),
        ({"train_type": None}, "missing_train_type"),
        ({"train_type": "  "}, "missing_train_type"),
        ({"departure_is_canceled": None}, "missing_cancel_flag"),
        ({"delay_in_min": None}, "missing_delay"),
        ({"delay_in_min": 3}, "delay_mismatch"),
        ({"departure_change_time": None}, "delay_mismatch"),
    ],
)
def test_each_rule_quarantines_with_its_reason(overrides: dict[str, object], reason: str) -> None:
    ok, quarantined = split_quarantine(hf_frame(hf_row(**overrides)))
    assert ok.empty
    assert quarantined["quarantine_reason"].tolist() == [reason]


def test_first_reason_wins() -> None:
    _, quarantined = split_quarantine(hf_frame(hf_row(id="bad", train_type=None)))
    assert quarantined["quarantine_reason"].tolist() == ["bad_id"]


def test_cancelled_rows_need_no_delay_or_change_time() -> None:
    raw = hf_frame(
        hf_row(departure_is_canceled=True, delay_in_min=None, departure_change_time=None)
    )
    ok, quarantined = split_quarantine(raw)
    assert len(ok) == 1
    assert quarantined.empty


def test_reason_catalogue_is_stable() -> None:
    assert QUARANTINE_REASONS == (
        "bad_id",
        "missing_train_type",
        "missing_cancel_flag",
        "missing_delay",
        "delay_mismatch",
    )

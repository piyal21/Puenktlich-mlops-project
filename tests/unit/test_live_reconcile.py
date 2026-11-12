import json
from collections.abc import Sequence
from datetime import date

import pandas as pd
import pytest

from dbdelay.live.reconcile import compare_silver

DAY = date(2026, 8, 24)


def frame(rows: Sequence[tuple[str, str, int | None, bool, str | None]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": [r[0] for r in rows],
            "eva": [r[1] for r in rows],
            "delay_min": pd.array([r[2] for r in rows], dtype="Int16"),
            "is_late": pd.array(
                [None if r[2] is None else r[2] >= 6 for r in rows], dtype="boolean"
            ),
            "is_cancelled": [r[3] for r in rows],
            "final_destination": [r[4] for r in rows],
        }
    )


BASE = [
    ("a", "8000199", 0, False, "Hamburg Hbf"),
    ("b", "8000199", 7, False, "Lübeck Hbf"),
    ("c", "8000105", None, True, "Fulda"),
]


def test_compare_identical_frames() -> None:
    c = compare_silver(frame(BASE), frame(BASE), DAY)
    assert (c.left_rows, c.right_rows, c.matched) == (3, 3, 3)
    assert c.match_rate_left == c.match_rate_right == 1.0
    assert c.is_late_agreement == c.delay_exact == 1.0
    assert c.cancelled_agreement == c.destination_agreement == 1.0
    assert json.loads(c.to_json())["matched"] == 3


def test_compare_counts_missing_both_ways() -> None:
    left = frame([*BASE, ("d", "8000199", 0, False, "X")])
    right = frame([BASE[0], BASE[1]])
    c = compare_silver(left, right, DAY)
    assert c.matched == 2
    assert c.match_rate_left == pytest.approx(2 / 4)
    assert c.match_rate_right == 1.0
    assert c.per_station["8000105"]["left_only"] == 1


def test_is_late_agreement_ignores_cancelled() -> None:
    left = frame([("a", "8000199", 8, False, "H"), ("c", "8000105", None, True, "F")])
    right = frame([("a", "8000199", 2, False, "H"), ("c", "8000105", 0, False, "F")])
    c = compare_silver(left, right, DAY)
    assert c.is_late_agreement == 0.0  # only "a" counts: both sides not cancelled
    assert c.cancelled_agreement == 0.5
    assert c.delay_exact == 0.0


def test_compare_empty_has_no_rates() -> None:
    c = compare_silver(frame([]), frame([]), DAY)
    assert c.matched == 0
    assert c.is_late_agreement is None
    assert c.match_rate_left is None

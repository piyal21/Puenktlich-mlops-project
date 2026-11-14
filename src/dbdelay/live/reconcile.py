"""Compare two silver frames for one day on ``event_id`` (parity and reconciliation, Phase 7)."""

import json
from dataclasses import asdict, dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class Comparison:
    day: date
    left_rows: int
    right_rows: int
    matched: int
    match_rate_left: float | None  # share of left rows found on the right
    match_rate_right: float | None  # share of right rows found on the left
    is_late_agreement: float | None  # matched rows not cancelled on either side
    delay_exact: float | None  # same set as is_late_agreement
    cancelled_agreement: float | None
    destination_agreement: float | None
    per_station: dict[str, dict[str, int]]

    def to_json(self) -> str:
        return json.dumps(asdict(self) | {"day": self.day.isoformat()}, indent=2)


def _rate(hits: "pd.Series[bool]") -> float | None:
    return None if hits.empty else float(hits.mean())


def compare_silver(left: pd.DataFrame, right: pd.DataFrame, day: date) -> Comparison:
    """Join on ``event_id``; agreement rates are over matched rows (``None`` if none)."""
    cols = ["event_id", "eva", "delay_min", "is_late", "is_cancelled", "final_destination"]
    joined = left[cols].merge(
        right[cols], on="event_id", how="outer", suffixes=("_l", "_r"), indicator=True
    )
    both = joined[joined["_merge"] == "both"]
    labelled = both[~both["is_cancelled_l"].astype(bool) & ~both["is_cancelled_r"].astype(bool)]
    destination_l = both["final_destination_l"].fillna("")
    destination_r = both["final_destination_r"].fillna("")

    per_station: dict[str, dict[str, int]] = {}
    station = joined["eva_l"].fillna(joined["eva_r"])
    for eva, part in joined.groupby(station):
        counts = part["_merge"].value_counts()
        per_station[str(eva)] = {
            "matched": int(counts.get("both", 0)),
            "left_only": int(counts.get("left_only", 0)),
            "right_only": int(counts.get("right_only", 0)),
        }
    matched = len(both)
    return Comparison(
        day=day,
        left_rows=len(left),
        right_rows=len(right),
        matched=matched,
        match_rate_left=matched / len(left) if len(left) else None,
        match_rate_right=matched / len(right) if len(right) else None,
        is_late_agreement=_rate(
            (labelled["is_late_l"] == labelled["is_late_r"]).fillna(False).astype(bool)
        ),
        delay_exact=_rate(
            (labelled["delay_min_l"] == labelled["delay_min_r"]).fillna(False).astype(bool)
        ),
        cancelled_agreement=_rate(
            both["is_cancelled_l"].astype(bool) == both["is_cancelled_r"].astype(bool)
        ),
        destination_agreement=_rate(destination_l == destination_r),
        per_station=per_station,
    )


# Spec §4 item 4 (owner-approved 2026-11-12): HF departures found in live silver, and is_late
# agreement on matched non-cancelled rows. Live is the left side, HF the right side.
THRESHOLD = 0.95


def verdict(comparison: Comparison) -> bool:
    """True when live silver meets the reconciliation threshold against HF for that day."""
    found, agree = comparison.match_rate_right, comparison.is_late_agreement
    return found is not None and agree is not None and found >= THRESHOLD and agree >= THRESHOLD

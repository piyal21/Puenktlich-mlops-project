"""Plain-language reasons: the top LightGBM feature contributions per departure (architecture §7).

Contributions are in raw log-odds space; isotonic calibration is monotone, so their direction
(raises / lowers the risk) holds for the calibrated ``p_late`` too.
"""

from dataclasses import dataclass
from typing import Literal

import lightgbm as lgb
import numpy as np
import pandas as pd

from dbdelay.features.spec import OTHER

Direction = Literal["up", "down"]
TOP_K = 3
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
_UNKNOWN = {OTHER, "none"}

# feature -> (text when it raises the risk, text when it lowers it); {value} from _value_text.
FACTOR_TEXT: dict[str, tuple[str, str]] = {
    "eva": (
        "Departures from this station are often late",
        "Departures from this station are usually on time",
    ),
    "train_type": ("{value} trains are often late here", "{value} trains are usually on time here"),
    "line_key": ("This line often runs late", "This line usually runs on time"),
    "destination_key": (
        "Trains to this destination are often late",
        "Trains to this destination are usually on time",
    ),
    "stop_index": (
        "Late stop in a long journey, so delays have had time to build up",
        "Early stop in the journey, so there is little delay to pick up yet",
    ),
    "hour_local": (
        "Departures around {value} are often late",
        "Departures around {value} are usually on time",
    ),
    "minute_of_day": ("This time of day is usually busy", "This time of day is usually quiet"),
    "weekday": ("{value}s are busier", "{value}s are usually calmer"),
    "is_weekend": ("{value} traffic raises the risk", "{value} traffic lowers the risk"),
    "month": ("Delays are more common in {value}", "Delays are less common in {value}"),
    "is_public_holiday": (
        "{value} timetable raises the risk",
        "{value} timetable lowers the risk",
    ),
}


@dataclass(frozen=True)
class Factor:
    feature: str
    direction: Direction
    text: str


def _value_text(feature: str, value: object) -> str:  # noqa: PLR0911 - one case per feature
    if feature == "train_type":
        return "These" if str(value) in _UNKNOWN else str(value)
    if feature == "hour_local":
        return f"{int(str(value)):02d}:00"
    if feature == "weekday":
        return _WEEKDAYS[int(str(value))]
    if feature == "month":
        return _MONTHS[int(str(value)) - 1]
    if feature == "is_weekend":
        return "Weekend" if bool(value) else "Weekday"
    if feature == "is_public_holiday":
        return "Public holiday" if bool(value) else "Regular"
    return str(value)


def factor_text(feature: str, direction: Direction, value: object) -> str:
    """Reason text for one feature of one departure."""
    raises, lowers = FACTOR_TEXT[feature]
    template = raises if direction == "up" else lowers
    return template.format(value=_value_text(feature, value))


def top_factors(
    booster: lgb.Booster, features: pd.DataFrame, k: int = TOP_K
) -> list[tuple[Factor, ...]]:
    """Per row, the ``k`` features with the largest absolute contribution (zero ones skipped)."""
    if features.empty:
        return []
    contrib = np.asarray(booster.predict(features, pred_contrib=True), dtype=np.float64)[:, :-1]
    names = [str(name) for name in booster.feature_name()]
    result: list[tuple[Factor, ...]] = []
    for i, row in enumerate(contrib):
        order = np.argsort(-np.abs(row), kind="stable")[:k]
        factors: list[Factor] = []
        for j in order:
            if row[j] == 0.0:
                continue
            direction: Direction = "up" if row[j] > 0 else "down"
            value = features.iloc[i][names[j]]
            factors.append(Factor(names[j], direction, factor_text(names[j], direction, value)))
        result.append(tuple(factors))
    return result

"""Late-rate lookup baseline with back-off to coarser groups (architecture §5.2)."""

import json
from collections.abc import Sequence

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from dbdelay.errors import DataValidationError

BACKOFF_LEVELS: tuple[tuple[str, ...], ...] = (
    ("eva", "train_type", "hour_local", "weekday"),
    ("eva", "train_type", "hour_local"),
    ("eva", "train_type"),
    ("train_type",),
)


def _group_keys(features: pd.DataFrame, columns: Sequence[str]) -> pd.Series:
    keys = features[columns[0]].astype(str)
    for column in columns[1:]:
        keys = keys + "|" + features[column].astype(str)
    return keys


class BaselineModel:
    """Predicts the train late rate of the most specific group with ≥ ``min_count`` rows."""

    def __init__(
        self,
        *,
        min_count: int,
        global_rate: float,
        groups: dict[tuple[str, ...], dict[str, tuple[int, int]]],
    ) -> None:
        self.min_count = min_count
        self.global_rate = global_rate
        self.groups = groups

    @classmethod
    def fit(cls, features: pd.DataFrame, y: NDArray[np.bool_], min_count: int) -> "BaselineModel":
        """Count rows and late rows per group at every back-off level.

        Raises:
            DataValidationError: if there are no rows or ``y`` does not match the rows.
        """
        labels = np.asarray(y, dtype=bool)
        if len(features) == 0 or len(features) != len(labels):
            raise DataValidationError("baseline needs a non-empty train set with one label per row")
        groups: dict[tuple[str, ...], dict[str, tuple[int, int]]] = {}
        for level in BACKOFF_LEVELS:
            stats = (
                pd.DataFrame({"key": _group_keys(features, level).to_numpy(), "late": labels})
                .groupby("key")["late"]
                .agg(["size", "sum"])
            )
            kept = stats[stats["size"] >= min_count]
            groups[level] = {
                str(key): (int(n), int(late))
                for key, n, late in zip(kept.index, kept["size"], kept["sum"], strict=True)
            }
        return cls(min_count=min_count, global_rate=float(labels.mean()), groups=groups)

    def predict(self, features: pd.DataFrame) -> NDArray[np.float64]:
        """Late probability per row (first level with a qualifying group, else global)."""
        result = np.full(len(features), np.nan)
        for level in BACKOFF_LEVELS:
            missing = np.isnan(result)
            if not missing.any():
                break
            rates = {key: late / n for key, (n, late) in self.groups[level].items()}
            mapped = _group_keys(features, level).map(rates).to_numpy(dtype=float, na_value=np.nan)
            result = np.where(missing, mapped, result)
        return np.where(np.isnan(result), self.global_rate, result)

    def to_json(self) -> str:
        return json.dumps(
            {
                "min_count": self.min_count,
                "global_rate": self.global_rate,
                "levels": [
                    {
                        "columns": list(level),
                        "groups": {k: list(v) for k, v in sorted(self.groups[level].items())},
                    }
                    for level in BACKOFF_LEVELS
                ],
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> "BaselineModel":
        """Restore a saved baseline.

        Raises:
            DataValidationError: if the JSON is malformed or has other back-off levels.
        """
        try:
            raw = json.loads(text)
            levels = [tuple(level["columns"]) for level in raw["levels"]]
            if levels != list(BACKOFF_LEVELS):
                raise DataValidationError("baseline back-off levels do not match this code")
            groups = {
                tuple(level["columns"]): {
                    key: (int(value[0]), int(value[1])) for key, value in level["groups"].items()
                }
                for level in raw["levels"]
            }
            return cls(
                min_count=int(raw["min_count"]),
                global_rate=float(raw["global_rate"]),
                groups=groups,
            )
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise DataValidationError("invalid baseline JSON") from exc

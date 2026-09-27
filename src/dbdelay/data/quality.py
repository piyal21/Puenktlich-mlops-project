"""Row-level quarantine and the per-month quality report (rules.md §5.3)."""

import pandas as pd

# s@id: "<trip hash>-<YYMMDDHHmm>-<stop ≥ 1>"
_ID_PATTERN = r"-?[0-9]+-[0-9]{10}-[1-9][0-9]*"
QUARANTINE_REASONS: tuple[str, ...] = (
    "bad_id",
    "missing_train_type",
    "missing_cancel_flag",
    "missing_delay",
    "delay_mismatch",
)


def _as_bool(mask: "pd.Series[bool]") -> "pd.Series[bool]":
    return mask.fillna(False).astype(bool)


def split_quarantine(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split HF-shaped rows into (usable, quarantined + ``quarantine_reason``).

    Never modifies values: rows either pass as they are or are set aside with a reason.
    """
    ids = raw["id"].astype("string")
    train_type = raw["train_type"].astype("string").str.strip()
    cancelled = raw["departure_is_canceled"]
    not_cancelled = _as_bool(cancelled.eq(False))
    delay = raw["delay_in_min"]
    expected = raw["departure_planned_time"] + pd.to_timedelta(delay.astype("Float64"), unit="min")
    rules: dict[str, pd.Series] = {
        "bad_id": ~_as_bool(ids.str.fullmatch(_ID_PATTERN)),
        "missing_train_type": _as_bool(train_type.isna() | train_type.eq("")),
        "missing_cancel_flag": _as_bool(cancelled.isna()),
        "missing_delay": not_cancelled & _as_bool(delay.isna()),
        "delay_mismatch": not_cancelled & ~_as_bool(raw["departure_change_time"].eq(expected)),
    }
    reason = pd.Series(pd.NA, index=raw.index, dtype="object")
    for name in QUARANTINE_REASONS:
        reason = reason.mask(reason.isna() & rules[name], name)
    flagged = reason.notna()
    quarantined = raw[flagged].assign(quarantine_reason=reason[flagged].astype("object"))
    return raw[~flagged], quarantined

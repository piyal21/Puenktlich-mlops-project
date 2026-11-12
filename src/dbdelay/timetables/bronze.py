"""Bronze for live ingestion: one JSONL.gz object per run and kind, one line per API call."""

import gzip
import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Literal

from dbdelay.errors import DataValidationError

Kind = Literal["plan", "fchg"]
BRONZE_ROOT = "bronze/timetables/"


@dataclass(frozen=True)
class BronzeRecord:
    """One call: ``hour`` is the Berlin ``YYMMDDHH`` of a plan call; ``body`` the raw XML."""

    kind: Kind
    eva: str
    hour: str | None
    requested_at: datetime
    status: int | None
    body: str | None
    error: str | None

    def __post_init__(self) -> None:
        if self.requested_at.utcoffset() != timedelta(0):
            raise ValueError("requested_at must be UTC")


def bronze_prefix(kind: Kind, day: str) -> str:
    """Prefix of one kind's objects for one UTC run date (``YYYY-MM-DD``)."""
    return f"{BRONZE_ROOT}kind={kind}/date={day}/"


def bronze_key(kind: Kind, run_at: datetime) -> str:
    return f"{bronze_prefix(kind, f'{run_at:%Y-%m-%d}')}run={run_at:%Y-%m-%dT%H%M%SZ}.jsonl.gz"


def records_to_bytes(records: Iterable[BronzeRecord]) -> bytes:
    lines = []
    for record in records:
        row = asdict(record) | {"requested_at": record.requested_at.isoformat()}
        lines.append(json.dumps(row, ensure_ascii=False))
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"), mtime=0)


def iter_records(data: bytes) -> Iterator[BronzeRecord]:
    """Records of one bronze object, in call order.

    Raises:
        DataValidationError: not gzip, or a line that is not a bronze record.
    """
    try:
        text = gzip.decompress(data).decode("utf-8")
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise DataValidationError("bronze object is not gzip UTF-8 text") from exc
    for number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        try:
            row = json.loads(line)
            row["requested_at"] = datetime.fromisoformat(row["requested_at"])
            yield BronzeRecord(**row)
        except (ValueError, KeyError, TypeError) as exc:
            raise DataValidationError(f"bronze line {number} invalid") from exc

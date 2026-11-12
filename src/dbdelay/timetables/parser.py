"""Parse DB Timetables `plan` and `fchg` XML into departure events (architecture §8).

Only ``defusedxml`` touches the untrusted XML. DB times are naive Europe/Berlin ``YYMMDDHHmm``;
like the HF ETL, times that are ambiguous or do not exist on DST days are dropped and counted.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from xml.etree.ElementTree import Element, ParseError
from zoneinfo import ZoneInfo

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring
from pydantic import AwareDatetime, BaseModel, ConfigDict

from dbdelay.errors import DataValidationError

BERLIN = ZoneInfo("Europe/Berlin")
STOP_ID = re.compile(r"(-?[0-9]+-[0-9]{10})-([1-9][0-9]*)")
DROP_REASONS = ("no_departure", "bad_id", "dst_ambiguous_or_nonexistent")


class StopEvent(BaseModel):
    """One `<s>` with a departure, as one response reported it. Missing attributes are None."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stop_id: str
    ride_id: str
    stop_index: int
    eva: str
    train_type: str | None
    train_number: str | None
    line_number: str | None
    planned_utc: AwareDatetime | None
    changed_utc: AwareDatetime | None
    cancelled: bool
    planned_platform: str | None
    changed_platform: str | None
    planned_path: tuple[str, ...]
    changed_path: tuple[str, ...] | None  # None: no `cpth`; () : changed to empty (cancelled)
    distant_endpoint: str | None


@dataclass(frozen=True)
class ParseResult:
    events: tuple[StopEvent, ...]
    drops: dict[str, int] = field(default_factory=lambda: dict.fromkeys(DROP_REASONS, 0))


def parse_db_time(value: str) -> datetime | None:
    """``YYMMDDHHmm`` Europe/Berlin → aware UTC; ``None`` if ambiguous or nonexistent.

    Raises:
        ValueError: if ``value`` is not a valid ``YYMMDDHHmm`` timestamp.
    """
    naive = datetime.strptime(value, "%y%m%d%H%M")
    first = naive.replace(tzinfo=BERLIN, fold=0)
    second = naive.replace(tzinfo=BERLIN, fold=1)
    if first.utcoffset() != second.utcoffset():
        return None  # the hour repeats when clocks go back
    as_utc = first.astimezone(UTC)
    if as_utc.astimezone(BERLIN).replace(tzinfo=None) != naive:
        return None  # the hour is skipped when clocks go forward
    return as_utc


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _path(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split("|") if part.strip())


def _parse_root(xml: bytes) -> Element:
    try:
        root = fromstring(xml)
    except (ParseError, DefusedXmlException) as exc:
        raise DataValidationError(f"timetable XML rejected: {type(exc).__name__}") from None
    if root.tag != "timetable":
        raise DataValidationError(f"timetable XML has root <{root.tag}>")
    return root


def _time(value: str | None) -> tuple[datetime | None, bool]:
    """(UTC time or None, whether a present value had to be dropped)."""
    if not value:
        return None, False
    try:
        parsed = parse_db_time(value)
    except ValueError:
        return None, True
    return parsed, parsed is None


def parse_timetable(xml: bytes, eva: str) -> ParseResult:
    """Departure events of one `plan` or `fchg` response for station ``eva``.

    Raises:
        DataValidationError: malformed XML, entity tricks, or a root other than `<timetable>`.
    """
    root = _parse_root(xml)
    drops = dict.fromkeys(DROP_REASONS, 0)
    events: list[StopEvent] = []
    for stop in root.findall("s"):
        dp = stop.find("dp")
        if dp is None:
            drops["no_departure"] += 1
            continue
        match = STOP_ID.fullmatch(stop.get("id", ""))
        if match is None:
            drops["bad_id"] += 1
            continue
        planned, planned_bad = _time(dp.get("pt"))
        changed, changed_bad = _time(dp.get("ct"))
        if planned_bad:
            drops["dst_ambiguous_or_nonexistent"] += 1
            continue
        if changed_bad:
            drops["dst_ambiguous_or_nonexistent"] += 1  # keep the stop, without a changed time
        tl = stop.find("tl")
        category = None if tl is None else _text(tl.get("c"))
        cpth = dp.get("cpth")
        events.append(
            StopEvent(
                stop_id=match.group(0),
                ride_id=match.group(1),
                stop_index=int(match.group(2)),
                eva=eva,
                train_type=None if category is None else category.upper(),
                train_number=None if tl is None else _text(tl.get("n")),
                line_number=_text(dp.get("l")),
                planned_utc=planned,
                changed_utc=changed,
                cancelled=dp.get("cs") == "c",
                planned_platform=_text(dp.get("pp")),
                changed_platform=_text(dp.get("cp")),
                planned_path=_path(dp.get("ppth")),
                changed_path=None if cpth is None else _path(cpth),
                distant_endpoint=_text(dp.get("pde")),
            )
        )
    return ParseResult(events=tuple(events), drops=drops)

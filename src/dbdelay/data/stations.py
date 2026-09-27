"""Supported stations from ``configs/stations.yaml`` (Phase 1 decision, ADR 0001)."""

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from dbdelay.errors import ConfigError

_EVA = re.compile(r"[1-9][0-9]{6}")


def _check_eva(value: str) -> str:
    if not _EVA.fullmatch(value):
        raise ValueError("EVA must be 7 digits without a leading zero")
    return value


class Station(BaseModel):
    """One supported station; ``hf_aliases`` are retired EVAs whose history belongs to it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    eva: str
    name: str
    state: str
    hf_aliases: tuple[str, ...] = ()

    @field_validator("eva")
    @classmethod
    def _eva_form(cls, value: str) -> str:
        return _check_eva(value)

    @field_validator("hf_aliases")
    @classmethod
    def _alias_form(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_check_eva(alias) for alias in value)


class _StationsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    stations: list[Station]


def load_stations(path: Path) -> list[Station]:
    """Load and validate the station list.

    Raises:
        ConfigError: if the file is missing, malformed, or EVAs/aliases collide.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read station list {path.name}") from exc
    try:
        parsed = _StationsFile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid station list {path.name}: {exc.error_count()} errors") from None
    evas = [s.eva for s in parsed.stations] + [a for s in parsed.stations for a in s.hf_aliases]
    if len(set(evas)) != len(evas):
        raise ConfigError(f"duplicate EVA or alias in {path.name}")
    return parsed.stations


def alias_map(stations: Sequence[Station]) -> dict[str, str]:
    """Map every EVA and alias to the station's canonical EVA."""
    mapping = {s.eva: s.eva for s in stations}
    mapping.update({alias: s.eva for s in stations for alias in s.hf_aliases})
    return mapping

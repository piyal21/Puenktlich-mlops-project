"""Request and response models (mirrored in frontend/src/api/types.ts; keep in sync)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

EVA_PATTERN = r"^[1-9][0-9]{6}$"


class Problem(BaseModel):
    """RFC 9457 problem details (`application/problem+json`)."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    request_id: str


class HealthOut(BaseModel):
    status: Literal["ok"]
    model_version: str | None
    board_generated_at: datetime | None


class StationOut(BaseModel):
    eva: str
    name: str
    state: str

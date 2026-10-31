"""AWS Lambda entry point (`sample_board`): refresh the sample live board on a schedule.

Same image as the API; Terraform sets `image_config.command = ["seed_handler.handler"]`.
Errors propagate, so a failed run is a Lambda error (alarm) and the last board stays.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from dbdelay.config import get_settings
from dbdelay.logging import get_logger
from dbdelay.serving.seed import seed_board
from dbdelay.storage import ObjectStore, make_s3_client


def handler(event: Mapping[str, Any], context: object) -> dict[str, Any]:
    settings = get_settings()
    board = seed_board(
        ObjectStore(make_s3_client(settings), settings.data_bucket),
        now=datetime.now(UTC),
        board_key=settings.board_key,
        silver_root=settings.sample_silver_root,
    )
    replayed_from = board.replayed_from.isoformat() if board.replayed_from else None
    result = {"departures": len(board.departures), "replayed_from": replayed_from}
    get_logger("sample_board").info("sample board written", extra=result)
    return result

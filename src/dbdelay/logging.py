"""Structured JSON logging via AWS Lambda Powertools (docs/rules.md §6.4).

Never log secrets, API keys, full request headers, or raw XML at INFO.
"""

from aws_lambda_powertools import Logger

from dbdelay.config import get_settings


def get_logger(service: str) -> Logger:
    """Create a JSON logger for one service.

    Args:
        service: Component name, e.g. ``"api"``, ``"ingest"``, ``"training"``.

    Returns:
        A Powertools ``Logger`` at the configured log level.
    """
    return Logger(service=service, level=get_settings().log_level)

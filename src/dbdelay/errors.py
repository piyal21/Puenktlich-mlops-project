"""Exception hierarchy for the whole project (see docs/rules.md §6.2).

Catch these specific types at boundaries; never catch ``Exception`` silently.
"""


class DbDelayError(Exception):
    """Base class for all project errors."""


class ConfigError(DbDelayError):
    """Bad or missing configuration. Raised at startup."""


class ExternalServiceError(DbDelayError):
    """DB API, S3 or SSM failure (transient or not)."""


class RateLimitedError(ExternalServiceError):
    """HTTP 429 from the DB API."""


class DataValidationError(DbDelayError):
    """Pandera or contract validation failed."""


class ArtifactIntegrityError(DbDelayError):
    """Checksum mismatch or missing model artifact file."""


class ModelNotAvailableError(DbDelayError):
    """No champion model, or loading it failed."""


class NotFoundError(DbDelayError):
    """Unknown station, event or storage object."""

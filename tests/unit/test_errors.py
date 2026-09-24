import pytest

from dbdelay import errors


@pytest.mark.parametrize(
    "error_type",
    [
        errors.ConfigError,
        errors.ExternalServiceError,
        errors.RateLimitedError,
        errors.DataValidationError,
        errors.ArtifactIntegrityError,
        errors.ModelNotAvailableError,
        errors.NotFoundError,
    ],
)
def test_all_errors_share_the_project_base(error_type: type[Exception]) -> None:
    assert issubclass(error_type, errors.DbDelayError)


def test_rate_limit_is_an_external_service_error() -> None:
    # Retry logic catches ExternalServiceError; 429s must be included.
    assert issubclass(errors.RateLimitedError, errors.ExternalServiceError)

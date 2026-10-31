"""Laptop CLIs that write to AWS: recognise a missing or expired `aws login` session."""

from botocore.exceptions import (
    ClientError,
    NoCredentialsError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)

LOGIN_HINT = 'AWS login missing or expired: run "aws login --profile puenktlich"'

_EXPIRED_CODES = frozenset({"ExpiredToken", "ExpiredTokenException"})


def is_login_error(exc: BaseException) -> bool:
    """True if ``exc`` or anything in its cause chain is a credentials/session problem."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, NoCredentialsError | TokenRetrievalError | UnauthorizedSSOTokenError):
            return True
        if isinstance(seen, ClientError) and seen.response["Error"]["Code"] in _EXPIRED_CODES:
            return True
        seen = seen.__cause__ or seen.__context__
    return False

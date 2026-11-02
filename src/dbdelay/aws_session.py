"""Laptop CLIs that write to AWS: recognise a missing or expired `aws login` session."""

from botocore.exceptions import (
    ClientError,
    CredentialRetrievalError,
    NoCredentialsError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)

LOGIN_HINT = 'AWS login missing or expired: run "aws login --profile puenktlich"'

# "400": HEAD responses have no error body, so S3 reports an expired token as a bare 400.
_EXPIRED_CODES = frozenset({"ExpiredToken", "ExpiredTokenException", "400"})
_LOGIN_ERRORS = (
    NoCredentialsError,
    CredentialRetrievalError,  # the `puenktlich-py` credential_process failed
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)


def refusal_line(verb: str, exc: BaseException) -> str:
    """The one line a prod CLI prints when it stops."""
    return LOGIN_HINT if is_login_error(exc) else f"{verb} refused: {exc}"


def is_login_error(exc: BaseException) -> bool:
    """True if ``exc`` or anything in its cause chain is a credentials/session problem."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, _LOGIN_ERRORS):
            return True
        if isinstance(seen, ClientError) and seen.response["Error"]["Code"] in _EXPIRED_CODES:
            return True
        seen = seen.__cause__ or seen.__context__
    return False

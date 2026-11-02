from botocore.exceptions import ClientError, CredentialRetrievalError, NoCredentialsError

from dbdelay.aws_session import is_login_error
from dbdelay.errors import ExternalServiceError


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "GetObject")


def test_wrapped_no_credentials_is_login_error() -> None:
    try:
        raise ExternalServiceError("s3") from NoCredentialsError()
    except ExternalServiceError as exc:
        assert is_login_error(exc)


def test_expired_token_client_error_is_login_error() -> None:
    assert is_login_error(_client_error("ExpiredToken"))


def test_other_errors_are_not_login_errors() -> None:
    assert not is_login_error(_client_error("AccessDenied"))
    assert not is_login_error(ExternalServiceError("s3 down"))


def test_failed_credential_process_is_login_error() -> None:
    try:
        raise ExternalServiceError("s3") from CredentialRetrievalError(provider="p", error_msg="x")
    except ExternalServiceError as exc:
        assert is_login_error(exc)


def test_head_object_400_is_login_error() -> None:
    # HEAD has no error body: S3 reports an expired token as a bare "400".
    assert is_login_error(_client_error("400"))

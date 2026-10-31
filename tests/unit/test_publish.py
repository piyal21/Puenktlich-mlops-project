from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import boto3
import pytest
from botocore.exceptions import NoCredentialsError
from moto import mock_aws

from dbdelay.errors import ArtifactIntegrityError, ExternalServiceError, ModelNotAvailableError
from dbdelay.registry import publish
from dbdelay.registry.artifacts import load_bundle, models_prefix
from dbdelay.registry.pointer import NONE_VALUE, ObjectStorePointer, SsmPointer
from dbdelay.registry.publish import LOGIN_HINT, publish_champion, publish_main
from dbdelay.registry.release import release_version, rollback
from dbdelay.storage import ObjectStore
from tests.bundles import bundle_manifest
from tests.fakes import FakeTracker

CHAMP = "/puenktlich/prod/model/champion_version"
PREV = "/puenktlich/prod/model/previous_version"
REGION = "eu-north-1"


@dataclass
class Env:
    source: ObjectStore
    target: ObjectStore
    ssm: Any

    @property
    def target_pointer(self) -> SsmPointer:
        return SsmPointer(self.ssm, CHAMP)


@pytest.fixture
def env() -> Iterator[Env]:
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        for bucket in ("local", "prod-models"):
            s3.create_bucket(
                Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"}
            )
        ssm = boto3.client("ssm", region_name=REGION)
        for name in (CHAMP, PREV):
            ssm.put_parameter(Name=name, Value=NONE_VALUE, Type="String")
        yield Env(ObjectStore(s3, "local"), ObjectStore(s3, "prod-models"), ssm)


def _local_release(env: Env, files: dict[str, bytes], version: str) -> None:
    release_version(
        env.source,
        ObjectStorePointer(env.source),
        FakeTracker(),
        manifest=bundle_manifest(files, version),
        files=files,
    )


def _publish(env: Env) -> Any:
    return publish_champion(
        env.source, ObjectStorePointer(env.source), env.target, env.target_pointer
    )


def test_publish_copies_local_champion_and_moves_ssm(
    env: Env, bundle_files: dict[str, bytes]
) -> None:
    _local_release(env, bundle_files, "1")
    state = _publish(env)
    assert (state.champion_version, state.previous_version) == ("1", None)
    load_bundle(env.target, "1")  # verified copy in the prod bucket
    read = env.target_pointer.get()
    assert read is not None
    assert read.champion_version == "1"


def test_publish_same_version_again_is_noop(env: Env, bundle_files: dict[str, bytes]) -> None:
    _local_release(env, bundle_files, "1")
    _publish(env)
    again = _publish(env)
    assert (again.champion_version, again.previous_version) == ("1", None)


def test_publish_without_local_champion_raises(env: Env) -> None:
    with pytest.raises(ModelNotAvailableError):
        _publish(env)


def test_publish_refuses_target_with_different_files(
    env: Env, bundle_files: dict[str, bytes]
) -> None:
    _local_release(env, bundle_files, "1")
    tampered = dict(bundle_files)
    tampered["model_card.md"] = b"something else"
    env.target.put_bytes(
        models_prefix("1") + "manifest.json",
        bundle_manifest(tampered, "1").model_dump_json().encode(),
    )
    with pytest.raises(ArtifactIntegrityError):
        _publish(env)
    assert env.target_pointer.get() is None


def test_publish_new_version_sets_previous(env: Env, bundle_files: dict[str, bytes]) -> None:
    _local_release(env, bundle_files, "1")
    _publish(env)
    _local_release(env, bundle_files, "2")
    state = _publish(env)
    assert (state.champion_version, state.previous_version) == ("2", "1")


def test_rollback_prod_swaps_ssm(env: Env, bundle_files: dict[str, bytes]) -> None:
    for version in ("1", "2"):
        _local_release(env, bundle_files, version)
        _publish(env)
    state = rollback(env.target, env.target_pointer, publish.NoAlias(), model_name="m")
    assert (state.champion_version, state.previous_version) == ("1", "2")


def test_publish_main_expired_login_is_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def expired(*_: object, **__: object) -> None:
        # What ObjectStore raises when the `aws login` session is gone.
        raise ExternalServiceError("s3 unreachable") from NoCredentialsError()

    monkeypatch.setattr(publish, "publish_champion", expired)
    assert publish_main(["--models-bucket", "prod-models"]) == 1
    assert capsys.readouterr().err == LOGIN_HINT + "\n"


def test_publish_main_refusal_is_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_champion(*_: object, **__: object) -> None:
        raise ModelNotAvailableError("no local champion")

    monkeypatch.setattr(publish, "publish_champion", no_champion)
    assert publish_main(["--models-bucket", "prod-models"]) == 1
    assert capsys.readouterr().err == "publish refused: no local champion\n"


def test_rollback_prod_main_without_previous_is_refused(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(publish, "make_ssm_client", lambda _region: env.ssm)
    monkeypatch.setattr(publish, "load_training_config", lambda _path: _Cfg())
    assert publish.rollback_prod_main(["--models-bucket", "prod-models"]) == 1
    assert capsys.readouterr().err.startswith("rollback refused: no previous model version")


def test_rollback_prod_main_swaps(
    env: Env,
    bundle_files: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for version in ("1", "2"):
        _local_release(env, bundle_files, version)
        _publish(env)
    monkeypatch.setattr(publish, "make_ssm_client", lambda _region: env.ssm)
    monkeypatch.setattr(publish, "load_training_config", lambda _path: _Cfg())
    assert publish.rollback_prod_main(["--models-bucket", "prod-models"]) == 0
    assert capsys.readouterr().out == "prod champion 1 (previous 2)\n"


class _Cfg:
    class registry:
        model_name = "m"

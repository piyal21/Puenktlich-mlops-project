"""Copy the local champion to AWS and move the SSM pointer; roll prod back (Phase 6).

Only the version the gate released locally (the local pointer's champion) can be published,
so this is never a manual promotion (rules.md). The MLflow alias is local and already set.
"""

import argparse
import sys
from collections.abc import Callable, Sequence

from dbdelay.aws_session import LOGIN_HINT, is_login_error
from dbdelay.config import get_settings
from dbdelay.errors import DbDelayError, ModelNotAvailableError
from dbdelay.registry.artifacts import read_bundle_files
from dbdelay.registry.pointer import (
    ModelPointer,
    ObjectStorePointer,
    PointerState,
    SsmPointer,
    make_ssm_client,
)
from dbdelay.registry.release import release_version, rollback
from dbdelay.storage import ObjectStore, make_aws_s3_client, make_s3_client
from dbdelay.training.config import load_training_config

__all__ = ["LOGIN_HINT", "NoAlias", "publish_champion", "publish_main", "rollback_prod_main"]

DEFAULT_POINTER_PARAM = "/puenktlich/prod/model/champion_version"
DEFAULT_REGION = "eu-north-1"


class NoAlias:
    """Prod releases leave the (local) MLflow alias alone."""

    def set_alias(self, model_name: str, alias: str, version: str) -> None:
        return None


def publish_champion(
    source: ObjectStore,
    source_pointer: ModelPointer,
    target: ObjectStore,
    target_pointer: ModelPointer,
) -> PointerState:
    """Upload the local champion's verified bundle to ``target`` and point prod at it.

    Re-run safe: an identical bundle already in ``target`` is reused, and a version that is
    already the prod champion leaves the pointer unchanged.

    Raises:
        ModelNotAvailableError: if there is no local champion.
        ArtifactIntegrityError: if the local bundle does not verify, or ``target`` holds the
            same version with other files (prod pointer unchanged).
    """
    local = source_pointer.get()
    if local is None:
        raise ModelNotAvailableError("no local champion to publish (run `make train` first)")
    manifest, files = read_bundle_files(source, local.champion_version)
    return release_version(target, target_pointer, NoAlias(), manifest=manifest, files=files)


def _args(argv: Sequence[str] | None, description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--models-bucket", required=True, help="AWS models bucket (TF output)")
    parser.add_argument("--pointer-param", default=DEFAULT_POINTER_PARAM)
    parser.add_argument("--region", default=DEFAULT_REGION)
    return parser.parse_args(argv)


def _run(verb: str, action: Callable[[], PointerState]) -> int:
    """Operator-facing CLI wrapper: one line on stdout, or one line on stderr and exit 1."""
    try:
        state = action()
    except DbDelayError as exc:
        if is_login_error(exc):
            sys.stderr.write(LOGIN_HINT + "\n")
        else:
            sys.stderr.write(f"{verb} refused: {exc}\n")
        return 1
    sys.stdout.write(
        f"prod champion {state.champion_version} (previous {state.previous_version})\n"
    )
    return 0


def publish_main(argv: Sequence[str] | None = None) -> int:
    """CLI for `make publish-model`."""
    args = _args(argv, "Publish the local champion model to AWS (S3 + SSM pointer).")

    def action() -> PointerState:
        source = ObjectStore(make_s3_client(get_settings()), get_settings().models_bucket)
        target = ObjectStore(make_aws_s3_client(args.region), args.models_bucket)
        return publish_champion(
            source,
            ObjectStorePointer(source),
            target,
            SsmPointer(make_ssm_client(args.region), args.pointer_param),
        )

    return _run("publish", action)


def rollback_prod_main(argv: Sequence[str] | None = None) -> int:
    """CLI for `make rollback-prod`: prod champion ↔ previous (SSM only)."""
    args = _args(argv, "Point the prod champion back at the previous version.")

    def action() -> PointerState:
        target = ObjectStore(make_aws_s3_client(args.region), args.models_bucket)
        cfg = load_training_config(get_settings().training_config_file)
        return rollback(
            target,
            SsmPointer(make_ssm_client(args.region), args.pointer_param),
            NoAlias(),
            model_name=cfg.registry.model_name,
        )

    return _run("rollback", action)

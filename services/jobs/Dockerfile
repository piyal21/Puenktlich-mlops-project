# Jobs image (Phase 7): ingest + nightly ETL Lambdas (Phase 9 adds monitor). Build context: repo root.
# linux/amd64 like Lambda x86_64. Each Lambda picks its handler via image_config.command.
FROM public.ecr.aws/lambda/python:3.15@sha256:14e5711c512caa87a1cb28eba1c4381d964ed1faf7206f16ed05c7ef9692cac4

# OS security fixes newer than the pinned base image (CI fails on fixable HIGH/CRITICAL, Phase 8).
# AL2023 locks updates to the image's release unless --releasever=latest.
RUN dnf upgrade -y --releasever=latest && dnf clean all

# Hash-pinned, exported from uv.lock (`make jobs-requirements`).
COPY services/jobs/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY configs/stations.yaml ${LAMBDA_TASK_ROOT}/configs/stations.yaml
COPY src/dbdelay ${LAMBDA_TASK_ROOT}/dbdelay
COPY services/jobs/ingest_handler.py services/jobs/etl_handler.py ${LAMBDA_TASK_ROOT}/

CMD ["ingest_handler.handler"]

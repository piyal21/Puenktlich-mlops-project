# API image (Phase 5). Build context: repo root. linux/amd64 like Lambda x86_64.
#   lambda (last stage = default for a plain `docker build`): Mangum handler for AWS Lambda.
#   local:  the same code + uvicorn on :8000 (compose profile "app", `--target local`).
FROM public.ecr.aws/lambda/python:3.15@sha256:14e5711c512caa87a1cb28eba1c4381d964ed1faf7206f16ed05c7ef9692cac4 AS base

# OS security fixes newer than the pinned base image (CI fails on fixable HIGH/CRITICAL, Phase 8).
# AL2023 locks updates to the image's release unless --releasever=latest;
# LightGBM wheels need the OpenMP runtime, which the base image lacks.
RUN dnf upgrade -y --releasever=latest && dnf install -y libgomp && dnf clean all

# Hash-pinned, exported from uv.lock (`make api-requirements`).
COPY services/api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY configs/stations.yaml ${LAMBDA_TASK_ROOT}/configs/stations.yaml
COPY src/dbdelay ${LAMBDA_TASK_ROOT}/dbdelay
COPY services/api/app ${LAMBDA_TASK_ROOT}/app
COPY services/api/lambda_handler.py services/api/seed_handler.py ${LAMBDA_TASK_ROOT}/

FROM base AS local
COPY services/api/requirements-local.txt /tmp/requirements-local.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-local.txt --target "${LAMBDA_TASK_ROOT}"
WORKDIR ${LAMBDA_TASK_ROOT}
USER 1000:1000
EXPOSE 8000
ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
CMD []

FROM base AS lambda
CMD ["lambda_handler.handler"]

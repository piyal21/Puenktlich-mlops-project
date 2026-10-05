# API image (Phase 5). Build context: repo root. linux/amd64 like Lambda x86_64.
#   lambda (last stage = default for a plain `docker build`): Mangum handler for AWS Lambda.
#   local:  the same code + uvicorn on :8000 (compose profile "app", `--target local`).
FROM public.ecr.aws/lambda/python:3.12@sha256:517bcc7dc1a3ba62e324961c1563cc54a2bd6dc1060188f9d376211ecb7b2926 AS base

# LightGBM wheels need the OpenMP runtime, which the base image lacks.
RUN dnf install -y libgomp && dnf clean all

# Hash-pinned, exported from uv.lock (`make api-requirements`).
COPY services/api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY configs/stations.yaml ${LAMBDA_TASK_ROOT}/configs/stations.yaml
COPY src/dbdelay ${LAMBDA_TASK_ROOT}/dbdelay
COPY services/api/app ${LAMBDA_TASK_ROOT}/app
COPY services/api/lambda_handler.py ${LAMBDA_TASK_ROOT}/

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

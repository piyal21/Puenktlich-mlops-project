# Jobs image (Phase 7): ingest + nightly ETL Lambdas (Phase 9 adds monitor). Build context: repo root.
# linux/amd64 like Lambda x86_64. Each Lambda picks its handler via image_config.command.
FROM public.ecr.aws/lambda/python:3.12@sha256:517bcc7dc1a3ba62e324961c1563cc54a2bd6dc1060188f9d376211ecb7b2926

# Hash-pinned, exported from uv.lock (`make jobs-requirements`).
COPY services/jobs/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY configs/stations.yaml ${LAMBDA_TASK_ROOT}/configs/stations.yaml
COPY src/dbdelay ${LAMBDA_TASK_ROOT}/dbdelay
COPY services/jobs/ingest_handler.py services/jobs/etl_handler.py ${LAMBDA_TASK_ROOT}/

CMD ["ingest_handler.handler"]

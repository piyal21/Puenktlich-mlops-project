# MLflow tracking server + registry for the local training plane.
# Adds the Postgres driver (backend store) and boto3 (MinIO artifact store).
# Dependencies are fully locked with hashes in requirements.txt (regenerate: see its header).
FROM python:3.12.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /bin/uv
COPY requirements.txt /tmp/requirements.txt

RUN uv pip install --system --no-cache --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && useradd --create-home --uid 1000 mlflow

ENV PYTHONUNBUFFERED=1
USER mlflow
WORKDIR /home/mlflow
EXPOSE 5000

# Command comes from docker-compose.yml so connection settings stay in one place.
ENTRYPOINT ["/bin/sh", "-c"]

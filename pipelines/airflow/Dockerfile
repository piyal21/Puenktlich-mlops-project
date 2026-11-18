# Airflow 3.3.2 + dbdelay[pipelines,training] (Phase 2, 4). Build context: repo root.
FROM apache/airflow:3.4.0-python3.12@sha256:818f94affc5bd7ce0be0cc04f2ace09aa8382e65190a73a08881229ff32700cb

# LightGBM wheels need the OpenMP runtime.
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

USER airflow
COPY --chown=airflow:root pyproject.toml README.md /opt/dbdelay/
COPY --chown=airflow:root src /opt/dbdelay/src

# Airflow's official way to add packages: install under its tested constraints so the image
# stays consistent. Exception: pandas — the project is pinned to pandas<3 (stubs, Pandera),
# while the 3.3.2 constraints pin pandas 3.x, so that one line is dropped.
RUN curl -fsSL https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt \
      | grep -v '^pandas==' > /tmp/constraints.txt \
 && pip install --no-cache-dir -c /tmp/constraints.txt \
      "apache-airflow==3.3.2" "/opt/dbdelay[pipelines,training]"

COPY --chown=airflow:root pipelines/airflow/init /opt/airflow/init

# Last, so a new commit only rebuilds this layer. Logged as the `git_sha` MLflow run tag.
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

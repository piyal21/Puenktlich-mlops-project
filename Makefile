# Works with GNU make on Windows (cmd/PowerShell), Linux and macOS.
# Recipes only call uv/docker, so no shell-specific syntax.

.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help setup up down logs ps lint fmt typecheck test test-integration check airflow-env airflow-up airflow-down test-dags backfill baseline train rollback

help: ## Show available targets
	@uv run python -c "import re; [print(f'{m[0]:<18} {m[1]}') for m in re.findall(r'^([a-z-]+):.*?## (.*)$$', open('Makefile', encoding='utf-8').read(), re.M)]"

setup: ## Create .venv (Python 3.12), install deps, copy .env, install git hooks
	uv sync
	uv run python -c "import os, shutil; os.path.exists('.env') or shutil.copyfile('.env.example', '.env')"
	uv run pre-commit install

up: ## Start local stack (MinIO, Postgres, MLflow)
	$(COMPOSE) up -d --build --wait

down: ## Stop local stack (data volumes are kept)
	$(COMPOSE) down

logs: ## Follow logs of the local stack
	$(COMPOSE) logs -f --tail=100

ps: ## Show status of local services
	$(COMPOSE) ps

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-fix lint issues and format
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## mypy (strict on src/dbdelay)
	uv run mypy

test: ## Unit tests with coverage (no network, no Docker)
	uv run pytest tests/unit --cov --cov-report=term-missing

test-integration: ## Integration tests against the running local stack
	uv run pytest tests/integration -m integration

check: lint typecheck test ## Everything CI runs locally

airflow-env: ## Add missing Airflow secrets to .env (values never printed)
	uv run python scripts/ensure_airflow_env.py

airflow-up: ## Start Airflow 3 (api-server :8080, scheduler, dag-processor) + core stack
	$(COMPOSE) --profile airflow up -d --build --wait

airflow-down: ## Stop Airflow and the core stack (volumes kept)
	$(COMPOSE) --profile airflow down

test-dags: ## Parse the DAGs inside the Airflow image
	$(COMPOSE) --profile airflow run --rm --no-deps --entrypoint python airflow-scheduler /opt/airflow/tests/check_dags.py

backfill: ## Unpause and trigger backfill_history with its default 9 months
	$(COMPOSE) --profile airflow exec airflow-scheduler airflow dags unpause backfill_history
	$(COMPOSE) --profile airflow exec airflow-scheduler airflow dags trigger backfill_history

baseline: ## Build the gold training snapshot and evaluate the late-rate baseline (Phase 3)
	uv run python -m dbdelay.training.run_baseline

train: ## Train, evaluate, gate and (if it passes) release a model; same steps as the DAG (Phase 4)
	uv run python -m dbdelay.training.run_train

rollback: ## Point the champion back at the previous model version (pointer + MLflow alias)
	uv run python scripts/rollback.py

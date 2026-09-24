# Works with GNU make on Windows (cmd/PowerShell), Linux and macOS.
# Recipes only call uv/docker, so no shell-specific syntax.

.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help setup up down logs ps lint fmt typecheck test test-integration check

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

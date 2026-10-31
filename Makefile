# Works with GNU make on Windows (cmd/PowerShell), Linux and macOS.
# Recipes only call uv/docker, so no shell-specific syntax.

.DEFAULT_GOAL := help
COMPOSE := docker compose
# Baked into the Airflow image as GIT_SHA (MLflow run tag `git_sha`).
export GIT_SHA := $(shell git rev-parse --short HEAD)
# Owner decision 2026-10-05: MLflow usage telemetry off for every make recipe (train, rollback).
export MLFLOW_DISABLE_TELEMETRY := true

# AWS (Phase 6): laptop deploys use the `aws login` profile. Exported only for AWS targets,
# so unit tests never see a real profile. Terraform outputs are read lazily (=, not :=).
AWS_PROFILE ?= puenktlich
AWS_REGION ?= eu-north-1
TF_BOOT := infra/bootstrap
TF_PROD := infra/envs/prod
tf_out = $(shell terraform -chdir=$(TF_PROD) output -raw $(1))
# Pinned by digest (0.64.1); runs from Docker, nothing to install.
TRIVY_IMAGE := aquasec/trivy@sha256:a8ca29078522f30393bdb34225e4c0994d38f37083be81a42da3a2a7e1488e9e
IMAGE_TAG ?= $(shell git rev-parse --short=12 HEAD)
ECR_REPO = $(call tf_out,ecr_repository_url)
ECR_REGISTRY = $(firstword $(subst /, ,$(ECR_REPO)))
AWS_TARGETS := publish-model rollback-prod upload-sample-days tf-bootstrap-plan tf-bootstrap-apply tf-init tf-plan tf-plan-stage-a tf-apply image-push web-deploy smoke-prod
$(AWS_TARGETS) aws-check: export AWS_PROFILE := $(AWS_PROFILE)
$(AWS_TARGETS) aws-check: export AWS_REGION := $(AWS_REGION)
# Git Bash would rewrite "/*" and docker -v paths into Windows paths.
$(AWS_TARGETS) tf-scan: export MSYS_NO_PATHCONV := 1
# boto3 cannot read `aws login` sessions without botocore[crt]; the Python CLIs use a profile
# whose credential_process asks the AWS CLI for them instead (no extra dependency).
PY_AWS_TARGETS := publish-model rollback-prod upload-sample-days
AWS_PY_PROFILE := $(AWS_PROFILE)-py
$(PY_AWS_TARGETS): export AWS_PROFILE := $(AWS_PY_PROFILE)
$(PY_AWS_TARGETS): aws-py-profile
aws-py-profile: export AWS_PROFILE := $(AWS_PROFILE)
# Docker Desktop's Windows credential store rejects ECR tokens ("The stub received bad data"):
# keep the 12 h ECR login in its own Docker config folder, outside the repo.
image-push: export DOCKER_CONFIG := $(or $(HOME),$(USERPROFILE))/.docker-puenktlich-ecr
# Fail fast with one clear line when the 12 h `aws login` session is gone.
$(AWS_TARGETS): aws-check

.PHONY: help setup up down logs ps lint fmt typecheck test test-integration check airflow-env airflow-up airflow-down test-dags backfill baseline train rollback train-dag seed app-up app-down api-dev api-requirements web-check publish-model rollback-prod upload-sample-days tf-fmt tf-validate tf-scan tf-bootstrap-plan tf-bootstrap-apply tf-init tf-plan tf-plan-stage-a tf-apply aws-check aws-py-profile image-push web-deploy smoke-prod

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

publish-model: ## Copy the local champion to AWS and point SSM at it (AWS write: owner OK)
	uv run python scripts/publish_model.py --models-bucket $(call tf_out,models_bucket) --region $(AWS_REGION)

rollback-prod: ## Swap the prod (SSM) champion back to the previous version (AWS write: owner OK)
	uv run python scripts/rollback_prod.py --models-bucket $(call tf_out,models_bucket) --region $(AWS_REGION)

upload-sample-days: ## Copy 9 real silver days to the AWS data bucket for the sample board (AWS write: owner OK)
	uv run python scripts/upload_sample_days.py --data-bucket $(call tf_out,data_bucket) --region $(AWS_REGION)

train-dag: ## Unpause and trigger training_pipeline in Airflow (Phase 4)
	$(COMPOSE) --profile airflow exec airflow-scheduler airflow dags unpause training_pipeline
	$(COMPOSE) --profile airflow exec airflow-scheduler airflow dags trigger training_pipeline

seed: ## Write a sample live board into MinIO (real silver day replayed onto today; Phase 5)
	uv run python scripts/seed_sample_data.py

app-up: ## Start the API (:8000) and web app (:5173) with the core stack (profile "app")
	$(COMPOSE) --profile app up -d --build --wait

app-down: ## Stop the app and the core stack (volumes kept)
	$(COMPOSE) --profile app down

api-dev: ## Run the API on the host with auto-reload (needs `make up`)
	uv run uvicorn app.main:app --app-dir services/api --reload --port 8000

api-requirements: ## Regenerate the API image's hash-pinned requirements from uv.lock
	uv export --frozen --no-dev --extra api --no-emit-project --format requirements-txt -o services/api/requirements.txt
	uv export --frozen --only-group api-local --no-emit-project --format requirements-txt -o services/api/requirements-local.txt

web-check: ## Frontend lint, typecheck and component tests (needs `npm --prefix frontend ci`)
	npm --prefix frontend run lint
	npm --prefix frontend run typecheck
	npm --prefix frontend test

# --- Infrastructure (Phase 6). Every apply is an AWS write: owner OK first. -------------------

tf-fmt: ## Check Terraform formatting (all of infra/)
	terraform fmt -check -recursive infra

tf-validate: ## Validate every Terraform root without touching state or AWS
	terraform -chdir=$(TF_BOOT) init -backend=false -input=false -no-color
	terraform -chdir=$(TF_BOOT) validate -no-color
	terraform -chdir=$(TF_PROD) init -backend=false -input=false -no-color
	terraform -chdir=$(TF_PROD) validate -no-color

tf-scan: ## Trivy misconfiguration scan of infra/ (Docker image, pinned)
	docker run --rm -v "$(CURDIR)/infra:/infra" $(TRIVY_IMAGE) config --exit-code 1 --ignorefile /infra/.trivyignore /infra

tf-bootstrap-plan: ## Plan the Terraform state bucket (read-only); saves bootstrap.tfplan
	terraform -chdir=$(TF_BOOT) init -input=false
	terraform -chdir=$(TF_BOOT) plan -input=false -out=bootstrap.tfplan

tf-bootstrap-apply: ## Apply the saved bootstrap plan and write envs/prod/backend.hcl (AWS write: owner OK)
	terraform -chdir=$(TF_BOOT) apply -input=false bootstrap.tfplan
	terraform -chdir=$(TF_BOOT) output -raw backend_hcl > $(TF_PROD)/backend.hcl

# envs/prod. Needs backend.hcl (from tf-bootstrap-apply) and TF_VAR_alert_email or a
# git-ignored terraform.auto.tfvars. Plans are saved and applied exactly as reviewed.
STAGE_A := -target=module.data_lake -target=module.ecr -target=module.ssm -target=module.budget -target=module.observability.aws_sns_topic.alerts -target=module.observability.aws_sns_topic_subscription.email

tf-init: ## Initialise envs/prod against the remote state bucket
	terraform -chdir=$(TF_PROD) init -input=false -reconfigure -backend-config=backend.hcl

tf-plan-stage-a: ## Plan stage A only (buckets, ECR, SSM, budget import, alert topic); saves prod.tfplan
	terraform -chdir=$(TF_PROD) plan -input=false $(STAGE_A) -out=prod.tfplan

tf-plan: ## Plan the whole prod stack (read-only); saves prod.tfplan
	terraform -chdir=$(TF_PROD) plan -input=false -out=prod.tfplan

tf-apply: ## Apply the saved prod.tfplan (AWS write: owner OK)
	terraform -chdir=$(TF_PROD) apply -input=false prod.tfplan

# --- Deploy from the laptop (no CI deploys on this account, ADR 0002) ----------------------

aws-check: ## Check the `aws login` session (12 h) before any AWS command
	@aws sts get-caller-identity --query "'aws login ok'" --output text || (echo AWS login missing or expired: run "aws login --profile puenktlich" && exit 1)

image-push: ## Build the API image (linux/amd64) and push it to ECR, tag = git SHA (AWS write: owner OK)
	git diff --quiet HEAD
	aws ecr get-login-password --region $(AWS_REGION) | docker login --username AWS --password-stdin $(ECR_REGISTRY)
	docker buildx build --platform linux/amd64 --target lambda --provenance=false -f services/api/Dockerfile -t $(ECR_REPO):$(IMAGE_TAG) --push .
	@echo pushed $(ECR_REPO):$(IMAGE_TAG) - next: TF_VAR_api_image_tag=$(IMAGE_TAG) make tf-plan

web-deploy: ## Build the frontend, sync it to the web bucket, invalidate CloudFront (AWS write: owner OK)
	npm --prefix frontend ci
	npm --prefix frontend run build
	aws s3 sync frontend/dist s3://$(call tf_out,web_bucket) --delete
	aws cloudfront create-invalidation --distribution-id $(call tf_out,cloudfront_distribution_id) --paths "/*"

smoke-prod: ## Check the public site end to end through CloudFront (read-only)
	uv run python scripts/smoke_prod.py --base https://$(call tf_out,cloudfront_domain)

aws-py-profile: ## Local AWS profile `<profile>-py` that hands the `aws login` session to boto3
	aws configure set credential_process "aws configure export-credentials --profile $(AWS_PROFILE) --format process" --profile $(AWS_PY_PROFILE)
	aws configure set region $(AWS_REGION) --profile $(AWS_PY_PROFILE)

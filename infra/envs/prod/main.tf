data "aws_caller_identity" "current" {}

locals {
  image_uri      = "${module.ecr.repository_url}:${var.api_image_tag}"
  jobs_image_uri = "${module.ecr_jobs.repository_url}:${var.jobs_image_tag}"
  board_key      = "live/boards/latest.json.gz"
  # Until the owner switches board_mode to "live", ingest writes a candidate board nobody serves.
  ingest_board_key = var.board_mode == "live" ? local.board_key : "live/boards/candidate.json.gz"
  data_arn         = module.data_lake.data_bucket_arn
}

# --- Stage A: storage, registry, pointer, budget, alert topic -----------------------------

module "data_lake" {
  source      = "../../modules/data_lake"
  name_suffix = data.aws_caller_identity.current.account_id
}

module "ecr" {
  source = "../../modules/ecr"
}

module "ecr_jobs" {
  source = "../../modules/ecr"
  name   = "puenktlich/jobs"
}

module "ssm" {
  source = "../../modules/ssm"
}

module "budget" {
  source      = "../../modules/budget"
  alert_email = var.alert_email
}

# The budget was created by hand before Terraform existed (2026-10-31); adopt it.
import {
  to = module.budget.aws_budgets_budget.monthly
  id = "${data.aws_caller_identity.current.account_id}:puenktlich-monthly"
}

module "observability" {
  source                     = "../../modules/observability"
  alert_email                = var.alert_email
  enable_alarms              = true
  api_id                     = module.http_api.api_id
  api_function_name          = module.api.function_name
  sample_board_function_name = module.sample_board.function_name
  ingest_function_name       = module.ingest.function_name
  etl_function_name          = module.etl_daily.function_name
  board_mode                 = var.board_mode
}

# --- Compute and edge ----------------------------------------------------------------------

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ReadBoard"
    actions   = ["s3:GetObject"]
    resources = ["${local.data_arn}/live/boards/*"]
  }
  statement {
    sid       = "ReadModels"
    actions   = ["s3:GetObject"]
    resources = ["${module.data_lake.models_bucket_arn}/models/*"]
  }
  statement {
    sid       = "ListModelBundles"
    actions   = ["s3:ListBucket"]
    resources = [module.data_lake.models_bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["models/*"]
    }
  }
  statement {
    sid       = "ReadModelPointer"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = module.ssm.param_arns
  }
}

module "api" {
  source      = "../../modules/lambda_image"
  name        = "api"
  image_uri   = local.image_uri
  memory_mb   = var.api_memory_mb
  timeout_s   = var.api_timeout_s
  policy_json = data.aws_iam_policy_document.api.json
  environment = {
    APP_ENV             = "prod"
    LOG_LEVEL           = "INFO"
    DATA_BUCKET         = module.data_lake.data_bucket
    MODELS_BUCKET       = module.data_lake.models_bucket
    MODEL_POINTER_PARAM = module.ssm.champion_param
    BOARD_KEY           = local.board_key
  }
}

data "aws_iam_policy_document" "sample_board" {
  statement {
    sid       = "ReadSampleDays"
    actions   = ["s3:GetObject"]
    resources = ["${local.data_arn}/samples/*"]
  }
  statement {
    sid       = "ListSampleDays"
    actions   = ["s3:ListBucket"]
    resources = [local.data_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["samples/*"]
    }
  }
  statement {
    sid       = "WriteBoard"
    actions   = ["s3:PutObject"]
    resources = ["${local.data_arn}/${local.board_key}"]
  }
}

module "sample_board" {
  source      = "../../modules/lambda_image"
  name        = "sample-board"
  image_uri   = local.image_uri
  command     = ["seed_handler.handler"]
  memory_mb   = 512 # account quota: max 512 MB
  timeout_s   = 60
  policy_json = data.aws_iam_policy_document.sample_board.json
  environment = {
    APP_ENV            = "prod"
    LOG_LEVEL          = "INFO"
    DATA_BUCKET        = module.data_lake.data_bucket
    BOARD_KEY          = local.board_key
    SAMPLE_SILVER_ROOT = "samples/"
  }
}

module "http_api" {
  source               = "../../modules/http_api"
  lambda_invoke_arn    = module.api.invoke_arn
  lambda_function_name = module.api.function_name
}

module "schedules" {
  source                    = "../../modules/schedules"
  board_mode                = var.board_mode
  sample_board_function_arn = module.sample_board.function_arn
  ingest_function_arn       = module.ingest.function_arn
  etl_function_arn          = module.etl_daily.function_arn
}

module "web_cdn" {
  source       = "../../modules/web_cdn"
  name_suffix  = data.aws_caller_identity.current.account_id
  api_endpoint = module.http_api.api_endpoint
}

# --- Phase 7: live pipeline (jobs image) -----------------------------------------------------

data "aws_iam_policy_document" "ingest" {
  statement {
    sid     = "BronzePlansBoard"
    actions = ["s3:GetObject", "s3:PutObject"]
    resources = [
      "${local.data_arn}/bronze/timetables/*",
      "${local.data_arn}/live/plans/*",
      "${local.data_arn}/live/boards/*",
    ]
  }
  statement {
    sid       = "ReadDbApiKeys"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = module.ssm.db_api_param_arns
  }
}

module "ingest" {
  source    = "../../modules/lambda_image"
  name      = "ingest"
  image_uri = local.jobs_image_uri
  command   = ["ingest_handler.handler"]
  memory_mb = 512 # account quota: max 512 MB
  timeout_s = 300
  # A retry could overlap the next 15-min run and break the 60 calls/min DB API limit.
  async_retries = 0
  policy_json   = data.aws_iam_policy_document.ingest.json
  environment = {
    APP_ENV                = "prod"
    LOG_LEVEL              = "INFO"
    DATA_BUCKET            = module.data_lake.data_bucket
    BOARD_KEY              = local.ingest_board_key
    DB_API_CLIENT_ID_PARAM = module.ssm.db_api_client_id_param
    DB_API_KEY_PARAM       = module.ssm.db_api_key_param
  }
}

data "aws_iam_policy_document" "etl_daily" {
  statement {
    sid       = "ListBronze"
    actions   = ["s3:ListBucket"]
    resources = [local.data_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["bronze/timetables/*"]
    }
  }
  statement {
    sid       = "ReadBronze"
    actions   = ["s3:GetObject"]
    resources = ["${local.data_arn}/bronze/timetables/*"]
  }
  statement {
    sid     = "WriteLiveSilver"
    actions = ["s3:PutObject"]
    resources = [
      "${local.data_arn}/silver/departures/source=live/*",
      "${local.data_arn}/silver/_quality/source=live/*",
    ]
  }
}

module "etl_daily" {
  source    = "../../modules/lambda_image"
  name      = "etl-daily"
  image_uri = local.jobs_image_uri
  command   = ["etl_handler.handler"]
  memory_mb = 512 # account quota: max 512 MB
  timeout_s = 600
  # Idempotent (re-running a day overwrites it): one retry is enough.
  async_retries = 1
  policy_json   = data.aws_iam_policy_document.etl_daily.json
  environment = {
    APP_ENV     = "prod"
    LOG_LEVEL   = "INFO"
    DATA_BUCKET = module.data_lake.data_bucket
  }
}

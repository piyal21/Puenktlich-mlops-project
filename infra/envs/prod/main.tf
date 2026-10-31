data "aws_caller_identity" "current" {}

locals {
  image_uri = "${module.ecr.repository_url}:${var.api_image_tag}"
  board_key = "live/boards/latest.json.gz"
  data_arn  = module.data_lake.data_bucket_arn
}

# --- Stage A: storage, registry, pointer, budget, alert topic -----------------------------

module "data_lake" {
  source      = "../../modules/data_lake"
  name_suffix = data.aws_caller_identity.current.account_id
}

module "ecr" {
  source = "../../modules/ecr"
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
  sample_board_function_arn = module.sample_board.function_arn
}

module "web_cdn" {
  source       = "../../modules/web_cdn"
  name_suffix  = data.aws_caller_identity.current.account_id
  api_endpoint = module.http_api.api_endpoint
}

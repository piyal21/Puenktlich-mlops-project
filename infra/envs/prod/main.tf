data "aws_caller_identity" "current" {}

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

module "observability" {
  source      = "../../modules/observability"
  alert_email = var.alert_email
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

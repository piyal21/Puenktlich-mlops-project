# Model pointer (architecture §6). Terraform owns the parameters, model releases own the
# values (`make publish-model` / `make rollback-prod`), so value changes are not drift.
# "none" = no version yet (SSM cannot store an empty string).

resource "aws_ssm_parameter" "pointer" {
  for_each = toset(["champion_version", "previous_version"])

  name  = "${var.prefix}/model/${each.key}"
  type  = "String"
  value = "none"

  lifecycle {
    ignore_changes = [value]
  }
}

# DB Timetables API keys for the ingest Lambda (Phase 7) are NOT Terraform resources: the AWS
# provider reads SecureString values back (decrypted) on every refresh and stores them in state,
# even with ignore_changes. `make put-db-api-secrets` creates/overwrites them from .env; Terraform
# only knows their names (for the Lambda environment) and ARNs (for its IAM policy).
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  db_api_names = { for k in ["client_id", "api_key"] : k => "${var.prefix}/db_api/${k}" }
}

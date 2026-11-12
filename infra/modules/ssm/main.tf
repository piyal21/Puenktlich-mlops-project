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

# DB Timetables API keys for the ingest Lambda (Phase 7). Terraform creates the parameters with a
# placeholder; `make put-db-api-secrets` copies the real values from .env, so they never reach
# Terraform state or the repo. Encrypted with the AWS-managed aws/ssm key (a customer-managed key
# costs ~$1/month, ADR 0002).
#trivy:ignore:AVD-AWS-0098 -- aws/ssm AWS-managed key; a CMK costs ~$1/month (ADR 0002)
resource "aws_ssm_parameter" "db_api" {
  for_each = toset(["client_id", "api_key"])

  name  = "${var.prefix}/db_api/${each.key}"
  type  = "SecureString"
  value = "set-by-make"

  lifecycle {
    ignore_changes = [value]
  }
}

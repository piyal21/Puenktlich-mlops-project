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

output "state_bucket" {
  description = "Remote state bucket for infra/envs/prod."
  value       = aws_s3_bucket.state.bucket
}

# `make tf-bootstrap-apply` writes this to infra/envs/prod/backend.hcl (git-ignored),
# so the bucket name (it contains the account id) never lands in the repo.
output "backend_hcl" {
  description = "Partial backend configuration for infra/envs/prod."
  value       = <<-EOT
    bucket       = "${aws_s3_bucket.state.bucket}"
    key          = "prod/terraform.tfstate"
    region       = "${var.region}"
    use_lockfile = true
    encrypt      = true
  EOT
}

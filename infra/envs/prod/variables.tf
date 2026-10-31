variable "region" {
  description = "AWS region (fixed by the account type, ADR 0002)."
  type        = string
  default     = "eu-north-1"
}

variable "alert_email" {
  description = "Budget and alarm recipient. Set TF_VAR_alert_email at run time; never committed."
  type        = string
  sensitive   = true
}

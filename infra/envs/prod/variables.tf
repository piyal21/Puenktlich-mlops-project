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

variable "api_image_tag" {
  description = "Immutable image tag (git SHA) pushed by `make image-push`. TF_VAR_api_image_tag."
  type        = string
  default     = null
}

variable "api_memory_mb" {
  description = "API Lambda memory; raise if the measured cold start is too slow."
  type        = number
  default     = 1024
}

variable "api_timeout_s" {
  description = "API Lambda timeout (API Gateway gives up after 29 s)."
  type        = number
  default     = 15

  validation {
    condition     = var.api_timeout_s <= 29
    error_message = "API Gateway HTTP APIs time out after 29 s."
  }
}

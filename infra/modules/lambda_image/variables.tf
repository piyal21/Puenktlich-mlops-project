variable "name" {
  description = "Short name; resources are called puenktlich-<name>."
  type        = string
}

variable "image_uri" {
  description = "ECR image URI including the immutable git-SHA tag."
  type        = string
}

variable "command" {
  description = "Handler override (image_config.command); null = the image's CMD."
  type        = list(string)
  default     = null
}

variable "memory_mb" {
  type = number
}

variable "timeout_s" {
  type = number
}

variable "environment" {
  description = "Environment variables (no secrets)."
  type        = map(string)
}

variable "policy_json" {
  description = "IAM policy for the function's data access (exact ARNs, no *)."
  type        = string
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "lambda_invoke_arn" {
  type = string
}

variable "lambda_function_name" {
  type = string
}

# Security review S1 (owner 2026-11-26): 2 req/s sustained bounds abuse while no hard spend cap
# exists on this account. This is ONE bucket for the whole site, not per visitor: burst 20 lets a
# page load (stations, model, board) plus a debounced search through without 429s.
variable "throttle_rate" {
  description = "Steady requests per second (architecture §12)."
  type        = number
  default     = 2
}

variable "throttle_burst" {
  type    = number
  default = 20
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "lambda_invoke_arn" {
  type = string
}

variable "lambda_function_name" {
  type = string
}

# Security review S1 (owner 2026-11-26): 2 req/s, burst 5 — far above demo traffic (one board
# load = one request), and it bounds abuse while no hard spend cap exists on this account.
variable "throttle_rate" {
  description = "Steady requests per second (architecture §12)."
  type        = number
  default     = 2
}

variable "throttle_burst" {
  type    = number
  default = 5
}

variable "log_retention_days" {
  type    = number
  default = 14
}

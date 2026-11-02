variable "lambda_invoke_arn" {
  type = string
}

variable "lambda_function_name" {
  type = string
}

variable "throttle_rate" {
  description = "Steady requests per second (architecture §12)."
  type        = number
  default     = 10
}

variable "throttle_burst" {
  type    = number
  default = 20
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "name" {
  type    = string
  default = "puenktlich-monthly"
}

variable "limit_usd" {
  description = "Monthly limit; alerts at 100 % actual and 500 % forecasted."
  type        = string
  default     = "1.0"
}

variable "alert_email" {
  description = "Budget alert recipient (given at apply time, never committed)."
  type        = string
  sensitive   = true
}

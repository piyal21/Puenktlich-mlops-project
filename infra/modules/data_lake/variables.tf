variable "name_suffix" {
  description = "Makes bucket names globally unique (the account id, read at apply time)."
  type        = string
}

variable "bronze_expiry_days" {
  description = "Days before raw live data (bronze/) expires (Phase 7 writes it)."
  type        = number
  default     = 30
}

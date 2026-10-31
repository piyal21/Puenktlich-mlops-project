variable "alert_email" {
  description = "Alarm recipient (given at apply time, never committed)."
  type        = string
  sensitive   = true
}

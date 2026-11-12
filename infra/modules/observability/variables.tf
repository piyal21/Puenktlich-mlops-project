variable "alert_email" {
  description = "Alarm recipient (given at apply time, never committed)."
  type        = string
  sensitive   = true
}

variable "api_id" {
  description = "HTTP API id to watch for 5xx (null = no alarm yet)."
  type        = string
  default     = null
}

variable "api_function_name" {
  type    = string
  default = null
}

variable "sample_board_function_name" {
  type    = string
  default = null
}

variable "enable_alarms" {
  description = "Create the alarms (needs the three ids/names above)."
  type        = bool
  default     = false
}

variable "ingest_function_name" {
  type    = string
  default = null
}

variable "etl_function_name" {
  type    = string
  default = null
}

variable "board_mode" {
  description = "Sample-board alarm only while the sample board is scheduled."
  type        = string
  default     = "sample"
}

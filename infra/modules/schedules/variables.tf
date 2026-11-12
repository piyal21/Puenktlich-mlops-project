variable "board_mode" {
  description = "Which writer owns the public board: \"live\" (ingest) or \"sample\" (Phase 6 replay)."
  type        = string
  default     = "sample"

  validation {
    condition     = contains(["live", "sample"], var.board_mode)
    error_message = "board_mode must be \"live\" or \"sample\"."
  }
}

variable "sample_board_function_arn" {
  type = string
}

variable "ingest_function_arn" {
  type = string
}

variable "etl_function_arn" {
  type = string
}

variable "board_rate" {
  description = "Board is marked stale after 20 min, so refresh more often than that."
  type        = string
  default     = "rate(15 minutes)"
}

variable "etl_cron" {
  description = "Nightly ETL for the previous UTC day (Europe/Berlin time; after late trains)."
  type        = string
  default     = "cron(30 3 * * ? *)"
}

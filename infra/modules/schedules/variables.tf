variable "sample_board_function_arn" {
  type = string
}

variable "sample_board_rate" {
  description = "Board is marked stale after 20 min, so refresh more often than that."
  type        = string
  default     = "rate(15 minutes)"
}

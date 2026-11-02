variable "name" {
  description = "Repository name."
  type        = string
  default     = "puenktlich/api"
}

variable "keep_images" {
  description = "Images kept by the lifecycle policy (owner OK 2026-10-31: 3, for cost)."
  type        = number
  default     = 3
}

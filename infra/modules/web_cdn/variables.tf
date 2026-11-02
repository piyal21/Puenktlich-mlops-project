variable "name_suffix" {
  description = "Makes the web bucket name globally unique (the account id)."
  type        = string
}

variable "api_endpoint" {
  description = "HTTP API endpoint, https://<id>.execute-api.<region>.amazonaws.com"
  type        = string
}

output "api_id" {
  value = aws_apigatewayv2_api.this.id
}

output "api_endpoint" {
  description = "https://<id>.execute-api.<region>.amazonaws.com"
  value       = aws_apigatewayv2_api.this.api_endpoint
}

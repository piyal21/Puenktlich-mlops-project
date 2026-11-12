output "champion_param" {
  value = aws_ssm_parameter.pointer["champion_version"].name
}

output "previous_param" {
  value = aws_ssm_parameter.pointer["previous_version"].name
}

output "param_arns" {
  value = [for p in aws_ssm_parameter.pointer : p.arn]
}

output "db_api_client_id_param" {
  value = aws_ssm_parameter.db_api["client_id"].name
}

output "db_api_key_param" {
  value = aws_ssm_parameter.db_api["api_key"].name
}

output "db_api_param_arns" {
  value = [for p in aws_ssm_parameter.db_api : p.arn]
}

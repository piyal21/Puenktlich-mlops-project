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
  value = local.db_api_names["client_id"]
}

output "db_api_key_param" {
  value = local.db_api_names["api_key"]
}

output "db_api_param_arns" {
  value = [
    for name in values(local.db_api_names) :
    "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${name}"
  ]
}

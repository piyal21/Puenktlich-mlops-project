output "champion_param" {
  value = aws_ssm_parameter.pointer["champion_version"].name
}

output "previous_param" {
  value = aws_ssm_parameter.pointer["previous_version"].name
}

output "param_arns" {
  value = [for p in aws_ssm_parameter.pointer : p.arn]
}

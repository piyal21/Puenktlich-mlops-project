output "data_bucket" {
  value = module.data_lake.data_bucket
}

output "models_bucket" {
  value = module.data_lake.models_bucket
}

output "ecr_repository_url" {
  value = module.ecr.repository_url
}

output "pointer_param_champion" {
  value = module.ssm.champion_param
}

output "pointer_param_previous" {
  value = module.ssm.previous_param
}

output "alerts_topic_arn" {
  value = module.observability.topic_arn
}

output "web_bucket" {
  value = module.web_cdn.web_bucket
}

output "cloudfront_domain" {
  value = module.web_cdn.domain_name
}

output "cloudfront_distribution_id" {
  value = module.web_cdn.distribution_id
}

output "api_endpoint" {
  value = module.http_api.api_endpoint
}

output "api_function_name" {
  value = module.api.function_name
}

output "sample_board_function_name" {
  value = module.sample_board.function_name
}

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

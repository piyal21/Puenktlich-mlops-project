output "data_bucket" {
  value = aws_s3_bucket.this["data"].bucket
}

output "data_bucket_arn" {
  value = aws_s3_bucket.this["data"].arn
}

output "models_bucket" {
  value = aws_s3_bucket.this["models"].bucket
}

output "models_bucket_arn" {
  value = aws_s3_bucket.this["models"].arn
}

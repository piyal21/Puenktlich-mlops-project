output "domain_name" {
  value = aws_cloudfront_distribution.this.domain_name
}

output "distribution_id" {
  value = aws_cloudfront_distribution.this.id
}

output "web_bucket" {
  value = aws_s3_bucket.web.bucket
}

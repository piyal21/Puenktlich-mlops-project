# One container-image Lambda with its own role (least privilege) and log group.

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "puenktlich-${var.name}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

#trivy:ignore:AVD-AWS-0017 -- log group uses the default CloudWatch encryption; a KMS key costs ~$1/month (ADR 0002)
resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/puenktlich-${var.name}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "logs" {
  source_policy_documents = [var.policy_json]

  statement {
    sid       = "OwnLogGroup"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.this.arn}:*"]
  }
}

resource "aws_iam_role_policy" "this" {
  name   = "puenktlich-${var.name}"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.logs.json
}

#trivy:ignore:AVD-AWS-0066 -- X-Ray tracing needs xray:PutTraceSegments on "*", which rules.md forbids; logs carry request ids
resource "aws_lambda_function" "this" {
  function_name = "puenktlich-${var.name}"
  role          = aws_iam_role.this.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  architectures = ["x86_64"]
  memory_size   = var.memory_mb
  timeout       = var.timeout_s

  dynamic "image_config" {
    for_each = var.command == null ? [] : [var.command]
    content {
      command = image_config.value
    }
  }

  environment {
    variables = var.environment
  }

  logging_config {
    log_format = "Text" # Powertools already writes JSON lines
    log_group  = aws_cloudwatch_log_group.this.name
  }

  depends_on = [aws_iam_role_policy.this]
}

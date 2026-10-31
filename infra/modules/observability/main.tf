# Alert topic (CloudWatch alarms → SNS → email). The owner confirms the subscription once.

# CloudWatch alarms cannot publish to a topic encrypted with the AWS-managed aws/sns key, and
# a customer-managed key costs ~$1/month; the topic only carries alarm names (ADR 0002).
#trivy:ignore:AVD-AWS-0095
resource "aws_sns_topic" "alerts" {
  name = "puenktlich-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Alarms (≤ 10 in total, free tier). `enable_alarms` is a plain bool (known at plan time); stage A
# targets only the topic and subscription (see Makefile STAGE_A).

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  count               = var.enable_alarms ? 1 : 0
  alarm_name          = "puenktlich-api-5xx"
  alarm_description   = "API returned more than 5 server errors in 5 minutes."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = var.api_id }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_throttles" {
  count               = var.enable_alarms ? 1 : 0
  alarm_name          = "puenktlich-api-throttles"
  alarm_description   = "API Lambda was throttled (account concurrency limit is 5)."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = var.api_function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "sample_board_errors" {
  count               = var.enable_alarms ? 1 : 0
  alarm_name          = "puenktlich-sample-board-errors"
  alarm_description   = "Sample board refresh failed twice in a row (board goes stale)."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = var.sample_board_function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

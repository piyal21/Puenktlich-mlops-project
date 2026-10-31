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

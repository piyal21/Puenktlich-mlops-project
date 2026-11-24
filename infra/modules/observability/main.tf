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
  count               = var.enable_alarms && var.board_mode == "sample" ? 1 : 0
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

# --- Phase 7: live pipeline (EMF metrics in namespace Puenktlich, dimension `service`) ----------

resource "aws_cloudwatch_metric_alarm" "ingestion_stopped" {
  count               = var.enable_alarms ? 1 : 0
  alarm_name          = "puenktlich-ingestion-stopped"
  alarm_description   = "No departures ingested for 60 minutes (DB API, keys or the ingest Lambda)."
  namespace           = "Puenktlich"
  metric_name         = "EventsIngested"
  dimensions          = { service = "ingest" }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "LessThanOrEqualToThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "ingest_errors" {
  count               = var.enable_alarms ? 1 : 0
  alarm_name          = "puenktlich-ingest-errors"
  alarm_description   = "Live ingest failed in two consecutive 15-minute runs."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = var.ingest_function_name }
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

resource "aws_cloudwatch_metric_alarm" "etl_errors" {
  count               = var.enable_alarms ? 1 : 0
  alarm_name          = "puenktlich-etl-errors"
  alarm_description   = "Nightly live ETL failed (yesterday has no live silver)."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = var.etl_function_name }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "empty_stations" {
  count               = var.enable_alarms ? 1 : 0
  alarm_name          = "puenktlich-empty-stations"
  alarm_description   = "A station had no departures yesterday (EVA drift or a feed gap)."
  namespace           = "Puenktlich"
  metric_name         = "EmptyStations"
  dimensions          = { service = "etl_daily" }
  statistic           = "Maximum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

# --- Phase 9: daily monitor (EMF metrics in namespace Puenktlich, service = monitor) ---------
# Thresholds mirror configs/monitoring.yaml (drift_share_max 0.30, brier_ratio_max 1.10, 3 days).

resource "aws_cloudwatch_metric_alarm" "monitor_missing" {
  count             = var.enable_alarms ? 1 : 0
  alarm_name        = "puenktlich-monitor-missing"
  alarm_description = "No daily monitor result for 26 h (monitor failed or did not run)."
  namespace         = "Puenktlich"
  metric_name       = "LabeledEvents"
  dimensions        = { service = "monitor" }
  statistic         = "SampleCount"
  # 26 hourly buckets: the alarm fires only when every one of them has no datapoint.
  period              = 3600
  evaluation_periods  = 26
  datapoints_to_alarm = 26
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

# Drift / performance: alarm = retrain trigger. The monitor emits RetrainRequested{reason} = 1
# only when it writes control/retrain_requested.json (3 calendar days in a row, same model, no
# open request). Rolling 24 h windows on DriftShare/BrierRatio could not express that and fired
# falsely on 21 + 22 Nov 2026. No OK email: "OK" only means the hour passed.
resource "aws_cloudwatch_metric_alarm" "drift" {
  count               = var.enable_alarms ? 1 : 0
  alarm_name          = "puenktlich-drift"
  alarm_description   = "Retrain requested (reason drift): share of drifted model inputs above 0.30 on 3 days in a row, same model."
  namespace           = "Puenktlich"
  metric_name         = "RetrainRequested"
  dimensions          = { service = "monitor", reason = "drift" }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "performance" {
  count               = var.enable_alarms ? 1 : 0
  alarm_name          = "puenktlich-performance"
  alarm_description   = "Retrain requested (reason performance): daily Brier above 1.10 x the champion's test Brier on 3 days in a row, same model."
  namespace           = "Puenktlich"
  metric_name         = "RetrainRequested"
  dimensions          = { service = "monitor", reason = "performance" }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

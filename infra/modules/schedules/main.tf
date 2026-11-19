# EventBridge Scheduler (Berlin time). Phase 7: live ingestion every 15 min and the nightly ETL;
# the Phase 6 sample board only runs while `board_mode = "sample"` (its Lambda stays for rollback).

data "aws_caller_identity" "current" {}

locals {
  sample_on = var.board_mode == "sample"
  targets = compact([
    var.ingest_function_arn,
    var.etl_function_arn,
    var.monitor_function_arn,
    local.sample_on ? var.sample_board_function_arn : "",
  ])
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "puenktlich-scheduler"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "invoke" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = local.targets
  }
}

resource "aws_iam_role_policy" "invoke" {
  name   = "invoke-scheduled-functions"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.invoke.json
}

moved {
  from = aws_scheduler_schedule.sample_board
  to   = aws_scheduler_schedule.sample_board[0]
}

resource "aws_scheduler_schedule" "sample_board" {
  count                        = local.sample_on ? 1 : 0
  name                         = "puenktlich-sample-board"
  schedule_expression          = var.board_rate
  schedule_expression_timezone = "Europe/Berlin"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = var.sample_board_function_arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_retry_attempts = 0 # the next run is 15 min away
    }
  }
}

resource "aws_scheduler_schedule" "ingest" {
  name                         = "puenktlich-ingest"
  schedule_expression          = var.board_rate
  schedule_expression_timezone = "Europe/Berlin"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = var.ingest_function_arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_retry_attempts = 0 # the next run is 15 min away; a retry could overlap it
    }
  }
}

resource "aws_scheduler_schedule" "etl_daily" {
  name                         = "puenktlich-etl-daily"
  schedule_expression          = var.etl_cron
  schedule_expression_timezone = "Europe/Berlin"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = var.etl_function_arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_retry_attempts = 1 # idempotent: re-running a day overwrites it
    }
  }
}

# Phase 9: daily monitor, after the ETL has written yesterday's live silver (~4 min at 03:30).
resource "aws_scheduler_schedule" "monitor" {
  name                         = "puenktlich-monitor"
  schedule_expression          = var.monitor_cron
  schedule_expression_timezone = "Europe/Berlin"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = var.monitor_function_arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_retry_attempts = 1 # idempotent: re-running a day replaces its results
    }
  }
}

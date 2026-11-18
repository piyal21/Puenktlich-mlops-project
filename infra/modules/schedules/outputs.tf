output "sample_board_schedule" {
  value = one(aws_scheduler_schedule.sample_board[*].name)
}

output "ingest_schedule" {
  value = aws_scheduler_schedule.ingest.name
}

output "etl_schedule" {
  value = aws_scheduler_schedule.etl_daily.name
}

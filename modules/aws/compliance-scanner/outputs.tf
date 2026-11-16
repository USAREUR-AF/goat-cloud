# Compliance Scanner Module Outputs

output "lambda_function_arn" {
  description = "ARN of the compliance scanner Lambda function"
  value       = aws_lambda_function.compliance_scanner.arn
}

output "lambda_function_name" {
  description = "Name of the compliance scanner Lambda function"
  value       = aws_lambda_function.compliance_scanner.function_name
}

output "reports_bucket" {
  description = "Name of the S3 bucket for compliance reports"
  value       = aws_s3_bucket.reports.id
}

output "reports_bucket_arn" {
  description = "ARN of the S3 bucket for compliance reports"
  value       = aws_s3_bucket.reports.arn
}

output "eventbridge_rule_arn" {
  description = "ARN of the nightly compliance scan EventBridge rule"
  value       = aws_cloudwatch_event_rule.nightly_scan.arn
}

output "report_data_table_name" {
  description = "Name of the DynamoDB table for compliance report data"
  value       = aws_dynamodb_table.report_data.name
}

output "report_data_table_arn" {
  description = "ARN of the DynamoDB table for compliance report data"
  value       = aws_dynamodb_table.report_data.arn
}

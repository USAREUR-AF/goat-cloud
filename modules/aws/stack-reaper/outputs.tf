# Stack Reaper Module Outputs

output "reconciler_function_name" {
  description = "Name of the reconciler Lambda function"
  value       = aws_lambda_function.reconciler.function_name
}

output "reconciler_function_arn" {
  description = "ARN of the reconciler Lambda function"
  value       = aws_lambda_function.reconciler.arn
}

output "reaper_function_name" {
  description = "Name of the reaper Lambda function (empty when not armed)"
  value       = var.armed ? aws_lambda_function.reaper[0].function_name : ""
}

output "reaper_function_arn" {
  description = "ARN of the reaper Lambda function (empty when not armed)"
  value       = var.armed ? aws_lambda_function.reaper[0].arn : ""
}

output "state_table_name" {
  description = "Name of the DynamoDB state table"
  value       = aws_dynamodb_table.state.name
}

output "state_table_arn" {
  description = "ARN of the DynamoDB state table"
  value       = aws_dynamodb_table.state.arn
}

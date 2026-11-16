# Hunterkiller Module Outputs

output "notifier_function_arn" {
  description = "ARN of the hunterkiller notifier Lambda function"
  value       = aws_lambda_function.notifier.arn
}

output "notifier_function_name" {
  description = "Name of the hunterkiller notifier Lambda function"
  value       = aws_lambda_function.notifier.function_name
}

output "executor_function_arn" {
  description = "ARN of the hunterkiller executor Lambda function (empty when executor_armed=false)"
  value       = var.executor_armed ? aws_lambda_function.executor[0].arn : ""
}

output "executor_function_name" {
  description = "Name of the hunterkiller executor Lambda function (empty when executor_armed=false)"
  value       = var.executor_armed ? aws_lambda_function.executor[0].function_name : ""
}

output "executor_armed" {
  description = "Whether the executor Lambda is deployed"
  value       = var.executor_armed
}

output "state_table_name" {
  description = "Name of the DynamoDB state tracking table"
  value       = aws_dynamodb_table.state.name
}

output "state_table_arn" {
  description = "ARN of the DynamoDB state tracking table"
  value       = aws_dynamodb_table.state.arn
}

# Backwards compatibility — points to the notifier (same function name as before)
output "lambda_function_arn" {
  description = "ARN of the hunterkiller Lambda function (alias for notifier_function_arn)"
  value       = aws_lambda_function.notifier.arn
}

output "lambda_function_name" {
  description = "Name of the hunterkiller Lambda function (alias for notifier_function_name)"
  value       = aws_lambda_function.notifier.function_name
}

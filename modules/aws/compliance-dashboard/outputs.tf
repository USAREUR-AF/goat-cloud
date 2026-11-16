# =============================================================================
# Compliance Dashboard — Outputs
# =============================================================================

output "overrides_table_name" {
  description = "DynamoDB overrides table name"
  value       = aws_dynamodb_table.overrides.name
}

output "overrides_table_arn" {
  description = "DynamoDB overrides table ARN"
  value       = aws_dynamodb_table.overrides.arn
}

output "lambda_function_name" {
  description = "Dashboard Lambda function name"
  value       = aws_lambda_function.dashboard.function_name
}

output "lambda_function_arn" {
  description = "Dashboard Lambda function ARN (for scoped lambda:InvokeFunctionUrl grants to fronting proxies)"
  value       = aws_lambda_function.dashboard.arn
}

output "function_url" {
  description = "Dashboard Lambda Function URL (empty when enable_function_url is false)"
  value       = var.enable_function_url ? aws_lambda_function_url.dashboard[0].function_url : ""
}

output "target_group_arn" {
  description = "ARN of the dashboard's ALB target group, for listener rules a deployment adds itself (empty when enable_alb is false)"
  value       = var.enable_alb ? aws_lb_target_group.dashboard[0].arn : ""
}

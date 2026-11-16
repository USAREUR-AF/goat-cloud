output "ssm_parameter_name" {
  description = "The SSM parameter name for this registry entry"
  value       = aws_ssm_parameter.registration.name
}

output "ssm_parameter_arn" {
  description = "The SSM parameter ARN for this registry entry"
  value       = aws_ssm_parameter.registration.arn
}

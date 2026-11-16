output "required_tags" {
  description = "List of required tag keys from the compliance contract"
  value       = local.contract.required_tags
}

output "default_tags" {
  description = "Complete set of required tags ready for provider.default_tags"
  value = merge(
    {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = var.managed_by
      Lifecycle   = var.lifecycle_value
      Repository  = var.repository_url
    },
    var.expires_at != null ? { ExpiresAt = var.expires_at } : {}
  )

  precondition {
    condition     = var.expires_at == null || can(regex(local.expires_at_pattern, var.expires_at))
    error_message = "expires_at must be a UTC timestamp ending in Z, for example 2030-01-31T00:00:00Z."
  }
}

output "contract" {
  description = "Full parsed compliance contract"
  value       = local.contract
}

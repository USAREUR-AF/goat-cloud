output "scanner_client_id" {
  description = "Client id of the read-only scanner identity"
  value       = azurerm_user_assigned_identity.scanner.client_id
}

output "policy_assignment_names" {
  description = "Names of the tag policy assignments"
  value       = sort([for a in azurerm_subscription_policy_assignment.require_tag : a.name])
}

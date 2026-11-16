output "state_resource_group" {
  description = "Resource group of the state storage account"
  value       = azurerm_resource_group.bootstrap.name
}

output "state_storage_account" {
  description = "Storage account that holds the repository's Terraform state"
  value       = azurerm_storage_account.state.name
}

output "state_container" {
  description = "Container that holds the state and the registry entries"
  value       = azurerm_storage_container.tfstate.name
}

output "ci_client_id" {
  description = "Client id of the CI identity; the repository variable AZURE_CLIENT_ID"
  value       = azurerm_user_assigned_identity.ci.client_id
}

output "tenant_id" {
  description = "Tenant id; the repository variable AZURE_TENANT_ID"
  value       = azurerm_user_assigned_identity.ci.tenant_id
}

output "stack_resource_groups" {
  description = "Ids of the stack's resource groups, by name; the stack root's resource_group_name is one of the names"
  value       = { for name, group in azurerm_resource_group.stack : name => group.id }
}

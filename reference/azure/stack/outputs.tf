output "resource_group_id" {
  description = "Id of the stack's resource group; the input of the teardown workflow"
  value       = azurerm_resource_group.app.id
}

output "group_tags" {
  description = "Tags on the resource group"
  value       = azurerm_resource_group.app.tags
}

output "registry_blob" {
  description = "Name of the registry entry in the tfstate container"
  value       = azurerm_storage_blob.registry.name
}

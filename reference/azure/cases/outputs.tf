output "resource_groups" {
  description = "Case name to resource group id"
  value       = { for name, group in azurerm_resource_group.case : name => group.id }
}

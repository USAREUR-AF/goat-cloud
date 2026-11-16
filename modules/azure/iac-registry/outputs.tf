output "blob_name" {
  description = "Name of the registry entry blob inside the container"
  value       = azurerm_storage_blob.entry.name
}

output "blob_url" {
  description = "URL of the registry entry blob"
  value       = azurerm_storage_blob.entry.url
}

output "entry" {
  description = "The registry entry as written"
  value       = local.entry
}

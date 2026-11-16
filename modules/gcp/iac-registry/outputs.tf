output "registry_object" {
  description = "The registry entry, as gs://<state_bucket>/iac-registry/<repo_slug>.json"
  value       = "gs://${google_storage_bucket_object.registration.bucket}/${google_storage_bucket_object.registration.name}"
}

output "registry_object_name" {
  description = "The object name of the registry entry inside the state bucket"
  value       = google_storage_bucket_object.registration.name
}

output "state_object" {
  description = "The default workspace's state object, as gs://<state_bucket>/<state_prefix>/default.tfstate"
  value       = "gs://${var.state_bucket}/${var.state_prefix}/default.tfstate"
}

output "entry" {
  description = "The registry entry as written"
  value       = local.entry
}

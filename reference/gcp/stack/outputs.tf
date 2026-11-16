output "registry_object" {
  description = "The registry entry, as gs://<state_bucket>/iac-registry/<slug>.json"
  value       = "gs://${google_storage_bucket_object.registry.bucket}/${google_storage_bucket_object.registry.name}"
}

output "labels" {
  description = "The labels the provider puts on every resource of this stack"
  value       = local.labels
}

output "topic" {
  description = "Name of the example topic"
  value       = google_pubsub_topic.example.name
}

output "bucket" {
  description = "Name of the example bucket"
  value       = google_storage_bucket.example.name
}

output "project_contract_missing" {
  description = "Contract labels the project lacks; empty when the project carries the contract (rule 2)"
  value       = local.missing_project_labels
}

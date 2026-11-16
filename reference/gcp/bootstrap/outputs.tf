output "state_bucket" {
  description = "The owner's state bucket, for ../bootstrap and ../platform: -backend-config=bucket=<name>. No CI identity holds a role on it"
  value       = google_storage_bucket.state["state"].name
}

output "stack_state_bucket" {
  description = "The stack's state bucket: -backend-config=bucket=<name> and var.state_bucket in ../stack, var.stack_state_bucket in ../platform, and the repository variable STATE_BUCKET"
  value       = google_storage_bucket.state["stack_state"].name
}

output "workload_identity_provider" {
  description = "Full name of the provider; the value of workload_identity_provider in the workflows"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deploy_principals" {
  description = "The federated identity of each deploy workflow on github_deploy_ref, by file name, as bound to the deploy grants"
  value       = local.deploy_principals
}

output "scanner_email" {
  description = "Email of the scanner's service account; its object read on the state bucket is granted by this root"
  value       = google_service_account.scanner.email
}

output "tag_keys" {
  description = "Tag keys created under the project, by short name; empty with tag_mode = none"
  value       = { for name, key in google_tags_tag_key.fact : name => key.id }
}

output "stack_bucket_prefix" {
  description = "Name prefix of the buckets the deploy identity may manage"
  value       = local.stack_bucket_prefix
}

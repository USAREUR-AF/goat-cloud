output "topics" {
  description = "Case name to topic id"
  value       = { for name, topic in google_pubsub_topic.case : name => topic.id }
}

output "service_account" {
  description = "Email of the case 7 service account"
  value       = google_service_account.unlabelable.email
}

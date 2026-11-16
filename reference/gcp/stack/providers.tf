# Rule 4: resources are labeled by the provider, never by hand.
provider "google" {
  project        = var.project_id
  default_labels = local.labels
}

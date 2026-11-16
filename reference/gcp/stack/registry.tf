# Rule 5: the repository registers itself with one object beside its state.
# The scanner's read grant on the bucket is made once by ../platform, so this
# root needs no IAM permission on the state bucket.

data "google_storage_bucket" "state" {
  name = var.state_bucket
}

locals {
  registry_entry = {
    version   = "1.1"
    repo_url  = var.repo_url
    repo_id   = var.repo_id
    iac_type  = "terraform"
    scopes    = [var.project_id]
    owner     = var.owner
    contact   = var.contact
    purpose   = var.purpose
    lifecycle = var.lifecycle_value
    state_location = {
      backend = "gcs"
      bucket  = var.state_bucket
      prefix  = "stack"
    }
    environment       = var.environment
    expires_at        = var.expires_at
    teardown_workflow = var.teardown_workflow
  }
}

resource "google_storage_bucket_object" "registry" {
  bucket       = var.state_bucket
  name         = "iac-registry/${var.slug}.json"
  content      = jsonencode(local.registry_entry)
  content_type = "application/json"

  lifecycle {
    precondition {
      condition     = var.contract_check == "report" || length(local.missing_project_labels) == 0
      error_message = "Rule 2: the project lacks the contract labels: ${join(", ", local.missing_project_labels)}. Set them on the project before the first plan."
    }

    precondition {
      condition     = !contains(["temporary", "experiment"], var.lifecycle_value) || var.expires_at != null
      error_message = "Rule 8: lifecycle_value = \"${var.lifecycle_value}\" requires expires_at."
    }

    precondition {
      condition     = try(data.google_storage_bucket.state.effective_labels["role"], data.google_storage_bucket.state.labels["role"], "") == "iac-registry"
      error_message = "Rule 1: the state bucket must carry the label role=iac-registry."
    }

    precondition {
      condition     = length(local.empty_labels) == 0
      error_message = "Rule 4: these facts have no value after normalization: ${join(", ", local.empty_labels)}."
    }
  }
}

# iac-registry
# Writes this repository's registry entry as a JSON object beside its state.
#
# The entry is the object iac-registry/<repo_slug>.json in the state bucket. A
# scanner finds every such bucket by the label role=iac-registry, lists the
# iac-registry/ prefix and reads each state the entries point to. The entry
# holds no timestamp and no other value that changes between applies, so the
# object is rewritten only when an input changes.

locals {
  # owner, contact and expires_at are part of the entry only when set;
  # lifecycle always is. On Google Cloud the contact is never a label, so this entry is where
  # it is stated.
  entry = merge(
    {
      repo_url = var.repo_url
      iac_type = lower(var.iac_type)
      project  = var.project
      state_location = {
        backend = "gcs"
        bucket  = var.state_bucket
        prefix  = var.state_prefix
      }
      environment = var.environment
      version     = "1.0"
    },
    var.owner != null ? { owner = var.owner } : {},
    var.contact != null ? { contact = var.contact } : {},
    { lifecycle = var.lifecycle_value },
    var.expires_at != null ? { expires_at = var.expires_at } : {},
  )

  object_name = "iac-registry/${var.repo_slug}.json"
}

data "google_storage_bucket" "state" {
  count = var.require_bucket_label ? 1 : 0
  name  = var.state_bucket
}

resource "google_storage_bucket_object" "registration" {
  bucket       = var.state_bucket
  name         = local.object_name
  content      = jsonencode(local.entry)
  content_type = "application/json"

  lifecycle {
    precondition {
      condition     = !var.require_bucket_label || try(data.google_storage_bucket.state[0].effective_labels["role"], data.google_storage_bucket.state[0].labels["role"], "") == "iac-registry"
      error_message = "The state bucket must carry the label role=iac-registry, or a scanner cannot find this entry."
    }

    precondition {
      condition     = contains(local.registry_lifecycle_values, var.lifecycle_value)
      error_message = "lifecycle_value must be one of: ${join(", ", local.registry_lifecycle_values)}."
    }

    precondition {
      condition     = var.expires_at == null || can(regex(local.registry_expires_at_pattern, coalesce(var.expires_at, "-")))
      error_message = "expires_at must match ${local.registry_expires_at_pattern}."
    }

    precondition {
      condition     = !contains(local.registry_expiry_lifecycles, var.lifecycle_value) || var.expires_at != null
      error_message = "lifecycle_value ${var.lifecycle_value} requires expires_at (rule ${var.lifecycle_value}-requires-expires-at)."
    }
  }
}

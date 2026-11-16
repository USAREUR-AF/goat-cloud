# Rule 1: state lives in Cloud Storage buckets in the same project, two per
# repository.
#
# - state: the owner's state, prefixes bootstrap/ and platform/. Only the
#   project's owners can read or write it; no CI identity holds a role on it.
# - stack_state: the stack's state and its registry entry, prefixes stack/
#   and iac-registry/. ../platform gives the deploy workflows object access to
#   this bucket only. It carries role=iac-registry so a scanner finds it.
#
# Both are versioned, use uniform bucket-level access and refuse public
# access.

resource "random_id" "suffix" {
  byte_length = 2
}

locals {
  bucket_labels = {
    owner       = var.owner
    workload    = var.workload
    environment = var.environment
    lifecycle   = "persistent"
    managed-by  = "terraform"
  }

  buckets = {
    state = {
      name   = "${var.slug}-tfstate-${random_id.suffix.hex}"
      labels = local.bucket_labels
    }
    stack_state = {
      name   = "${var.slug}-stack-tfstate-${random_id.suffix.hex}"
      labels = merge(local.bucket_labels, { role = "iac-registry" })
    }
  }
}

resource "google_storage_bucket" "state" {
  for_each = local.buckets

  name     = each.value.name
  project  = var.project_id
  location = var.location

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.force_destroy

  versioning {
    enabled = true
  }

  dynamic "soft_delete_policy" {
    for_each = var.soft_delete_retention_seconds == null ? [] : [var.soft_delete_retention_seconds]
    content {
      retention_duration_seconds = soft_delete_policy.value
    }
  }

  labels = each.value.labels
}

# The single state bucket of earlier versions is the owner's bucket now.
moved {
  from = google_storage_bucket.state
  to   = google_storage_bucket.state["state"]
}

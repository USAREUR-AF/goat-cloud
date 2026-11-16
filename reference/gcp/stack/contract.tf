# The contract as this root applies it.
#
# Inline here so the reference root has no module source to fetch. The
# published modules compliance-labels and iac-registry do not yet render the
# `workload` key, the project-level check or the registry fields below.

data "google_project" "this" {
  project_id = var.project_id
}

locals {
  required_project_labels = ["workload", "owner", "environment", "lifecycle", "managed-by"]

  project_labels         = coalesce(data.google_project.this.labels, {})
  missing_project_labels = [for key in local.required_project_labels : key if !contains(keys(local.project_labels), key)]

  expires_at_label = var.expires_at == null ? null : formatdate("YYYYMMDD't'hhmm'z'", var.expires_at)

  # A label value holds lowercase letters, digits, underscores and dashes, up
  # to 63 characters.
  normalized = {
    for key, value in {
      workload    = var.workload
      owner       = var.owner
      environment = var.environment
      lifecycle   = var.lifecycle_value
      managed-by  = "terraform"
    } : key => substr(replace(lower(value), "/[^a-z0-9_-]/", "_"), 0, 63)
  }

  labels = merge(
    local.normalized,
    local.expires_at_label == null ? {} : { expires-at = local.expires_at_label },
  )

  empty_labels = [for key, value in local.normalized : key if value == ""]
}

# Rule 2, reported. In enforce mode the same condition also stops the plan
# through the precondition in registry.tf.
check "project_contract" {
  assert {
    condition     = length(local.missing_project_labels) == 0
    error_message = "Rule 2: the project lacks the contract labels: ${join(", ", local.missing_project_labels)}."
  }
}

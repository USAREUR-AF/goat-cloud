# Verification cases: one empty Pub/Sub topic per case, each breaking exactly
# one rule of the contract, and one service account for the type that cannot
# carry labels. Labels are set per resource on purpose; this root is the one
# place that does not use provider default_labels, because each case needs its
# own labels. Nothing here costs anything while empty.
#
# ../truth.yaml states the result a scan must report for each.

locals {
  conforming = {
    workload    = "reference"
    owner       = "reference-team"
    environment = "dev"
    lifecycle   = "persistent"
    managed-by  = "terraform"
  }

  cases = {
    "case-1-conforming" = local.conforming

    "case-2-missing-environment" = {
      for key, value in local.conforming : key => value if key != "environment"
    }

    "case-3-expired" = merge(local.conforming, {
      lifecycle  = "temporary"
      expires-at = "20200101t0000z"
    })

    "case-4-expiry-unreadable" = merge(local.conforming, {
      lifecycle  = "temporary"
      expires-at = "next-week"
    })

    "case-5-invalid-managed-by" = merge(local.conforming, {
      managed-by = "chef"
    })

    "case-6-protected" = merge(local.conforming, {
      protected = "true"
    })
  }
}

resource "google_pubsub_topic" "case" {
  for_each = local.cases

  project = var.project_id
  name    = "${var.prefix}-${each.key}"
  labels  = each.value
}

# Case 7: a service account has no labels field.
resource "google_service_account" "unlabelable" {
  project      = var.project_id
  account_id   = "${var.prefix}-case-7-unlabelable"
  display_name = "Verification case: a type that cannot carry labels"
}

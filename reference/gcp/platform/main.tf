# What the owner of the project sets up once: the APIs (rule 7), the keyless
# sign-in path for CI (P3, rule 6), the scanner's read-only identity (P4) and
# the repository's deploy grants. The deploy grants go to two workflow files of
# one repository, deploy.yml and teardown.yml, run from one ref, and reach the
# stack's state bucket only through object access. The owner's state bucket,
# which holds this root's state and that of ../bootstrap, gets no deploy grant.
# No deploy grant can change IAM or enable an API.

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_project_service" "required" {
  for_each = toset(var.apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# A deleted pool keeps its id reserved for 30 days, so the id carries a suffix.
resource "random_id" "pool" {
  byte_length = 2
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.slug}-gh-${random_id.pool.hex}"
  display_name              = "GitHub Actions"
  description               = "Sign-in for CI workflows; no service account key"

  depends_on = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"

  # attribute.workflow joins the numeric repository id and workflow_ref, the
  # workflow file and the ref it ran from, so a binding names one workflow
  # file of one repository on one branch.
  attribute_mapping = {
    "google.subject"                = "assertion.sub"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
    "attribute.workflow"            = "assertion.repository_id + '@' + assertion.workflow_ref"
  }

  # Sign-in itself is refused for any other organization, repository,
  # workflow file or ref: another workflow on main, a pull_request_target
  # workflow among them, gets no token.
  attribute_condition = "assertion.repository_owner_id == '${var.github_org_id}' && assertion.repository_id == '${var.github_repo_id}' && assertion.workflow_ref in [${join(", ", [for ref in local.workflow_refs : "'${ref}'"])}]"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

locals {
  # workflow_ref as GitHub issues it: <owner>/<repo>/.github/workflows/<file>@<ref>.
  # A pull request runs on refs/pull/<n>/merge and another branch on its own
  # ref, so neither matches.
  workflow_refs = {
    for file in var.github_workflows : file => "${var.github_repository}/.github/workflows/${file}@${var.github_deploy_ref}"
  }

  deploy_principals = {
    for file, ref in local.workflow_refs : file => "principalSet://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github.workload_identity_pool_id}/attribute.workflow/${var.github_repo_id}@${ref}"
  }

  deploy_role_grants = {
    for pair in setproduct(var.deploy_roles, var.github_workflows) : "${pair[0]} ${pair[1]}" => { role = pair[0], workflow = pair[1] }
  }

  # The slug has no underscore, so <slug>_ is a prefix no other slug's
  # buckets can start with, and no state bucket (<slug>-...) either.
  stack_bucket_prefix = coalesce(var.stack_bucket_prefix, "${var.slug}_")

  # A custom role id is letters, digits, underscores and periods. A deleted
  # role keeps its id for up to 37 days, so the id carries the pool's suffix.
  role_id_base = "${replace(var.slug, "-", "_")}_${random_id.pool.hex}"
}

resource "google_project_iam_member" "deploy" {
  for_each = local.deploy_role_grants

  project = var.project_id
  role    = each.value.role
  member  = local.deploy_principals[each.value.workflow]
}

# The stack's own buckets. Creating a bucket is a project-level permission
# with no bucket to name yet, so it is a role of its own without a condition;
# it gives no access to any bucket that exists.
resource "google_project_iam_custom_role" "stack_bucket_create" {
  project     = var.project_id
  role_id     = "${local.role_id_base}_bucket_create"
  title       = "${var.slug}: create buckets"
  description = "Create a bucket. Access to it comes from the stack_buckets role, limited by name prefix"
  permissions = ["storage.buckets.create"]
}

resource "google_project_iam_member" "deploy_stack_bucket_create" {
  for_each = local.deploy_principals

  project = var.project_id
  role    = google_project_iam_custom_role.stack_bucket_create.id
  member  = each.value
}

# Read, change and delete buckets of this project whose name starts with the
# stack's prefix, and empty them on destroy. A project-level binding reaches
# only this project's buckets; the condition narrows it to the prefix. No IAM
# permission: the stack cannot grant access to its buckets.
resource "google_project_iam_custom_role" "stack_buckets" {
  project     = var.project_id
  role_id     = "${local.role_id_base}_stack_buckets"
  title       = "${var.slug}: manage the stack's buckets"
  description = "Manage the buckets named ${local.stack_bucket_prefix}*; bound with a name condition"
  permissions = [
    "storage.buckets.get",
    "storage.buckets.update",
    "storage.buckets.delete",
    "storage.objects.list",
    "storage.objects.get",
    "storage.objects.delete",
  ]
}

resource "google_project_iam_member" "deploy_stack_buckets" {
  for_each = local.deploy_principals

  project = var.project_id
  role    = google_project_iam_custom_role.stack_buckets.id
  member  = each.value

  condition {
    title       = "stack-buckets-by-prefix"
    description = "Buckets whose name starts with ${local.stack_bucket_prefix}, and their objects"
    expression  = "resource.name.startsWith(\"projects/_/buckets/${local.stack_bucket_prefix}\")"
  }

  lifecycle {
    precondition {
      condition     = startswith(local.stack_bucket_prefix, "${var.slug}_")
      error_message = "stack_bucket_prefix must start with ${var.slug}_, so that it matches no other repository's buckets and no state bucket."
    }

    precondition {
      condition     = !startswith(var.stack_state_bucket, local.stack_bucket_prefix)
      error_message = "stack_bucket_prefix must not match the stack's state bucket ${var.stack_state_bucket}: the deploy identity would manage the state bucket itself."
    }
  }
}

# The stack's state bucket: objects only. Read and write the stack's state
# and the registry entry, take the state lock, read the bucket's labels
# (rule 1). No bucket IAM, no public-access or versioning settings. The
# bucket holds no other root's state.
resource "google_storage_bucket_iam_member" "deploy_state_objects" {
  for_each = local.deploy_principals

  bucket = var.stack_state_bucket
  role   = "roles/storage.objectUser"
  member = each.value
}

resource "google_storage_bucket_iam_member" "deploy_state_bucket_read" {
  for_each = local.deploy_principals

  bucket = var.stack_state_bucket
  role   = "roles/storage.legacyBucketReader"
  member = each.value
}

resource "google_service_account" "scanner" {
  project      = var.project_id
  account_id   = var.scanner_account_id
  display_name = "Read-only inventory scanner"

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "scanner_asset_viewer" {
  project = var.project_id
  role    = "roles/cloudasset.viewer"
  member  = "serviceAccount:${google_service_account.scanner.email}"
}

# Rule 5: the scanner reads the registry entries and the state beside them.
# Granted here, by the project's owner, so the deploy identity needs no IAM
# permission on the state bucket.
resource "google_storage_bucket_iam_member" "scanner_state_read" {
  bucket = var.stack_state_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.scanner.email}"
}

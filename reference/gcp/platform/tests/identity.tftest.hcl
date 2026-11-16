# Tests against mocked providers: no credentials, no API calls. Runs that read
# a principal use `command = apply`, which applies to the mocks only, because
# the pool id is not known at plan time.
mock_provider "google" {}
mock_provider "random" {}

variables {
  project_id         = "example-project"
  slug               = "example-service"
  stack_state_bucket = "example-service-stack-tfstate-1a2b"
  github_org_id      = "12345678"
  github_repo_id     = "123456789"
  github_repository  = "example-org/example-service"
}

override_data {
  target = data.google_project.this
  values = {
    number = "111111111111"
  }
}

override_resource {
  target = random_id.pool
  values = {
    hex = "1a2b"
  }
}

run "grants_go_to_the_two_workflows_on_main_only" {
  command = apply

  assert {
    condition     = endswith(output.deploy_principals["deploy.yml"], "/attribute.workflow/123456789@example-org/example-service/.github/workflows/deploy.yml@refs/heads/main")
    error_message = "The deploy principal is deploy.yml of the repository, run from refs/heads/main."
  }

  assert {
    condition     = endswith(output.deploy_principals["teardown.yml"], "/attribute.workflow/123456789@example-org/example-service/.github/workflows/teardown.yml@refs/heads/main")
    error_message = "The teardown principal is teardown.yml of the repository, run from refs/heads/main."
  }

  assert {
    condition     = length(output.deploy_principals) == 2
    error_message = "No other workflow receives a principal."
  }

  assert {
    condition     = google_iam_workload_identity_pool_provider.github.attribute_mapping["attribute.workflow"] == "assertion.repository_id + '@' + assertion.workflow_ref"
    error_message = "attribute.workflow joins the repository id and workflow_ref."
  }

  assert {
    condition     = google_iam_workload_identity_pool_provider.github.attribute_condition == "assertion.repository_owner_id == '12345678' && assertion.repository_id == '123456789' && assertion.workflow_ref in ['example-org/example-service/.github/workflows/deploy.yml@refs/heads/main', 'example-org/example-service/.github/workflows/teardown.yml@refs/heads/main']"
    error_message = "Sign-in is refused for any other organization, repository, workflow file or ref."
  }

  assert {
    condition     = alltrue([for m in google_project_iam_member.deploy : contains(values(output.deploy_principals), m.member)])
    error_message = "Every project role goes to a workflow principal."
  }
}

run "no_broad_storage_or_service_usage_role" {
  command = plan

  assert {
    condition     = toset([for g in google_project_iam_member.deploy : g.role]) == toset(["roles/pubsub.editor"])
    error_message = "The only predefined project role is pubsub.editor."
  }

  assert {
    condition     = contains(var.apis, "pubsub.googleapis.com")
    error_message = "The stack's API is enabled here, so CI needs no Service Usage role."
  }

  assert {
    condition     = alltrue([for g in google_storage_bucket_iam_member.deploy_state_objects : g.role == "roles/storage.objectUser" && g.bucket == "example-service-stack-tfstate-1a2b"])
    error_message = "The deploy workflows reach the stack's state bucket through object access on that bucket only."
  }

  assert {
    condition     = alltrue([for g in google_project_iam_member.deploy_stack_buckets : g.condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/example-service_\")"])
    error_message = "Bucket management is limited to buckets whose name starts with <slug>_."
  }

  assert {
    condition     = length([for p in google_project_iam_custom_role.stack_buckets.permissions : p if strcontains(p, "IamPolicy")]) == 0
    error_message = "The stack bucket role holds no IAM permission."
  }

  assert {
    condition     = google_storage_bucket_iam_member.scanner_state_read.role == "roles/storage.objectViewer" && google_storage_bucket_iam_member.scanner_state_read.bucket == "example-service-stack-tfstate-1a2b"
    error_message = "The scanner's read grant is on the stack's state bucket."
  }
}

run "another_ref_can_be_named" {
  command = apply

  variables {
    github_deploy_ref = "refs/heads/release"
  }

  assert {
    condition     = endswith(output.deploy_principals["deploy.yml"], "/deploy.yml@refs/heads/release")
    error_message = "github_deploy_ref names the ref that receives the deploy grants."
  }
}

run "a_prefix_outside_the_slug_is_refused" {
  command = plan

  variables {
    stack_bucket_prefix = "example-"
  }

  expect_failures = [google_project_iam_member.deploy_stack_buckets]
}

run "a_prefix_that_matches_the_state_bucket_is_refused" {
  command = plan

  variables {
    slug                = "example-service"
    stack_bucket_prefix = "example-service_"
    stack_state_bucket  = "example-service_state"
  }

  expect_failures = [google_project_iam_member.deploy_stack_buckets]
}

run "a_role_off_the_allowed_list_is_refused" {
  command = plan

  variables {
    deploy_roles = ["roles/pubsub.editor", "roles/storage.objectAdmin"]
  }

  expect_failures = [var.deploy_roles]
}

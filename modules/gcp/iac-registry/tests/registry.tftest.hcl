# Plan-only tests against a mocked provider: no credentials, no API calls.
mock_provider "google" {}

variables {
  repo_slug    = "example-service"
  repo_url     = "https://git.example/example-org/example-service"
  project      = "example-app"
  environment  = "dev"
  state_bucket = "example-project-example-service-tfstate"
  state_prefix = "example-service"
}

override_data {
  target = data.google_storage_bucket.state[0]
  values = {
    labels           = { role = "iac-registry" }
    effective_labels = { role = "iac-registry" }
  }
}

run "entry_is_an_object_beside_the_state" {
  command = plan

  assert {
    condition     = google_storage_bucket_object.registration.bucket == "example-project-example-service-tfstate"
    error_message = "The entry is written to the state bucket."
  }

  assert {
    condition     = google_storage_bucket_object.registration.name == "iac-registry/example-service.json"
    error_message = "The entry is the object iac-registry/<repo_slug>.json."
  }

  assert {
    condition     = google_storage_bucket_object.registration.content_type == "application/json"
    error_message = "The entry is served as JSON."
  }

  assert {
    condition     = output.registry_object == "gs://example-project-example-service-tfstate/iac-registry/example-service.json"
    error_message = "registry_object is the gs:// address of the entry."
  }

  assert {
    condition     = output.state_object == "gs://example-project-example-service-tfstate/example-service/default.tfstate"
    error_message = "state_object is the default workspace's state object."
  }
}

run "payload_has_exactly_the_registry_fields" {
  command = plan

  assert {
    condition = google_storage_bucket_object.registration.content == jsonencode({
      repo_url = "https://git.example/example-org/example-service"
      iac_type = "terraform"
      project  = "example-app"
      state_location = {
        backend = "gcs"
        bucket  = "example-project-example-service-tfstate"
        prefix  = "example-service"
      }
      environment = "dev"
      version     = "1.0"
      lifecycle   = "persistent"
    })
    error_message = "The payload is repo_url, iac_type, project, state_location{backend,bucket,prefix}, environment, version and lifecycle, with no value that changes between applies."
  }
}

run "iac_type_is_carried" {
  command = plan

  variables {
    iac_type = "pulumi"
  }

  assert {
    condition     = jsondecode(google_storage_bucket_object.registration.content).iac_type == "pulumi"
    error_message = "iac_type is written to the entry."
  }
}

run "unknown_iac_type_is_refused" {
  command = plan

  variables {
    iac_type = "shell-script"
  }

  expect_failures = [var.iac_type]
}

run "repo_url_without_a_scheme_is_refused" {
  command = plan

  variables {
    repo_url = "example-org/example-service"
  }

  expect_failures = [var.repo_url]
}

run "slug_with_a_path_separator_is_refused" {
  command = plan

  variables {
    repo_slug = "example/service"
  }

  expect_failures = [var.repo_slug]
}

run "state_prefix_inside_the_registry_prefix_is_refused" {
  command = plan

  variables {
    state_prefix = "iac-registry/example-service"
  }

  expect_failures = [var.state_prefix]
}

run "unlabelled_bucket_is_refused" {
  command = plan

  override_data {
    target = data.google_storage_bucket.state[0]
    values = {
      labels           = {}
      effective_labels = {}
    }
  }

  expect_failures = [google_storage_bucket_object.registration]
}

run "bucket_label_check_can_be_turned_off" {
  command = plan

  variables {
    require_bucket_label = false
  }

  assert {
    condition     = google_storage_bucket_object.registration.name == "iac-registry/example-service.json"
    error_message = "With require_bucket_label = false the bucket is not read."
  }
}

run "owner_and_contact_are_carried_when_set" {
  command = plan

  variables {
    owner   = "platform-team"
    contact = "platform-team@example.invalid"
  }

  assert {
    condition     = jsondecode(google_storage_bucket_object.registration.content).owner == "platform-team"
    error_message = "owner is written to the entry."
  }

  assert {
    condition     = jsondecode(google_storage_bucket_object.registration.content).contact == "platform-team@example.invalid"
    error_message = "contact is written to the entry: on Google Cloud it is never a label."
  }
}

run "opentofu_and_infrastructure_manager_are_iac_tools" {
  command = plan

  variables {
    iac_type = "infrastructure-manager"
  }

  assert {
    condition     = jsondecode(google_storage_bucket_object.registration.content).iac_type == "infrastructure-manager"
    error_message = "infrastructure-manager is in the contract's list."
  }
}

run "deployment_manager_is_refused" {
  command = plan

  variables {
    iac_type = "deployment-manager"
  }

  expect_failures = [var.iac_type]
}

run "lifecycle_and_expiry_are_carried_when_set" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "20301201t0000z"
  }

  assert {
    condition     = jsondecode(google_storage_bucket_object.registration.content).lifecycle == "temporary"
    error_message = "lifecycle is written to the entry."
  }

  assert {
    condition     = jsondecode(google_storage_bucket_object.registration.content).expires_at == "20301201t0000z"
    error_message = "expires_at is written to the entry."
  }
}

run "iac_type_compares_without_regard_to_case" {
  command = plan

  variables {
    iac_type = "Terraform"
  }

  assert {
    condition     = jsondecode(google_storage_bucket_object.registration.content).iac_type == "terraform"
    error_message = "iac_type is accepted in any case and written in lowercase."
  }
}

run "temporary_without_expiry_is_refused" {
  command = plan

  variables {
    lifecycle_value = "experiment"
  }

  expect_failures = [google_storage_bucket_object.registration]
}

run "unknown_lifecycle_is_refused" {
  command = plan

  variables {
    lifecycle_value = "forever"
  }

  expect_failures = [google_storage_bucket_object.registration]
}

run "expiry_in_another_form_is_refused" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2030-12-01T00:00:00Z"
  }

  expect_failures = [google_storage_bucket_object.registration]
}

run "persistent_by_default_with_no_expiry" {
  command = plan

  assert {
    condition     = jsondecode(google_storage_bucket_object.registration.content).lifecycle == "persistent"
    error_message = "lifecycle defaults to persistent and is always written."
  }

  assert {
    condition     = !contains(keys(jsondecode(google_storage_bucket_object.registration.content)), "expires_at")
    error_message = "A persistent entry without expires_at has no expires_at field."
  }
}

run "temporary_without_expiry_fails_the_plan" {
  command = plan

  variables {
    lifecycle_value = "temporary"
  }

  expect_failures = [google_storage_bucket_object.registration]
}

run "credential_in_repo_url_is_refused" {
  command = plan

  variables {
    repo_url = "https://user:token@git.example/example-org/example-repo"
  }

  expect_failures = [var.repo_url]
}

run "user_in_repo_url_is_refused" {
  command = plan

  variables {
    repo_url = "ssh://git@git.example/example-org/example-repo.git"
  }

  expect_failures = [var.repo_url]
}

run "git_ssh_form_is_accepted" {
  command = plan

  variables {
    repo_url = "git@git.example:example-org/example-repo.git"
  }
}

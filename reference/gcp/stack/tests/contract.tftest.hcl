# Plan-only tests against mocked providers: no credentials, no API calls.
mock_provider "google" {}
mock_provider "random" {}

variables {
  project_id   = "example-project"
  location     = "us-central1"
  state_bucket = "example-service-stack-tfstate-1a2b"
  slug         = "example-service"
  repo_url     = "https://github.com/example-org/example-service"
  repo_id      = "123456789"
  workload     = "Order Events"
  owner        = "Payments Team"
  contact      = "payments-team@example.com"
  purpose      = "Order events topic and archive bucket"
}

override_data {
  target = data.google_storage_bucket.state
  values = {
    labels           = { role = "iac-registry" }
    effective_labels = { role = "iac-registry" }
  }
}

run "project_with_the_contract_plans" {
  command = plan

  override_data {
    target = data.google_project.this
    values = {
      labels = { workload = "order-events", owner = "payments-team", environment = "dev", lifecycle = "persistent", managed-by = "terraform" }
    }
  }

  assert {
    condition     = output.labels == { workload = "order_events", owner = "payments_team", environment = "dev", lifecycle = "persistent", managed-by = "terraform" }
    error_message = "Labels are the five facts, normalized to the label character rule."
  }

  assert {
    condition     = google_storage_bucket_object.registry.name == "iac-registry/example-service.json"
    error_message = "The registry entry is iac-registry/<slug>.json."
  }

  assert {
    condition     = length(output.project_contract_missing) == 0
    error_message = "A project with all five labels misses none."
  }
}

run "project_without_the_contract_stops_the_plan" {
  command = plan

  override_data {
    target = data.google_project.this
    values = {
      labels = { environment = "dev" }
    }
  }

  expect_failures = [
    check.project_contract,
    google_storage_bucket_object.registry,
  ]
}

run "report_mode_continues_and_lists_what_is_missing" {
  command = plan

  variables {
    contract_check = "report"
  }

  override_data {
    target = data.google_project.this
    values = {
      labels = { environment = "dev" }
    }
  }

  expect_failures = [check.project_contract]

  assert {
    condition     = output.project_contract_missing == ["workload", "owner", "lifecycle", "managed-by"]
    error_message = "Report mode lists the missing labels."
  }
}

run "temporary_without_expiry_stops_the_plan" {
  command = plan

  variables {
    lifecycle_value = "temporary"
  }

  override_data {
    target = data.google_project.this
    values = {
      labels = { workload = "a", owner = "b", environment = "dev", lifecycle = "persistent", managed-by = "terraform" }
    }
  }

  expect_failures = [google_storage_bucket_object.registry]
}

run "temporary_renders_the_expiry_label" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2026-12-01T00:00:00Z"
  }

  override_data {
    target = data.google_project.this
    values = {
      labels = { workload = "a", owner = "b", environment = "dev", lifecycle = "persistent", managed-by = "terraform" }
    }
  }

  assert {
    condition     = output.labels["expires-at"] == "20261201t0000z"
    error_message = "The expiry label is yyyymmddthhmmz."
  }

  assert {
    condition     = jsondecode(google_storage_bucket_object.registry.content).expires_at == "2026-12-01T00:00:00Z"
    error_message = "The registry entry carries the expiry as RFC 3339."
  }
}

run "state_bucket_without_the_role_label_stops_the_plan" {
  command = plan

  override_data {
    target = data.google_storage_bucket.state
    values = {
      labels           = {}
      effective_labels = {}
    }
  }

  override_data {
    target = data.google_project.this
    values = {
      labels = { workload = "a", owner = "b", environment = "dev", lifecycle = "persistent", managed-by = "terraform" }
    }
  }

  expect_failures = [google_storage_bucket_object.registry]
}

run "repo_url_with_a_token_is_refused" {
  command = plan

  variables {
    repo_url = "https://user:token@git.example/example-org/example-service"
  }

  override_data {
    target = data.google_project.this
    values = {
      labels = { workload = "a", owner = "b", environment = "dev", lifecycle = "persistent", managed-by = "terraform" }
    }
  }

  expect_failures = [var.repo_url]
}

run "repo_url_with_a_user_name_is_refused" {
  command = plan

  variables {
    repo_url = "https://user@git.example/example-org/example-service"
  }

  override_data {
    target = data.google_project.this
    values = {
      labels = { workload = "a", owner = "b", environment = "dev", lifecycle = "persistent", managed-by = "terraform" }
    }
  }

  expect_failures = [var.repo_url]
}

run "ssh_repo_url_is_accepted" {
  command = plan

  variables {
    repo_url = "git@git.example:example-org/example-service.git"
  }

  override_data {
    target = data.google_project.this
    values = {
      labels = { workload = "a", owner = "b", environment = "dev", lifecycle = "persistent", managed-by = "terraform" }
    }
  }

  assert {
    condition     = jsondecode(google_storage_bucket_object.registry.content).repo_url == "git@git.example:example-org/example-service.git"
    error_message = "An SSH URL is written to the registry entry as given."
  }
}

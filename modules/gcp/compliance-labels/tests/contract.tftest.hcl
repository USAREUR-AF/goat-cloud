# Plan-only tests of the label rendering. The module declares no provider and
# no resource, so nothing here needs credentials or reaches a cloud.

variables {
  project     = "Example App"
  environment = "dev"
  owner       = "platform-team"
}

run "defaults_render_the_default_keys" {
  command = plan

  assert {
    condition = jsonencode(output.default_labels) == jsonencode({
      workload     = "example_app"
      environment  = "dev"
      "managed-by" = "terraform"
      lifecycle    = "persistent"
      owner        = "platform-team"
    })
    error_message = "The five required facts render under workload, environment, managed-by, lifecycle and owner, and nothing else is emitted."
  }

  assert {
    condition     = jsonencode(output.required_labels) == jsonencode(["workload", "environment", "managed-by", "lifecycle", "owner"])
    error_message = "required_labels lists the rendered keys of the required facts."
  }
}

run "optional_facts_render_when_set" {
  command = plan

  variables {
    owner       = "Platform Team"
    cost_center = "CC 1234"
    protected   = true
    schedule    = "Office Hours"
  }

  assert {
    condition     = output.default_labels["owner"] == "platform_team" && output.default_labels["cost-center"] == "cc_1234"
    error_message = "Optional facts render lowercased under owner and cost-center."
  }

  assert {
    condition     = output.default_labels["protected"] == "true" && output.default_labels["schedule"] == "office_hours"
    error_message = "protected renders as true and schedule as a label value."
  }

  assert {
    condition     = !contains(keys(output.default_labels), "contact") && output.contract.carriers["contact"] == "registry-entry"
    error_message = "contact is not a label: the registry entry carries it."
  }
}

run "values_are_lowercased_and_restricted" {
  command = plan

  variables {
    project = "My.App/v2 (EU)"
  }

  assert {
    condition     = output.default_labels["workload"] == "my_app_v2__eu_"
    error_message = "Characters outside [a-z0-9_-] become underscores after lowercasing."
  }
}

run "long_value_is_cut_to_63_characters" {
  command = plan

  variables {
    owner = "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz"
  }

  assert {
    condition     = length(output.default_labels["owner"]) == 63
    error_message = "A label value is at most 63 characters."
  }
}

run "expiry_timestamp_is_encoded" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2026-12-01T00:00:00Z"
  }

  assert {
    condition     = output.default_labels["expires-at"] == "20261201t0000z"
    error_message = "expires_at renders as yyyymmddthhmmz."
  }
}

run "expiry_seconds_are_dropped" {
  command = plan

  variables {
    expires_at = "2027-01-31T23:59:58Z"
  }

  assert {
    condition     = output.default_labels["expires-at"] == "20270131t2359z"
    error_message = "The encoding keeps minutes and drops seconds."
  }
}

run "expiry_date_only_is_midnight_utc" {
  command = plan

  variables {
    expires_at = "2026-12-01"
  }

  assert {
    condition     = output.default_labels["expires-at"] == "20261201t0000z"
    error_message = "A date without a time is midnight UTC."
  }
}

run "expiry_with_offset_is_refused" {
  command = plan

  variables {
    expires_at = "2026-12-01T02:00:00+02:00"
  }

  expect_failures = [var.expires_at]
}

run "expiry_that_is_not_a_date_is_refused" {
  command = plan

  variables {
    expires_at = "2026-13-40"
  }

  expect_failures = [var.expires_at]
}

run "temporary_without_expiry_is_refused" {
  command = plan

  variables {
    lifecycle_value = "temporary"
  }

  expect_failures = [output.default_labels]
}

run "unknown_managed_by_is_refused" {
  command = plan

  variables {
    managed_by = "by-hand-on-a-tuesday"
  }

  expect_failures = [output.default_labels]
}

run "managed_by_is_checked_after_lowercasing" {
  command = plan

  variables {
    managed_by = "Terraform"
  }

  assert {
    condition     = output.default_labels["managed-by"] == "terraform"
    error_message = "The allowed-value check reads the rendered value."
  }
}

run "unknown_lifecycle_is_refused" {
  command = plan

  variables {
    lifecycle_value = "forever"
  }

  expect_failures = [output.default_labels]
}

run "experiment_without_expiry_is_refused" {
  command = plan

  variables {
    lifecycle_value = "experiment"
  }

  expect_failures = [output.default_labels]
}

run "experiment_with_expiry" {
  command = plan

  variables {
    lifecycle_value = "experiment"
    expires_at      = "2026-12-01T00:00:00Z"
  }

  assert {
    condition     = can(regex(output.contract.patterns.expires_at, output.default_labels["expires-at"]))
    error_message = "The rendered expiry matches the contract's pattern."
  }
}

run "contact_is_not_a_label_key" {
  command = plan

  variables {
    key_names = { contact = "contact" }
  }

  expect_failures = [var.key_names]
}

run "key_names_render_under_other_keys" {
  command = plan

  variables {
    cost_center = "shared"
    key_names = {
      project     = "app_name"
      environment = "env"
      cost_center = "cost_center"
    }
  }

  assert {
    condition = jsonencode(output.default_labels) == jsonencode({
      app_name     = "example_app"
      env          = "dev"
      cost_center  = "shared"
      "managed-by" = "terraform"
      lifecycle    = "persistent"
      owner        = "platform-team"
    })
    error_message = "A renamed fact renders under its new key; the others keep the default key."
  }

  assert {
    condition     = jsonencode(output.required_labels) == jsonencode(["app_name", "env", "managed-by", "lifecycle", "owner"])
    error_message = "required_labels follows key_names."
  }
}

run "key_name_for_an_unknown_fact_is_refused" {
  command = plan

  variables {
    key_names = { repository = "repository" }
  }

  expect_failures = [var.key_names]
}

run "invalid_label_key_is_refused" {
  command = plan

  variables {
    key_names = { project = "Project" }
  }

  expect_failures = [var.key_names]
}

run "label_key_starting_with_a_digit_is_refused" {
  command = plan

  variables {
    key_names = { project = "1project" }
  }

  expect_failures = [var.key_names]
}

run "two_facts_under_one_key_is_refused" {
  command = plan

  variables {
    key_names = { owner = "workload" }
  }

  expect_failures = [output.default_labels]
}

run "required_fact_without_a_value_is_refused" {
  command = plan

  variables {
    owner = null
  }

  expect_failures = [output.default_labels]
}

run "required_fact_with_a_value_passes" {
  command = plan

  variables {
    owner          = "platform"
    required_facts = ["project", "owner"]
  }

  assert {
    condition     = jsonencode(output.required_labels) == jsonencode(["workload", "owner"])
    error_message = "required_facts chooses which facts are required."
  }
}

run "unknown_required_fact_is_refused" {
  command = plan

  variables {
    required_facts = ["project", "repository"]
  }

  expect_failures = [var.required_facts]
}

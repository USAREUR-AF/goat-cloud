# Plan only, mocked provider: no credentials are used and no API is called.

mock_provider "aws" {}

variables {
  project          = "example"
  environment      = "dev"
  repository_url   = "https://git.example/example-org/example-repo"
  use_ssm_contract = false
}

run "defaults" {
  command = plan

  assert {
    condition = output.default_tags == {
      Project     = "example"
      Environment = "dev"
      ManagedBy   = "terraform"
      Lifecycle   = "persistent"
      Repository  = "https://git.example/example-org/example-repo"
    }
    error_message = "The five required tags are emitted."
  }

  assert {
    condition     = output.required_tags == ["Project", "Environment", "ManagedBy", "Lifecycle", "Repository"]
    error_message = "required_tags comes from the built-in contract."
  }
}

run "expiry_in_contract_form" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2030-01-31T00:00:00Z"
  }

  assert {
    condition     = output.default_tags["ExpiresAt"] == "2030-01-31T00:00:00Z"
    error_message = "A UTC timestamp ending in Z is accepted."
  }
}

run "expiry_as_date_is_refused" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2030-01-31"
  }

  expect_failures = [output.default_tags]
}

run "expiry_without_zone_is_refused" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2030-01-31T00:00:00"
  }

  expect_failures = [output.default_tags]
}

run "expiry_in_compact_form_is_refused" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "20300131t0000z"
  }

  expect_failures = [output.default_tags]
}

run "temporary_without_expiry_is_reported" {
  command = plan

  variables {
    lifecycle_value = "temporary"
  }

  expect_failures = [check.lifecycle_requires_expires_at]
}

run "experiment_without_expiry_is_reported" {
  command = plan

  variables {
    lifecycle_value = "experiment"
  }

  expect_failures = [check.lifecycle_requires_expires_at]
}

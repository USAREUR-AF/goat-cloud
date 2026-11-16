# Mocked provider: no credentials are used and no API is called.

mock_provider "aws" {}

variables {
  project        = "example"
  environment    = "dev"
  repo_slug      = "example-repo"
  repo_url       = "https://git.example/example-org/example-repo"
  iac_type       = "Terraform"
  state_location = { type = "s3_tfstate", bucket = "example-state", key = "example-repo/terraform.tfstate" }
}

run "lifecycle_and_expiry_are_carried_when_set" {
  command = apply

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2030-12-01T00:00:00Z"
  }

  assert {
    condition     = jsondecode(aws_ssm_parameter.registration.value).lifecycle == "temporary"
    error_message = "lifecycle is written to the entry."
  }

  assert {
    condition     = jsondecode(aws_ssm_parameter.registration.value).expires_at == "2030-12-01T00:00:00Z"
    error_message = "expires_at is written to the entry."
  }

  assert {
    condition     = jsondecode(aws_ssm_parameter.registration.value).iac_type == "terraform"
    error_message = "iac_type is accepted in any case and written in lowercase."
  }
}

run "experiment_without_expiry_is_refused" {
  command = plan

  variables {
    lifecycle_value = "experiment"
  }

  expect_failures = [aws_ssm_parameter.registration]
}

run "unknown_lifecycle_is_refused" {
  command = plan

  variables {
    lifecycle_value = "forever"
  }

  expect_failures = [aws_ssm_parameter.registration]
}

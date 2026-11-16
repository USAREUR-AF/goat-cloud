# Two applies in one file: the second changes expires_at and the entry follows.
# Mocked provider: no credentials are used and no API is called.

mock_provider "aws" {}

variables {
  project        = "example"
  environment    = "dev"
  repo_slug      = "example-repo"
  repo_url       = "https://git.example/example-org/example-repo"
  iac_type       = "terraform"
  state_location = { type = "s3_tfstate", bucket = "example-state", key = "example-repo/terraform.tfstate" }
}

run "expiry_set" {
  command = apply

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2030-12-01T00:00:00Z"
  }
}

run "changing_expires_at_updates_the_entry" {
  command = apply

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2031-06-01T00:00:00Z"
  }

  assert {
    condition     = jsondecode(aws_ssm_parameter.registration.value).expires_at == "2031-06-01T00:00:00Z"
    error_message = "An extended expires_at reaches the entry on the next apply."
  }
}

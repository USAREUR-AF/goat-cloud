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

run "owner_and_contact_are_carried_when_set" {
  command = apply

  variables {
    owner   = "platform-team"
    contact = "platform-team@example.invalid"
  }

  assert {
    condition     = jsondecode(aws_ssm_parameter.registration.value).owner == "platform-team"
    error_message = "owner is written to the entry."
  }

  assert {
    condition     = jsondecode(aws_ssm_parameter.registration.value).contact == "platform-team@example.invalid"
    error_message = "contact is written to the entry."
  }
}

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

run "entry" {
  command = apply

  assert {
    condition     = aws_ssm_parameter.registration.name == "/example/dev/iac-registry/example-repo"
    error_message = "The entry is the parameter /<project>/<environment>/iac-registry/<repo_slug>."
  }

  assert {
    condition     = jsondecode(aws_ssm_parameter.registration.value).repo_url == "https://git.example/example-org/example-repo"
    error_message = "repo_url is written to the entry."
  }

  assert {
    condition     = !contains(keys(jsondecode(aws_ssm_parameter.registration.value)), "owner") && !contains(keys(jsondecode(aws_ssm_parameter.registration.value)), "contact")
    error_message = "An entry without owner and contact inputs has neither field."
  }
}

run "opentofu_is_an_iac_tool" {
  command = plan

  variables {
    iac_type = "opentofu"
  }
}

run "manual_is_not_an_iac_tool" {
  command = plan

  variables {
    iac_type = "manual"
  }

  expect_failures = [var.iac_type]
}

run "persistent_by_default_with_no_expiry" {
  command = plan

  assert {
    condition     = jsondecode(aws_ssm_parameter.registration.value).lifecycle == "persistent"
    error_message = "lifecycle defaults to persistent and is always written."
  }

  assert {
    condition     = !contains(keys(jsondecode(aws_ssm_parameter.registration.value)), "expires_at")
    error_message = "A persistent entry without expires_at has no expires_at field."
  }
}

run "temporary_without_expiry_fails_the_plan" {
  command = plan

  variables {
    lifecycle_value = "temporary"
  }

  expect_failures = [aws_ssm_parameter.registration]
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

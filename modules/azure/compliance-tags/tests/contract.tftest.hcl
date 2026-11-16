# The module has no provider and no resource: every run is a plan of outputs.

variables {
  project        = "example"
  environment    = "dev"
  repository_url = "https://git.example/example-org/example-repo"
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
    error_message = "The five required tags must be emitted with the same names as on AWS."
  }

  assert {
    condition     = length(setsubtract(output.required_tags, keys(output.default_tags))) == 0
    error_message = "Every required tag name must be present in default_tags."
  }

  assert {
    condition     = output.contract.limits.max_tags_per_resource == 50
    error_message = "The contract output must carry the Azure tag limits."
  }
}

run "temporary_with_expiry" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2030-01-31T00:00:00Z"
  }

  assert {
    condition     = output.default_tags.ExpiresAt == "2030-01-31T00:00:00Z" && output.default_tags.Lifecycle == "temporary"
    error_message = "A temporary lifecycle must carry ExpiresAt."
  }
}

run "temporary_without_expiry_fails" {
  command = plan

  variables {
    lifecycle_value = "temporary"
  }

  expect_failures = [output.default_tags]
}

run "expiry_as_date_fails" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2030-01-31"
  }

  expect_failures = [output.default_tags]
}

run "expiry_without_zone_fails" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2030-01-31T00:00:00"
  }

  expect_failures = [output.default_tags]
}

run "expiry_without_seconds" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2030-01-31T00:00Z"
  }

  assert {
    condition     = output.default_tags["ExpiresAt"] == "2030-01-31T00:00Z"
    error_message = "Seconds are optional in the contract's expiry form."
  }
}

run "expiry_not_a_date_fails" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "next week"
  }

  expect_failures = [output.default_tags]
}

run "experiment_without_expiry_fails" {
  command = plan

  variables {
    lifecycle_value = "experiment"
  }

  expect_failures = [output.default_tags]
}

run "experiment_with_expiry" {
  command = plan

  variables {
    lifecycle_value = "experiment"
    expires_at      = "2030-01-31T00:00:00Z"
  }

  assert {
    condition     = output.default_tags["Lifecycle"] == "experiment"
    error_message = "An experiment with an expiry is accepted."
  }
}

run "opentofu_is_a_managed_by_value" {
  command = plan

  variables {
    managed_by = "opentofu"
  }

  assert {
    condition     = output.default_tags["ManagedBy"] == "opentofu"
    error_message = "opentofu is in the contract's managed_by list."
  }
}

run "optional_contract_tags_pass_through_extra_tags" {
  command = plan

  variables {
    extra_tags = { Owner = "platform-team", Protected = "true", Schedule = "office-hours" }
  }

  assert {
    condition     = output.default_tags["Protected"] == "true" && output.default_tags["Schedule"] == "office-hours"
    error_message = "The contract's optional tags are set through extra_tags."
  }
}

run "unknown_managed_by_fails" {
  command = plan

  variables {
    managed_by = "clickops"
  }

  expect_failures = [output.default_tags]
}

run "unknown_lifecycle_fails" {
  command = plan

  variables {
    lifecycle_value = "forever"
  }

  expect_failures = [output.default_tags]
}

run "repository_not_a_url_fails" {
  command = plan

  variables {
    repository_url = "example-repo"
  }

  expect_failures = [var.repository_url]
}

run "value_over_256_fails" {
  command = plan

  variables {
    project = join("", [for i in range(257) : "a"])
  }

  expect_failures = [var.project]
}

run "extra_tags_merged" {
  command = plan

  variables {
    extra_tags = {
      Owner      = "platform-team"
      CostCenter = "cc-0000"
    }
  }

  assert {
    condition     = length(output.default_tags) == 7 && output.default_tags.Owner == "platform-team"
    error_message = "extra_tags must be added to the required tags."
  }
}

run "extra_tag_same_name_other_case_fails" {
  command = plan

  variables {
    extra_tags = {
      project = "other"
    }
  }

  expect_failures = [output.default_tags]
}

run "extra_tags_differ_only_by_case_fails" {
  command = plan

  variables {
    extra_tags = {
      Owner = "a"
      owner = "b"
    }
  }

  expect_failures = [var.extra_tags]
}

run "extra_tag_forbidden_character_fails" {
  command = plan

  variables {
    extra_tags = {
      "cost/center" = "cc-0000"
    }
  }

  expect_failures = [var.extra_tags]
}

run "extra_tag_backslash_fails" {
  command = plan

  variables {
    extra_tags = {
      "cost\\center" = "cc-0000"
    }
  }

  expect_failures = [var.extra_tags]
}

run "extra_tag_name_over_512_fails" {
  command = plan

  variables {
    extra_tags = {
      (join("", [for i in range(513) : "k"])) = "v"
    }
  }

  expect_failures = [var.extra_tags]
}

run "extra_tag_value_over_256_fails" {
  command = plan

  variables {
    extra_tags = {
      Owner = join("", [for i in range(257) : "v"])
    }
  }

  expect_failures = [var.extra_tags]
}

run "fifty_tags_allowed" {
  command = plan

  variables {
    extra_tags = { for i in range(45) : "Extra${i}" => "v" }
  }

  assert {
    condition     = length(output.default_tags) == 50
    error_message = "Fifty tags is the Azure maximum and must be accepted."
  }
}

run "fifty_one_tags_fails" {
  command = plan

  variables {
    extra_tags = { for i in range(46) : "Extra${i}" => "v" }
  }

  expect_failures = [output.default_tags]
}

run "managed_by_is_checked_without_regard_to_case" {
  command = plan

  variables {
    managed_by = "Terraform"
  }

  assert {
    condition     = output.default_tags["ManagedBy"] == "Terraform"
    error_message = "A managed_by value is compared with the contract's list in lowercase."
  }
}

run "ansible_and_helm_are_managed_by_values" {
  command = plan

  variables {
    managed_by = "helm"
  }

  assert {
    condition     = output.default_tags["ManagedBy"] == "helm"
    error_message = "helm is in the contract's managed_by list."
  }
}

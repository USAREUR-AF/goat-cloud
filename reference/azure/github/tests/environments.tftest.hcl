# No token and no GitHub calls: the github provider is mocked.
mock_provider "github" {}

variables {
  github_org                 = "example-org"
  github_repo                = "example-repo"
  teardown_reviewer_user_ids = [1234567]
}

run "both_environments_allow_main_only" {
  command = plan

  assert {
    condition     = github_repository_environment_deployment_policy.branch["deploy"].branch_pattern == "main" && github_repository_environment_deployment_policy.branch["teardown"].branch_pattern == "main"
    error_message = "deploy and teardown accept jobs from main only."
  }

  assert {
    condition     = github_repository_environment.deploy.deployment_branch_policy[0].custom_branch_policies && github_repository_environment.teardown.deployment_branch_policy[0].custom_branch_policies
    error_message = "Both environments use a branch name policy."
  }

  assert {
    condition     = !github_repository_environment.deploy.can_admins_bypass && !github_repository_environment.teardown.can_admins_bypass
    error_message = "Administrators cannot bypass the rules."
  }

  assert {
    condition     = github_repository_environment.teardown.prevent_self_review && contains(github_repository_environment.teardown.reviewers[0].users, 1234567)
    error_message = "A teardown needs a reviewer other than the person who started it."
  }
}

run "teardown_without_a_reviewer_is_refused" {
  command = plan

  variables {
    teardown_reviewer_user_ids = []
  }

  expect_failures = [github_repository_environment.teardown]
}

# Optional. The two GitHub environments the CI identity of ../bootstrap trusts,
# with the protection that makes that trust safe: jobs from the branch main
# only, and a reviewer before every teardown. Microsoft Entra ID issues a token
# to any job that names the environment, so without these rules a job on any
# branch could sign in. The same settings can be made by hand (README, B1).

resource "github_repository_environment" "deploy" {
  repository        = var.github_repo
  environment       = "deploy"
  can_admins_bypass = false

  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment" "teardown" {
  repository          = var.github_repo
  environment         = "teardown"
  can_admins_bypass   = false
  prevent_self_review = true

  reviewers {
    users = var.teardown_reviewer_user_ids
    teams = var.teardown_reviewer_team_ids
  }

  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }

  lifecycle {
    precondition {
      condition     = length(var.teardown_reviewer_user_ids) + length(var.teardown_reviewer_team_ids) >= 1 && length(var.teardown_reviewer_user_ids) + length(var.teardown_reviewer_team_ids) <= 6
      error_message = "The teardown environment needs one to six reviewers, users and teams together."
    }
  }
}

resource "github_repository_environment_deployment_policy" "branch" {
  for_each = {
    deploy   = github_repository_environment.deploy.environment
    teardown = github_repository_environment.teardown.environment
  }

  repository     = var.github_repo
  environment    = each.value
  branch_pattern = var.deploy_branch
}

output "environments" {
  description = "The environments this root manages, each limited to deploy_branch"
  value = {
    for name, policy in github_repository_environment_deployment_policy.branch : name => policy.branch_pattern
  }
}

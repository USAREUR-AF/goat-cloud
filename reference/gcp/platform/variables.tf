variable "project_id" {
  description = "The existing project"
  type        = string
}

variable "slug" {
  description = "Short name of the repository; prefixes the pool id and the custom role ids"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}[a-z0-9]$", var.slug))
    error_message = "slug must be 3 to 32 lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "stack_state_bucket" {
  description = "Name of the stack's state bucket, the output stack_state_bucket of ../bootstrap. The deploy identity gets object access to it and the scanner object read. The owner's state bucket, which holds this root's state, is not an input: nothing here grants on it"
  type        = string
}

variable "apis" {
  description = "APIs this root and the stack need (rule 7). Each is enabled when it is off and is never disabled. The stack's APIs are listed here so that the deploy identity needs no Service Usage role"
  type        = list(string)
  default = [
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "storage.googleapis.com",
    "cloudasset.googleapis.com",
    "pubsub.googleapis.com",
  ]
}

variable "github_org_id" {
  description = "Numeric id of the GitHub organization whose workflows may sign in (rule 6). Tokens from any other owner are refused"
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_org_id))
    error_message = "github_org_id is the numeric id, not the organization name."
  }
}

variable "github_repo_id" {
  description = "Numeric id of the GitHub repository that receives the deploy roles (rule 6)"
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repo_id))
    error_message = "github_repo_id is the numeric id, not the repository name."
  }
}

variable "github_repository" {
  description = "<owner>/<repo> of the repository, exactly as GitHub writes it in the token's workflow_ref claim (the same case). The repository it names must have the id github_repo_id"
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "github_repository is <owner>/<repo>."
  }
}

variable "github_workflows" {
  description = "Workflow files, under .github/workflows/, whose runs on github_deploy_ref receive the deploy grants. No other workflow of the repository can sign in"
  type        = set(string)
  default     = ["deploy.yml", "teardown.yml"]

  validation {
    condition     = length(var.github_workflows) > 0 && alltrue([for file in var.github_workflows : can(regex("^[A-Za-z0-9._-]+\\.ya?ml$", file))])
    error_message = "github_workflows holds file names such as deploy.yml, with no directory."
  }
}

variable "github_deploy_ref" {
  description = "The git ref whose workflow runs receive the deploy grants (rule 6). Runs on any other ref, pull requests included, receive none"
  type        = string
  default     = "refs/heads/main"

  validation {
    condition     = can(regex("^refs/(heads|tags)/[A-Za-z0-9._/-]+$", var.github_deploy_ref))
    error_message = "github_deploy_ref is a full ref such as refs/heads/main."
  }
}

variable "deploy_roles" {
  description = "Project roles granted to the deploy workflows, besides the bucket grants. Each must be on the allowed list below, which names the roles the stack needs: pubsub.editor for the example topic, which also grants resourcemanager.projects.get for the stack's project read. A stack that uses another service adds that service's role to the list in a reviewed change"
  type        = set(string)
  default     = ["roles/pubsub.editor"]

  validation {
    condition     = length(var.deploy_roles) > 0 && alltrue([for role in var.deploy_roles : contains(["roles/pubsub.editor"], role)])
    error_message = "deploy_roles may hold only the roles on the allowed list in this variable's validation: roles/pubsub.editor."
  }
}

variable "stack_bucket_prefix" {
  description = "Name prefix of the buckets the stack creates and manages in this project. Null uses <slug>_; a value given must also start with <slug>_, so that no other repository's buckets and no state bucket match it"
  type        = string
  default     = null

  validation {
    condition     = var.stack_bucket_prefix == null || can(regex("^[a-z0-9][a-z0-9_.-]{2,}$", var.stack_bucket_prefix))
    error_message = "stack_bucket_prefix is at least three lowercase letters, digits, dashes, underscores or dots."
  }
}

variable "scanner_account_id" {
  description = "Account id of the read-only service account a scanner runs as (P4)"
  type        = string
  default     = "goat-cloud-scanner"
}

variable "tag_mode" {
  description = "`none` creates no tags. `project-keys` creates the tag keys lifecycle, environment and protected, and their values, under the project (rule 3)"
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "project-keys"], var.tag_mode)
    error_message = "tag_mode must be none or project-keys."
  }
}

variable "bind_project_tags" {
  description = "With tag_mode = project-keys, also bind the project to lifecycle and environment values. Turn off to create keys and values only"
  type        = bool
  default     = true
}

variable "project_lifecycle" {
  description = "Tag value bound to the project for lifecycle"
  type        = string
  default     = "persistent"
}

variable "project_environment" {
  description = "Tag value bound to the project for environment"
  type        = string
  default     = "dev"
}

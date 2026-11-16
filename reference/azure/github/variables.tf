variable "github_org" {
  description = "GitHub organization or user that owns the repository; the same value as in ../bootstrap"
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name; the same value as in ../bootstrap"
  type        = string
}

variable "deploy_branch" {
  description = "The only branch whose jobs may use the environments deploy and teardown"
  type        = string
  default     = "main"
}

variable "teardown_reviewer_user_ids" {
  description = "Numeric ids of the users who may approve a teardown job (up to six users and teams together)"
  type        = list(number)
  default     = []
}

variable "teardown_reviewer_team_ids" {
  description = "Numeric ids of the teams who may approve a teardown job (up to six users and teams together)"
  type        = list(number)
  default     = []
}

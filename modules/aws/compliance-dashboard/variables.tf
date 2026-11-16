# =============================================================================
# Compliance Dashboard — Variables
# =============================================================================

variable "project" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

# ALB integration
variable "enable_alb" {
  description = "Serve the dashboard through an Application Load Balancer. Set to false when it is reached only through the Function URL (enable_function_url); no load balancer resource is then created and the other ALB inputs are not needed."
  type        = bool
  default     = true
}

variable "alb_listener_arn" {
  description = "ARN of the ALB HTTPS listener to attach the rules to. Required when enable_alb is true."
  type        = string
  default     = null
}

variable "listener_rule_priority" {
  description = "Priority for the ALB public listener rule (only used when create_public_listener_rule is true)"
  type        = number
  default     = 100
}

variable "create_public_listener_rule" {
  description = "Create the public ALB listener rule for the dashboard host. Set to false when a sign-in proxy owns the public rule and forwards to this dashboard through the bypass rule instead."
  type        = bool
  default     = true
}

variable "host_header" {
  description = "Host name the ALB listener rules match (e.g., compliance.example.com). Required when enable_alb is true."
  type        = string
  default     = ""
}

variable "alb_oidc_config" {
  description = "OIDC provider settings for the ALB authenticate-oidc action. When null, the public listener rule forwards without authentication."
  type = object({
    authorization_endpoint = string
    client_id              = string
    client_secret          = string
    issuer                 = string
    token_endpoint         = string
    user_info_endpoint     = string
    scope                  = string
    session_timeout        = number
  })
  default = null
}

# Data sources
variable "compliance_reports_bucket" {
  description = "S3 bucket name where compliance scanner writes reports"
  type        = string
}

variable "hunterkiller_state_table_name" {
  description = "DynamoDB table name for hunterkiller enforcement state"
  type        = string
}

variable "hunterkiller_state_table_arn" {
  description = "DynamoDB table ARN for hunterkiller enforcement state"
  type        = string
}

variable "scanner_function_name" {
  description = "Lambda function name of the compliance scanner"
  type        = string
  default     = ""
}

variable "scanner_function_arn" {
  description = "Lambda function ARN of the compliance scanner"
  type        = string
  default     = ""
}

variable "hunterkiller_function_name" {
  description = "Lambda function name of the hunterkiller notifier"
  type        = string
}

variable "hunterkiller_function_arn" {
  description = "Lambda function ARN of the hunterkiller notifier"
  type        = string
}

variable "hunterkiller_executor_function_name" {
  description = "Lambda function name of the hunterkiller executor (empty when executor_armed=false)"
  type        = string
  default     = ""
}

variable "hunterkiller_executor_function_arn" {
  description = "Lambda function ARN of the hunterkiller executor (empty when executor_armed=false)"
  type        = string
  default     = ""
}

variable "hunterkiller_executor_armed" {
  description = "Whether the hunterkiller executor is deployed"
  type        = bool
  default     = false
}

# Compliance report data (DynamoDB — scanner writes, dashboard reads)
variable "compliance_report_table_name" {
  description = "DynamoDB table name for compliance report data (written by scanner)"
  type        = string
  default     = ""
}

variable "compliance_report_table_arn" {
  description = "DynamoDB table ARN for compliance report data (written by scanner)"
  type        = string
  default     = ""
}

# Terraform state (for CURE page — maps resources to TF config)
variable "tf_state_bucket" {
  description = "S3 bucket that holds the Terraform state of the root that deploys the dashboard"
  type        = string
  default     = ""
}

variable "tf_state_key" {
  description = "S3 key of that state file"
  type        = string
  default     = ""
}

# Auth / admin
variable "admin_emails" {
  description = "Email addresses authorized for destructive actions (abort, destroy, extend)"
  type        = list(string)
}

variable "ses_sender_email" {
  description = "SES sender email for action confirmation emails"
  type        = string
  default     = ""
}

variable "dashboard_url" {
  description = "Public URL of the dashboard (for self-referencing links)"
  type        = string
  default     = ""
}

variable "image_versions_ssm_name" {
  description = "Optional SSM parameter holding a JSON object that annotates the container images on the security page: one key per image, each with ecr_repo, upstream, upstream_tag and ecr_digest. Empty shows the ECR repositories from Terraform state without annotations."
  type        = string
  default     = ""
}

variable "cli_prefix" {
  description = "CLI prefix for remediation commands. Set it when a credential helper must precede `aws`."
  type        = string
  default     = "aws"
}

variable "repository_url" {
  description = "URL of the repository that deploys this module. Used for source links and to recognize that repository's own entry in the IaC registry."
  type        = string
}

variable "site_config_dir" {
  description = "Directory with the deployment's own dashboard content: site.json (service names, connections and security annotations for the architecture and security pages) and overview.html (body of the overview page). Copied into the Lambda package. Empty (the default) shows only what is discovered from Terraform state."
  type        = string
  default     = ""
}

variable "module_file_map" {
  description = "Map of top-level Terraform module address to the file that declares it (e.g. { \"module.alb\" = \"live/prod/main.tf\" }), used for source links on resource pages."
  type        = map(string)
  default     = {}
}

variable "domain_name" {
  description = "Root domain name (e.g., example.com) for architecture diagram hostnames"
  type        = string
}

variable "region" {
  description = "AWS region the deployment's resources live in, for console URLs and generated commands"
  type        = string
}

variable "sprawl_test_function_arn" {
  description = "ARN of an optional Lambda function you supply that creates non-compliant test resources, so that enforcement can be exercised; used only for the invoke permission. Empty when there is none."
  type        = string
  default     = ""
}

variable "oauth2_proxy_bypass_header_name" {
  description = "Name of the static header that a sign-in proxy in front of this dashboard adds to upstream requests. Pair with oauth2_proxy_bypass_header_value to enable the bypass rule."
  type        = string
  default     = "X-OAuth2-Proxy-Auth"
}

variable "oauth2_proxy_bypass_header_value" {
  description = "Exact value of the bypass header that the sign-in proxy adds to upstream requests. When non-null, an ALB bypass rule forwards matching requests to the Lambda target group without auth, so the proxy can call back into the same hostname."
  type        = string
  default     = null
  sensitive   = true
}

variable "oauth2_proxy_bypass_priority" {
  description = "Priority of the sign-in proxy bypass listener rule. Must be a lower number (higher priority) than the public listener rule that routes traffic to the proxy."
  type        = number
  default     = 128
}

variable "allow_unauthenticated_public_rule" {
  description = "Accept a public listener rule that forwards to the dashboard with no sign-in (create_public_listener_rule = true and alb_oidc_config = null). Without a sign-in front nobody has an identity, so the dashboard is read-only and readable by every caller of the listener."
  type        = bool
  default     = false
}

variable "trusted_origins" {
  description = "Additional origins (scheme://host) whose pages may submit dashboard actions, for a front that serves the dashboard under a host name the function cannot see in the Host or X-Forwarded-Host header. The request's own host and dashboard_url are always accepted."
  type        = list(string)
  default     = []
}

# Viewer gate — restrict dashboard viewing to authorized emails
variable "enable_viewer_gate" {
  description = "Restrict dashboard viewing to explicitly-authorized emails (static list + invite enrollment)"
  type        = bool
  default     = false
}

variable "viewer_emails" {
  description = "Static email addresses authorized to view the dashboard (in addition to admin_emails). Only checked when enable_viewer_gate=true."
  type        = list(string)
  default     = []
}

variable "provisioned_concurrency" {
  description = "Number of provisioned concurrent executions (0 to disable)"
  type        = number
  default     = 0
}

variable "protected_resources_ssm_name" {
  description = "SSM Parameter Store name containing protected resources JSON. Lambda reads at runtime."
  type        = string
  default     = ""
}

variable "required_tags" {
  description = "List of tag keys that all resources should have. Null uses the required tags of the contract."
  type        = list(string)
  default     = null
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}

# =============================================================================
# Function URL — default off
# =============================================================================
# Default off. A deployment that has an ALB can add the Function URL beside
# it; a deployment without one sets enable_function_url = true and
# enable_alb = false.

variable "enable_function_url" {
  description = "Expose the dashboard Lambda through a Function URL. Independent of the ALB: it can be used beside it or, with enable_alb = false, without one."
  type        = bool
  default     = false
}

variable "function_url_authorization_type" {
  description = "Function URL auth: AWS_IAM (only SigV4-signed callers, e.g. a fronting proxy) or NONE (the app's own auth carries the gate, e.g. behind an access proxy)."
  type        = string
  default     = "AWS_IAM"

  validation {
    condition     = contains(["AWS_IAM", "NONE"], var.function_url_authorization_type)
    error_message = "function_url_authorization_type must be AWS_IAM or NONE."
  }
}

variable "function_url_invoke_principal_arns" {
  description = "IAM principal ARNs granted lambda:InvokeFunctionUrl when function_url_authorization_type = AWS_IAM — for example the identity of each proxy that fronts this service, so that access can be granted and revoked per proxy. Empty grants no resource-based permission (callers rely on their own identity policy). Invoke permission gives a caller no identity: see function_url_identity_proxy_principal_arns."
  type        = list(string)
  default     = []
}

variable "function_url_identity_proxy_principal_arns" {
  description = "IAM principal ARNs whose Function URL requests may assert the user's identity in X-Forwarded-Email (function_url_authorization_type = AWS_IAM only). Name only a front that signs the user in itself and overwrites any X-Forwarded-Email the client sent. Permission to invoke the URL (function_url_invoke_principal_arns) gives no identity: a request from any other caller is anonymous, so it can read pages when the viewer gate is off and can perform no admin action. Empty: no Function URL caller asserts an identity."
  type        = list(string)
  default     = []
}

variable "scan_stale_hours" {
  description = "Hours since the last complete scan after which the overview shows the compliance status as STALE instead of a grade. Match the scanner module's scan_stale_alarm_hours."
  type        = number
  default     = 26
}

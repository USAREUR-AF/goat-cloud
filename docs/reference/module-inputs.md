# Module inputs

Every module is a Terraform module under `modules/aws/`. Use it with a source of the form `git::https://github.com/USAREUR-AF/goat-cloud.git//modules/aws/<module>?ref=<tag>`. Each takes `project` and `environment`, which together prefix the names of what it creates.

The tables are written from each module's `variables.tf` and `outputs.tf`.

## Tag contract: `compliance-tags`

Produces the required tags as one map. It creates no resource; with `use_ssm_contract = true` it reads the SSM parameter `/<project>/<environment>/compliance-contract`.

| Input | Type | Default | Description |
|---|---|---|---|
| `project` | `string` | required | Project name (must match the compliance contract project) |
| `environment` | `string` | required | Environment name (dev, staging, production) |
| `repository_url` | `string` | required | Git repository URL (https:// or git@) |
| `lifecycle_value` | `string` | `"persistent"` | Lifecycle tag value (persistent, temporary, experiment, deprecated) |
| `managed_by` | `string` | `"terraform"` | ManagedBy tag value |
| `expires_at` | `string` | `null` | Expiry as a UTC timestamp ending in Z (2030-01-31T00:00:00Z). Required when lifecycle_value is temporary or experiment |
| `use_ssm_contract` | `bool` | `true` | Whether to load the compliance contract from SSM. Set to false if the contract SSM parameter hasn't been deployed yet. |

| Output | Description |
|---|---|
| `required_tags` | List of required tag keys from the compliance contract |
| `default_tags` | Complete set of required tags ready for provider.default_tags |
| `contract` | Full parsed compliance contract |

## Registry: `iac-registry`

Writes one SSM parameter, `/<project>/<environment>/iac-registry/<repo_slug>`, holding the registry entry.

| Input | Type | Default | Description |
|---|---|---|---|
| `iac_type` | `string` | required | IaC tool type |
| `project` | `string` | required | Project name |
| `environment` | `string` | required | Environment name |
| `repo_slug` | `string` | required | Short name of the repository; the last segment of the SSM parameter path (e.g. 'example-repo') |
| `repo_url` | `string` | required | Git repository URL, as https://host/path or git@host:path |
| `state_location` | `any` | required | Where the repository's IaC state lives; the shape depends on iac_type |
| `deployer` | `string` | `"github-actions"` | Who deploys this repo (e.g. github-actions, manual) |
| `tags` | `map(string)` | `{}` | Tags to apply to the SSM parameter |
| `owner` | `string` | `null` | Optional: the team or role accountable for what this repository deploys. A team or role name, never a person |
| `contact` | `string` | `null` | Optional: where to reach the owner, as a team address or a channel, never a person's own address. Used for notices about resources of this repository that carry no contact of their own |
| `lifecycle_value` | `string` | `"persistent"` | The lifecycle of what this repository deploys, one of the contract's lifecycle values. Written to the entry as `lifecycle`; the expiry teardown decides from it and from expires_at |
| `expires_at` | `string` | `null` | When what this repository deploys expires, in the form of the contract's expiry on this platform. Written to the entry when set; required when lifecycle_value is temporary or experiment (rules temporary-requires-expires-at, experiment-requires-expires-at) |

| Output | Description |
|---|---|
| `ssm_parameter_name` | The SSM parameter name for this registry entry |
| `ssm_parameter_arn` | The SSM parameter ARN for this registry entry |

## Scanner: `compliance-scanner`

Creates the scanner Lambda `<project>-<environment>-compliance-scanner` with the AWS managed `ReadOnlyAccess` policy plus write access to its own report bucket and index table, the bucket `<project>-<environment>-compliance-reports`, and the schedule.

| Input | Type | Default | Description |
|---|---|---|---|
| `project` | `string` | required | Project name |
| `environment` | `string` | required | Environment name |
| `tf_state_bucket` | `string` | required | S3 bucket that holds the Terraform state of the root that deploys the scanner (for drift detection) |
| `tf_state_key` | `string` | required | S3 key of that state file |
| `repository_url` | `string` | required | URL of the repository that deploys this module. Used for report links and to recognize that repository's own entry in the IaC registry. |
| `required_tags` | `list(string)` | `null` | List of tag keys that all resources should have. Null uses the required tags of the contract. |
| `exclusion_arns` | `list(string)` | `[]` | Specific ARNs to exclude from drift detection (bootstrap resources, TF state bucket, etc.) |
| `exclusion_patterns` | `list(string)` | `[]` | Regex patterns for ARNs to exclude from drift detection |
| `schedule_expression` | `string` | `"cron(0 3 ? * * *)"` | EventBridge schedule expression for the scan |
| `report_retention_days` | `number` | `90` | Days to retain compliance reports in S3 |
| `ses_sender_email` | `string` | `""` | Verified SES sender address for HTML compliance emails (e.g. compliance@example.com). Leave empty to skip SES. |
| `ses_recipient_emails` | `list(string)` | `[]` | Email addresses to receive the HTML compliance report via SES |
| `hunterkiller_function_name` | `string` | `""` | Name of the hunterkiller Lambda function. When set, the scanner invokes it in warn mode after each scan. |
| `overrides_table_name` | `string` | `""` | DynamoDB table name for compliance dashboard overrides (false positives / exemptions). When set, the scanner excludes overridden resources from findings. |
| `overrides_table_arn` | `string` | `""` | DynamoDB table ARN for compliance dashboard overrides. Required for IAM permissions when overrides_table_name is set. |
| `cli_prefix` | `string` | `"aws"` | CLI prefix for teardown commands in reports. Set it when a credential helper must precede `aws`. |
| `scanner_source_url` | `string` | `""` | URL of the scanner's source, linked from the footer of each report. Empty omits the link. |
| `standards_url` | `string` | `""` | URL of the document that states the deployment's compliance standards, linked from the footer of each report. Empty omits the link. |
| `purpose_hints` | `map(string)` | `{}` | Map of resource-name fragment to purpose text, used to describe resources that carry no descriptive tag (e.g. { wiki = "Team wiki" }). |
| `protected_resources_ssm_name` | `string` | `""` | SSM Parameter Store name containing protected resources JSON. Lambda reads at runtime. |
| `iac_registry_prefix` | `string` | `""` | SSM parameter path prefix for IaC registry entries. Defaults to /{project}/{environment}/iac-registry. |
| `iac_registry_state_buckets` | `list(string)` | `[]` | S3 bucket names that hold cross-repo TF state for IaC registry entries. The scanner Lambda gets s3:GetObject on these so it can read other repos' state for drift detection. |
| `tags` | `map(string)` | `{}` | Additional tags |
| `manage_reports_bucket_policy` | `bool` | `true` | Attach a bucket policy to the reports bucket that refuses requests not made over TLS. Set to false when the bucket's policy is managed elsewhere (a bucket has one policy), and include the same statement there. |
| `alarm_sns_topic_arn` | `string` | `""` | SNS topic that receives this module's own failure alarms (function error, failed scan, no successful scan). Empty (the default) creates no alarm: a function that stops or fails is then not reported by this module. The topic's policy must allow cloudwatch.amazonaws.com to publish. |
| `scan_stale_alarm_hours` | `number` | `26` | Hours without a successful scan after which the no-successful-scan alarm fires. The default allows a daily scan to run two hours late. |

| Output | Description |
|---|---|
| `lambda_function_arn` | ARN of the compliance scanner Lambda function |
| `lambda_function_name` | Name of the compliance scanner Lambda function |
| `reports_bucket` | Name of the S3 bucket for compliance reports |
| `reports_bucket_arn` | ARN of the S3 bucket for compliance reports |
| `eventbridge_rule_arn` | ARN of the nightly compliance scan EventBridge rule |
| `report_data_table_name` | Name of the DynamoDB table for compliance report data |
| `report_data_table_arn` | ARN of the DynamoDB table for compliance report data |

## Enforcement notifier and executor: `hunterkiller`

Creates the notifier Lambda `<project>-<environment>-hunterkiller`, the state table `<project>-<environment>-hunterkiller-state`, and, while `executor_armed = true`, the executor Lambda `<project>-<environment>-hunterkiller-executor` with its role.

| Input | Type | Default | Description |
|---|---|---|---|
| `project` | `string` | required | Project name |
| `environment` | `string` | required | Environment name |
| `compliance_reports_bucket` | `string` | required | S3 bucket where the compliance scanner stores JSON reports |
| `compliance_scanner_function_name` | `string` | required | Name of the compliance scanner Lambda function (for invoke permissions) |
| `executor_armed` | `bool` | `false` | When true, creates the executor Lambda with destructive IAM permissions. Default false — no destructive infrastructure exists. |
| `dry_run` | `bool` | `true` | When true, the hunterkiller logs teardown actions but does not execute them. Safety net. |
| `unrecoverable_teardown` | `bool` | `false` | Remove data-holding resources with no recovery point. Default false: an RDS instance is deleted with a final snapshot and its automated backups retained, an EBS volume (and the volumes an instance termination deletes) is snapshotted first, a single-node Redis or Valkey cache cluster takes a final snapshot, a secret gets a 30-day recovery window, a KMS key a 30-day pending window, and a bucket or image repository that still holds data is refused. true selects the plain delete for all of these and grants the role s3:DeleteObject. |
| `grace_period_hours` | `number` | `24` | Hours between the warning and the enforcement action |
| `ses_sender_email` | `string` | `""` | Verified SES sender address for warning and teardown notification emails |
| `ses_admin_emails` | `list(string)` | `[]` | Admin email addresses: the fallback contacts when a resource names no owner |
| `protected_environments` | `list(string)` | `null` | Environment tag values that are exempt from teardown. Null uses the contract's protected environment names (prod, production) |
| `protected_tag_key` | `string` | `null` | Tag key that marks a resource as exempt from teardown. Null uses the contract's key for the protected fact (Protected) |
| `contact_tag_keys` | `list(string)` | `["Contact"]` | Tag keys to check for direct contact email (checked in order, first non-empty wins). The default reads the contract's Contact tag only; add keys such as OwnerEmail to read others. |
| `dashboard_url` | `string` | `""` | URL of the compliance dashboard, linked from warning emails so an owner can object before enforcement |
| `state_ttl_days` | `number` | `30` | Days to retain DynamoDB notification records before TTL cleanup |
| `protected_resource_types` | `list(string)` | `["route53:hostedzone", "payments:payment-instrument", "s3:bucket"]` | Resource types that are always protected from teardown. Require manual action to delete. |
| `protected_resources_ssm_name` | `string` | `""` | SSM Parameter Store name containing protected resources JSON. Executor reads at runtime. |
| `auto_approve_expired` | `bool` | `false` | Approve teardown automatically for resources whose ExpiresAt is in the past. For non-production environments only. |
| `bedrock_summary_model_id` | `string` | `""` | Bedrock model or inference-profile id for an LLM-written summary at the top of enforcement emails. Empty (the default) turns the summary off and grants no Bedrock permission. |
| `bedrock_summary_region` | `string` | `""` | Region of the Bedrock runtime endpoint to call. Empty uses the function's own region. |
| `tags` | `map(string)` | `{}` | Additional tags |
| `contact_allowed_email_domains` | `list(string)` | `[]` | Mail domains a contact address taken from a resource tag (or an IAM user's Email tag) may be in. A warning for a resource whose contact is outside these domains goes to the admins instead. Empty accepts any domain: anyone who can tag a resource then chooses where its warning email is sent. |
| `alarm_sns_topic_arn` | `string` | `""` | SNS topic that receives this module's own failure alarms. Empty (the default) creates no alarm: a function that stops or fails is then not reported by this module. The topic's policy must allow cloudwatch.amazonaws.com to publish. |
| `destroy_now_report_max_age_hours` | `number` | `36` | "Destroy now" acts only on a resource with a HIGH finding in the newest compliance report generated no more than this many hours ago. |

| Output | Description |
|---|---|
| `notifier_function_arn` | ARN of the hunterkiller notifier Lambda function |
| `notifier_function_name` | Name of the hunterkiller notifier Lambda function |
| `executor_function_arn` | ARN of the hunterkiller executor Lambda function (empty when executor_armed=false) |
| `executor_function_name` | Name of the hunterkiller executor Lambda function (empty when executor_armed=false) |
| `executor_armed` | Whether the executor Lambda is deployed |
| `state_table_name` | Name of the DynamoDB state tracking table |
| `state_table_arn` | ARN of the DynamoDB state tracking table |
| `lambda_function_arn` | ARN of the hunterkiller Lambda function (alias for notifier_function_arn) |
| `lambda_function_name` | Name of the hunterkiller Lambda function (alias for notifier_function_name) |

## Expiry teardown: `stack-reaper`

Creates the reconciler Lambda `<project>-<environment>-stack-reaper-reconciler`, the state table `<project>-<environment>-stack-reaper-state`, and, while `armed = true`, the dispatcher Lambda `<project>-<environment>-stack-reaper`.

| Input | Type | Default | Description |
|---|---|---|---|
| `project` | `string` | required | Project name |
| `environment` | `string` | required | Environment name |
| `iac_registry_prefix` | `string` | required | SSM Parameter Store prefix for IaC registry entries |
| `repository_url` | `string` | required | URL of the repository that deploys this module. Its own IaC registry entry is never scheduled for teardown. |
| `armed` | `bool` | `false` | When true, creates the reaper Lambda with teardown dispatch permissions. Default false — only the reconciler exists. |
| `dry_run` | `bool` | `true` | When true, the reaper logs teardown actions but does not execute them. Safety net. |
| `reconciler_schedule` | `string` | `"rate(6 hours)"` | EventBridge schedule expression for the reconciler (e.g. rate(6 hours)) |
| `github_app_secret_arn` | `string` | `""` | Secrets Manager ARN for GitHub App credentials (app_id + private_key). Required for workflow_dispatch teardowns. |
| `github_org` | `string` | `""` | GitHub org to dispatch teardown workflows against. Overrides the org parsed from IaC registry repo_url. |
| `ses_sender_email` | `string` | `""` | Verified SES sender address for teardown notification emails |
| `ses_admin_emails` | `list(string)` | `[]` | Admin email addresses for teardown notifications |
| `state_ttl_days` | `number` | `90` | Days to retain DynamoDB state records before TTL cleanup |
| `tags` | `map(string)` | `{}` | Additional tags |
| `alarm_sns_topic_arn` | `string` | `""` | SNS topic that receives this module's own failure alarms. Empty (the default) creates no alarm: a function that stops or fails is then not reported by this module. The topic's policy must allow cloudwatch.amazonaws.com to publish. |

| Output | Description |
|---|---|
| `reconciler_function_name` | Name of the reconciler Lambda function |
| `reconciler_function_arn` | ARN of the reconciler Lambda function |
| `reaper_function_name` | Name of the reaper Lambda function (empty when not armed) |
| `reaper_function_arn` | ARN of the reaper Lambda function (empty when not armed) |
| `state_table_name` | Name of the DynamoDB state table |
| `state_table_arn` | ARN of the DynamoDB state table |

## Budget and stale-resource alerts: `governance`

Creates a monthly AWS budget with alerts at each threshold, one SNS topic with `owner_email` subscribed, and a weekly Lambda that reports non-persistent resources near or past their expiry.

| Input | Type | Default | Description |
|---|---|---|---|
| `project` | `string` | required | Project name |
| `environment` | `string` | required | Environment name |
| `owner_email` | `string` | required | Infrastructure owner email for alerts |
| `monthly_budget_limit` | `number` | `200` | Monthly budget limit in USD |
| `lifecycle_warning_days` | `number` | `30` | Days before expiry to start sending warnings |
| `lifecycle_termination_days` | `number` | `60` | Days after creation at which a temporary resource is reported as expired |
| `enable_auto_termination` | `bool` | `false` | Removed. This module reports and alerts and removes nothing. Kept so an existing call still parses; true fails the plan. |
| `cost_alert_thresholds` | `list(number)` | `[50, 80, 100, 120]` | Budget thresholds for cost alerts (percentages) |
| `tags` | `map(string)` | `{}` | Additional tags |
| `alarm_sns_topic_arn` | `string` | `""` | SNS topic that receives this module's own failure alarms. Empty (the default) creates no alarm: a function that stops or fails is then not reported by this module. The topic's policy must allow cloudwatch.amazonaws.com to publish. |

| Output | Description |
|---|---|
| `sns_topic_arn` | ARN of the governance alerts SNS topic |
| `sns_topic_name` | Name of the governance alerts SNS topic |
| `lambda_function_arn` | ARN of the lifecycle manager Lambda function |
| `lambda_function_name` | Name of the lifecycle manager Lambda function |
| `eventbridge_rule_arn` | ARN of the weekly governance EventBridge rule |
| `budget_name` | Name of the monthly budget |
| `governance_tags` | Standard governance tags to apply to all resources |

## Dashboard: `compliance-dashboard`

Creates the dashboard Lambda and the overrides table. With `enable_alb = true` (the default) it also creates a target group and listener rules on an existing HTTPS Application Load Balancer listener; the load balancer and its certificate are created outside this module. With `enable_alb = false` and `enable_function_url = true` it is reached through a Lambda function URL and needs no load balancer.

The dashboard names no deployment's services. To have a deployment's own service names, connections, sign-in classification, fixed costs, product name and overview page shown, pass a directory as `site_config_dir`; it holds `site.json` (keys and defaults are listed in `modules/aws/compliance-dashboard/lambda/site_config.py`) and, optionally, `overview.html`.

| Input | Type | Default | Description |
|---|---|---|---|
| `project` | `string` | required | Project name |
| `environment` | `string` | required | Environment name |
| `compliance_reports_bucket` | `string` | required | S3 bucket name where compliance scanner writes reports |
| `hunterkiller_state_table_name` | `string` | required | DynamoDB table name for hunterkiller enforcement state |
| `hunterkiller_state_table_arn` | `string` | required | DynamoDB table ARN for hunterkiller enforcement state |
| `hunterkiller_function_name` | `string` | required | Lambda function name of the hunterkiller notifier |
| `hunterkiller_function_arn` | `string` | required | Lambda function ARN of the hunterkiller notifier |
| `admin_emails` | `list(string)` | required | Email addresses authorized for destructive actions (abort, destroy, extend) |
| `repository_url` | `string` | required | URL of the repository that deploys this module. Used for source links and to recognize that repository's own entry in the IaC registry. |
| `domain_name` | `string` | required | Root domain name (e.g., example.com) for architecture diagram hostnames |
| `region` | `string` | required | AWS region the deployment's resources live in, for console URLs and generated commands |
| `enable_alb` | `bool` | `true` | Serve the dashboard through an Application Load Balancer. Set to false when it is reached only through the Function URL (enable_function_url); no load balancer resource is then created and the other ALB inputs are not needed. |
| `alb_listener_arn` | `string` | `null` | ARN of the ALB HTTPS listener to attach the rules to. Required when enable_alb is true. |
| `listener_rule_priority` | `number` | `100` | Priority for the ALB public listener rule (only used when create_public_listener_rule is true) |
| `create_public_listener_rule` | `bool` | `true` | Create the public ALB listener rule for the dashboard host. Set to false when a sign-in proxy owns the public rule and forwards to this dashboard through the bypass rule instead. |
| `host_header` | `string` | `""` | Host name the ALB listener rules match (e.g., compliance.example.com). Required when enable_alb is true. |
| `alb_oidc_config` | `object({ authorization_endpoint = string client_id = string client_secret = string issuer = string token_endpoint = string user_info_endpoint = string scope = string session_timeout = number })` | `null` | OIDC provider settings for the ALB authenticate-oidc action. When null, the public listener rule forwards without authentication. |
| `scanner_function_name` | `string` | `""` | Lambda function name of the compliance scanner |
| `scanner_function_arn` | `string` | `""` | Lambda function ARN of the compliance scanner |
| `hunterkiller_executor_function_name` | `string` | `""` | Lambda function name of the hunterkiller executor (empty when executor_armed=false) |
| `hunterkiller_executor_function_arn` | `string` | `""` | Lambda function ARN of the hunterkiller executor (empty when executor_armed=false) |
| `hunterkiller_executor_armed` | `bool` | `false` | Whether the hunterkiller executor is deployed |
| `compliance_report_table_name` | `string` | `""` | DynamoDB table name for compliance report data (written by scanner) |
| `compliance_report_table_arn` | `string` | `""` | DynamoDB table ARN for compliance report data (written by scanner) |
| `tf_state_bucket` | `string` | `""` | S3 bucket that holds the Terraform state of the root that deploys the dashboard |
| `tf_state_key` | `string` | `""` | S3 key of that state file |
| `ses_sender_email` | `string` | `""` | SES sender email for action confirmation emails |
| `dashboard_url` | `string` | `""` | Public URL of the dashboard (for self-referencing links) |
| `image_versions_ssm_name` | `string` | `""` | Optional SSM parameter holding a JSON object that annotates the container images on the security page: one key per image, each with ecr_repo, upstream, upstream_tag and ecr_digest. Empty shows the ECR repositories from Terraform state without annotations. |
| `cli_prefix` | `string` | `"aws"` | CLI prefix for remediation commands. Set it when a credential helper must precede `aws`. |
| `site_config_dir` | `string` | `""` | Directory with the deployment's own dashboard content: site.json (service names, connections and security annotations for the architecture and security pages) and overview.html (body of the overview page). Copied into the Lambda package. Empty (the default) shows only what is discovered from Terraform state. |
| `module_file_map` | `map(string)` | `{}` | Map of top-level Terraform module address to the file that declares it (e.g. { "module.alb" = "live/prod/main.tf" }), used for source links on resource pages. |
| `sprawl_test_function_arn` | `string` | `""` | ARN of an optional Lambda function you supply that creates non-compliant test resources, so that enforcement can be exercised; used only for the invoke permission. Empty when there is none. |
| `oauth2_proxy_bypass_header_name` | `string` | `"X-OAuth2-Proxy-Auth"` | Name of the static header that a sign-in proxy in front of this dashboard adds to upstream requests. Pair with oauth2_proxy_bypass_header_value to enable the bypass rule. |
| `oauth2_proxy_bypass_header_value` | `string` | `null` | Exact value of the bypass header that the sign-in proxy adds to upstream requests. When non-null, an ALB bypass rule forwards matching requests to the Lambda target group without auth, so the proxy can call back into the same hostname. |
| `oauth2_proxy_bypass_priority` | `number` | `128` | Priority of the sign-in proxy bypass listener rule. Must be a lower number (higher priority) than the public listener rule that routes traffic to the proxy. |
| `allow_unauthenticated_public_rule` | `bool` | `false` | Accept a public listener rule that forwards to the dashboard with no sign-in (create_public_listener_rule = true and alb_oidc_config = null). Without a sign-in front nobody has an identity, so the dashboard is read-only and readable by every caller of the listener. |
| `trusted_origins` | `list(string)` | `[]` | Additional origins (scheme://host) whose pages may submit dashboard actions, for a front that serves the dashboard under a host name the function cannot see in the Host or X-Forwarded-Host header. The request's own host and dashboard_url are always accepted. |
| `enable_viewer_gate` | `bool` | `false` | Restrict dashboard viewing to explicitly-authorized emails (static list + invite enrollment) |
| `viewer_emails` | `list(string)` | `[]` | Static email addresses authorized to view the dashboard (in addition to admin_emails). Only checked when enable_viewer_gate=true. |
| `provisioned_concurrency` | `number` | `0` | Number of provisioned concurrent executions (0 to disable) |
| `protected_resources_ssm_name` | `string` | `""` | SSM Parameter Store name containing protected resources JSON. Lambda reads at runtime. |
| `required_tags` | `list(string)` | `null` | List of tag keys that all resources should have. Null uses the required tags of the contract. |
| `tags` | `map(string)` | `{}` | Tags to apply to all resources |
| `enable_function_url` | `bool` | `false` | Expose the dashboard Lambda through a Function URL. Independent of the ALB: it can be used beside it or, with enable_alb = false, without one. |
| `function_url_authorization_type` | `string` | `"AWS_IAM"` | Function URL auth: AWS_IAM (only SigV4-signed callers, e.g. a fronting proxy) or NONE (the app's own auth carries the gate, e.g. behind an access proxy). |
| `function_url_invoke_principal_arns` | `list(string)` | `[]` | IAM principal ARNs granted lambda:InvokeFunctionUrl when function_url_authorization_type = AWS_IAM — for example the identity of each proxy that fronts this service, so that access can be granted and revoked per proxy. Empty grants no resource-based permission (callers rely on their own identity policy). Invoke permission gives a caller no identity: see function_url_identity_proxy_principal_arns. |
| `function_url_identity_proxy_principal_arns` | `list(string)` | `[]` | IAM principal ARNs whose Function URL requests may assert the user's identity in X-Forwarded-Email (function_url_authorization_type = AWS_IAM only). Name only a front that signs the user in itself and overwrites any X-Forwarded-Email the client sent. Permission to invoke the URL (function_url_invoke_principal_arns) gives no identity: a request from any other caller is anonymous, so it can read pages when the viewer gate is off and can perform no admin action. Empty: no Function URL caller asserts an identity. |
| `scan_stale_hours` | `number` | `26` | Hours since the last complete scan after which the overview shows the compliance status as STALE instead of a grade. Match the scanner module's scan_stale_alarm_hours. |

| Output | Description |
|---|---|
| `overrides_table_name` | DynamoDB overrides table name |
| `overrides_table_arn` | DynamoDB overrides table ARN |
| `lambda_function_name` | Dashboard Lambda function name |
| `lambda_function_arn` | Dashboard Lambda function ARN (for scoped lambda:InvokeFunctionUrl grants to fronting proxies) |
| `function_url` | Dashboard Lambda Function URL (empty when enable_function_url is false) |
| `target_group_arn` | ARN of the dashboard's ALB target group, for listener rules a deployment adds itself (empty when enable_alb is false) |

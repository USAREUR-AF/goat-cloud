# compliance-dashboard

<!-- BEGIN_TF_DOCS -->
## Compliance Dashboard

Interactive web dashboard for compliance management, architecture visualization, resource overrides, and control of enforcement (the hunterkiller module). Served by a Lambda function, behind an Application Load Balancer or through a Lambda Function URL.

## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.7.0 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 5.0 |
| <a name="requirement_null"></a> [null](#requirement\_null) | >= 3.0 |
| <a name="requirement_random"></a> [random](#requirement\_random) | >= 3.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_aws"></a> [aws](#provider\_aws) | >= 5.0 |
| <a name="provider_null"></a> [null](#provider\_null) | >= 3.0 |
| <a name="provider_random"></a> [random](#provider\_random) | >= 3.0 |

## Resources

| Name | Type |
|------|------|
| [aws_cloudwatch_log_group.lambda](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_group) | resource |
| [aws_dynamodb_table.overrides](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/dynamodb_table) | resource |
| [aws_iam_role.lambda](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role) | resource |
| [aws_iam_role_policy.lambda_permissions](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy) | resource |
| [aws_lambda_alias.live](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_alias) | resource |
| [aws_lambda_function.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_function) | resource |
| [aws_lambda_function_url.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_function_url) | resource |
| [aws_lambda_permission.alb](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_permission) | resource |
| [aws_lambda_permission.function_url](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_permission) | resource |
| [aws_lambda_provisioned_concurrency_config.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_provisioned_concurrency_config) | resource |
| [aws_lb_listener_rule.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb_listener_rule) | resource |
| [aws_lb_listener_rule.dashboard_no_auth](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb_listener_rule) | resource |
| [aws_lb_listener_rule.oauth2_proxy_bypass](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb_listener_rule) | resource |
| [aws_lb_target_group.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb_target_group) | resource |
| [aws_lb_target_group_attachment.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb_target_group_attachment) | resource |
| [aws_secretsmanager_secret.invite_hmac](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/secretsmanager_secret) | resource |
| [aws_secretsmanager_secret_version.invite_hmac](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/secretsmanager_secret_version) | resource |
| [null_resource.lambda_zip](https://registry.terraform.io/providers/hashicorp/null/latest/docs/resources/resource) | resource |
| [random_password.invite_hmac](https://registry.terraform.io/providers/hashicorp/random/latest/docs/resources/password) | resource |
| [aws_caller_identity.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/caller_identity) | data source |
| [aws_region.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/region) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_admin_emails"></a> [admin\_emails](#input\_admin\_emails) | Email addresses authorized for destructive actions (abort, destroy, extend) | `list(string)` | n/a | yes |
| <a name="input_compliance_reports_bucket"></a> [compliance\_reports\_bucket](#input\_compliance\_reports\_bucket) | S3 bucket name where compliance scanner writes reports | `string` | n/a | yes |
| <a name="input_domain_name"></a> [domain\_name](#input\_domain\_name) | Root domain name (e.g., example.com) for architecture diagram hostnames | `string` | n/a | yes |
| <a name="input_environment"></a> [environment](#input\_environment) | Environment name | `string` | n/a | yes |
| <a name="input_hunterkiller_function_arn"></a> [hunterkiller\_function\_arn](#input\_hunterkiller\_function\_arn) | Lambda function ARN of the hunterkiller notifier | `string` | n/a | yes |
| <a name="input_hunterkiller_function_name"></a> [hunterkiller\_function\_name](#input\_hunterkiller\_function\_name) | Lambda function name of the hunterkiller notifier | `string` | n/a | yes |
| <a name="input_hunterkiller_state_table_arn"></a> [hunterkiller\_state\_table\_arn](#input\_hunterkiller\_state\_table\_arn) | DynamoDB table ARN for hunterkiller enforcement state | `string` | n/a | yes |
| <a name="input_hunterkiller_state_table_name"></a> [hunterkiller\_state\_table\_name](#input\_hunterkiller\_state\_table\_name) | DynamoDB table name for hunterkiller enforcement state | `string` | n/a | yes |
| <a name="input_project"></a> [project](#input\_project) | Project name | `string` | n/a | yes |
| <a name="input_region"></a> [region](#input\_region) | AWS region the deployment's resources live in, for console URLs and generated commands | `string` | n/a | yes |
| <a name="input_repository_url"></a> [repository\_url](#input\_repository\_url) | URL of the repository that deploys this module. Used for source links and to recognize that repository's own entry in the IaC registry. | `string` | n/a | yes |
| <a name="input_alb_listener_arn"></a> [alb\_listener\_arn](#input\_alb\_listener\_arn) | ARN of the ALB HTTPS listener to attach the rules to. Required when enable\_alb is true. | `string` | `null` | no |
| <a name="input_alb_oidc_config"></a> [alb\_oidc\_config](#input\_alb\_oidc\_config) | OIDC provider settings for the ALB authenticate-oidc action. When null, the public listener rule forwards without authentication. | <pre>object({<br/>    authorization_endpoint = string<br/>    client_id              = string<br/>    client_secret          = string<br/>    issuer                 = string<br/>    token_endpoint         = string<br/>    user_info_endpoint     = string<br/>    scope                  = string<br/>    session_timeout        = number<br/>  })</pre> | `null` | no |
| <a name="input_allow_unauthenticated_public_rule"></a> [allow\_unauthenticated\_public\_rule](#input\_allow\_unauthenticated\_public\_rule) | Accept a public listener rule that forwards to the dashboard with no sign-in (create\_public\_listener\_rule = true and alb\_oidc\_config = null). Without a sign-in front nobody has an identity, so the dashboard is read-only and readable by every caller of the listener. | `bool` | `false` | no |
| <a name="input_cli_prefix"></a> [cli\_prefix](#input\_cli\_prefix) | CLI prefix for remediation commands. Set it when a credential helper must precede `aws`. | `string` | `"aws"` | no |
| <a name="input_compliance_report_table_arn"></a> [compliance\_report\_table\_arn](#input\_compliance\_report\_table\_arn) | DynamoDB table ARN for compliance report data (written by scanner) | `string` | `""` | no |
| <a name="input_compliance_report_table_name"></a> [compliance\_report\_table\_name](#input\_compliance\_report\_table\_name) | DynamoDB table name for compliance report data (written by scanner) | `string` | `""` | no |
| <a name="input_create_public_listener_rule"></a> [create\_public\_listener\_rule](#input\_create\_public\_listener\_rule) | Create the public ALB listener rule for the dashboard host. Set to false when a sign-in proxy owns the public rule and forwards to this dashboard through the bypass rule instead. | `bool` | `true` | no |
| <a name="input_dashboard_url"></a> [dashboard\_url](#input\_dashboard\_url) | Public URL of the dashboard (for self-referencing links) | `string` | `""` | no |
| <a name="input_enable_alb"></a> [enable\_alb](#input\_enable\_alb) | Serve the dashboard through an Application Load Balancer. Set to false when it is reached only through the Function URL (enable\_function\_url); no load balancer resource is then created and the other ALB inputs are not needed. | `bool` | `true` | no |
| <a name="input_enable_function_url"></a> [enable\_function\_url](#input\_enable\_function\_url) | Expose the dashboard Lambda through a Function URL. Independent of the ALB: it can be used beside it or, with enable\_alb = false, without one. | `bool` | `false` | no |
| <a name="input_enable_viewer_gate"></a> [enable\_viewer\_gate](#input\_enable\_viewer\_gate) | Restrict dashboard viewing to explicitly-authorized emails (static list + invite enrollment) | `bool` | `false` | no |
| <a name="input_function_url_authorization_type"></a> [function\_url\_authorization\_type](#input\_function\_url\_authorization\_type) | Function URL auth: AWS\_IAM (only SigV4-signed callers, e.g. a fronting proxy) or NONE (the app's own auth carries the gate, e.g. behind an access proxy). | `string` | `"AWS_IAM"` | no |
| <a name="input_function_url_identity_proxy_principal_arns"></a> [function\_url\_identity\_proxy\_principal\_arns](#input\_function\_url\_identity\_proxy\_principal\_arns) | IAM principal ARNs whose Function URL requests may assert the user's identity in X-Forwarded-Email (function\_url\_authorization\_type = AWS\_IAM only). Name only a front that signs the user in itself and overwrites any X-Forwarded-Email the client sent. Permission to invoke the URL (function\_url\_invoke\_principal\_arns) gives no identity: a request from any other caller is anonymous, so it can read pages when the viewer gate is off and can perform no admin action. Empty: no Function URL caller asserts an identity. | `list(string)` | `[]` | no |
| <a name="input_function_url_invoke_principal_arns"></a> [function\_url\_invoke\_principal\_arns](#input\_function\_url\_invoke\_principal\_arns) | IAM principal ARNs granted lambda:InvokeFunctionUrl when function\_url\_authorization\_type = AWS\_IAM — for example the identity of each proxy that fronts this service, so that access can be granted and revoked per proxy. Empty grants no resource-based permission (callers rely on their own identity policy). Invoke permission gives a caller no identity: see function\_url\_identity\_proxy\_principal\_arns. | `list(string)` | `[]` | no |
| <a name="input_host_header"></a> [host\_header](#input\_host\_header) | Host name the ALB listener rules match (e.g., compliance.example.com). Required when enable\_alb is true. | `string` | `""` | no |
| <a name="input_hunterkiller_executor_armed"></a> [hunterkiller\_executor\_armed](#input\_hunterkiller\_executor\_armed) | Whether the hunterkiller executor is deployed | `bool` | `false` | no |
| <a name="input_hunterkiller_executor_function_arn"></a> [hunterkiller\_executor\_function\_arn](#input\_hunterkiller\_executor\_function\_arn) | Lambda function ARN of the hunterkiller executor (empty when executor\_armed=false) | `string` | `""` | no |
| <a name="input_hunterkiller_executor_function_name"></a> [hunterkiller\_executor\_function\_name](#input\_hunterkiller\_executor\_function\_name) | Lambda function name of the hunterkiller executor (empty when executor\_armed=false) | `string` | `""` | no |
| <a name="input_image_versions_ssm_name"></a> [image\_versions\_ssm\_name](#input\_image\_versions\_ssm\_name) | Optional SSM parameter holding a JSON object that annotates the container images on the security page: one key per image, each with ecr\_repo, upstream, upstream\_tag and ecr\_digest. Empty shows the ECR repositories from Terraform state without annotations. | `string` | `""` | no |
| <a name="input_listener_rule_priority"></a> [listener\_rule\_priority](#input\_listener\_rule\_priority) | Priority for the ALB public listener rule (only used when create\_public\_listener\_rule is true) | `number` | `100` | no |
| <a name="input_module_file_map"></a> [module\_file\_map](#input\_module\_file\_map) | Map of top-level Terraform module address to the file that declares it (e.g. { "module.alb" = "live/prod/main.tf" }), used for source links on resource pages. | `map(string)` | `{}` | no |
| <a name="input_oauth2_proxy_bypass_header_name"></a> [oauth2\_proxy\_bypass\_header\_name](#input\_oauth2\_proxy\_bypass\_header\_name) | Name of the static header that a sign-in proxy in front of this dashboard adds to upstream requests. Pair with oauth2\_proxy\_bypass\_header\_value to enable the bypass rule. | `string` | `"X-OAuth2-Proxy-Auth"` | no |
| <a name="input_oauth2_proxy_bypass_header_value"></a> [oauth2\_proxy\_bypass\_header\_value](#input\_oauth2\_proxy\_bypass\_header\_value) | Exact value of the bypass header that the sign-in proxy adds to upstream requests. When non-null, an ALB bypass rule forwards matching requests to the Lambda target group without auth, so the proxy can call back into the same hostname. | `string` | `null` | no |
| <a name="input_oauth2_proxy_bypass_priority"></a> [oauth2\_proxy\_bypass\_priority](#input\_oauth2\_proxy\_bypass\_priority) | Priority of the sign-in proxy bypass listener rule. Must be a lower number (higher priority) than the public listener rule that routes traffic to the proxy. | `number` | `128` | no |
| <a name="input_protected_resources_ssm_name"></a> [protected\_resources\_ssm\_name](#input\_protected\_resources\_ssm\_name) | SSM Parameter Store name containing protected resources JSON. Lambda reads at runtime. | `string` | `""` | no |
| <a name="input_provisioned_concurrency"></a> [provisioned\_concurrency](#input\_provisioned\_concurrency) | Number of provisioned concurrent executions (0 to disable) | `number` | `0` | no |
| <a name="input_required_tags"></a> [required\_tags](#input\_required\_tags) | List of tag keys that all resources should have. Null uses the required tags of the contract. | `list(string)` | `null` | no |
| <a name="input_scan_stale_hours"></a> [scan\_stale\_hours](#input\_scan\_stale\_hours) | Hours since the last complete scan after which the overview shows the compliance status as STALE instead of a grade. Match the scanner module's scan\_stale\_alarm\_hours. | `number` | `26` | no |
| <a name="input_scanner_function_arn"></a> [scanner\_function\_arn](#input\_scanner\_function\_arn) | Lambda function ARN of the compliance scanner | `string` | `""` | no |
| <a name="input_scanner_function_name"></a> [scanner\_function\_name](#input\_scanner\_function\_name) | Lambda function name of the compliance scanner | `string` | `""` | no |
| <a name="input_ses_sender_email"></a> [ses\_sender\_email](#input\_ses\_sender\_email) | SES sender email for action confirmation emails | `string` | `""` | no |
| <a name="input_site_config_dir"></a> [site\_config\_dir](#input\_site\_config\_dir) | Directory with the deployment's own dashboard content: site.json (service names, connections and security annotations for the architecture and security pages) and overview.html (body of the overview page). Copied into the Lambda package. Empty (the default) shows only what is discovered from Terraform state. | `string` | `""` | no |
| <a name="input_sprawl_test_function_arn"></a> [sprawl\_test\_function\_arn](#input\_sprawl\_test\_function\_arn) | ARN of an optional Lambda function you supply that creates non-compliant test resources, so that enforcement can be exercised; used only for the invoke permission. Empty when there is none. | `string` | `""` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | Tags to apply to all resources | `map(string)` | `{}` | no |
| <a name="input_tf_state_bucket"></a> [tf\_state\_bucket](#input\_tf\_state\_bucket) | S3 bucket that holds the Terraform state of the root that deploys the dashboard | `string` | `""` | no |
| <a name="input_tf_state_key"></a> [tf\_state\_key](#input\_tf\_state\_key) | S3 key of that state file | `string` | `""` | no |
| <a name="input_trusted_origins"></a> [trusted\_origins](#input\_trusted\_origins) | Additional origins (scheme://host) whose pages may submit dashboard actions, for a front that serves the dashboard under a host name the function cannot see in the Host or X-Forwarded-Host header. The request's own host and dashboard\_url are always accepted. | `list(string)` | `[]` | no |
| <a name="input_viewer_emails"></a> [viewer\_emails](#input\_viewer\_emails) | Static email addresses authorized to view the dashboard (in addition to admin\_emails). Only checked when enable\_viewer\_gate=true. | `list(string)` | `[]` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_function_url"></a> [function\_url](#output\_function\_url) | Dashboard Lambda Function URL (empty when enable\_function\_url is false) |
| <a name="output_lambda_function_arn"></a> [lambda\_function\_arn](#output\_lambda\_function\_arn) | Dashboard Lambda function ARN (for scoped lambda:InvokeFunctionUrl grants to fronting proxies) |
| <a name="output_lambda_function_name"></a> [lambda\_function\_name](#output\_lambda\_function\_name) | Dashboard Lambda function name |
| <a name="output_overrides_table_arn"></a> [overrides\_table\_arn](#output\_overrides\_table\_arn) | DynamoDB overrides table ARN |
| <a name="output_overrides_table_name"></a> [overrides\_table\_name](#output\_overrides\_table\_name) | DynamoDB overrides table name |
| <a name="output_target_group_arn"></a> [target\_group\_arn](#output\_target\_group\_arn) | ARN of the dashboard's ALB target group, for listener rules a deployment adds itself (empty when enable\_alb is false) |
<!-- END_TF_DOCS -->

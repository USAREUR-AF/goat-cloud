# hunterkiller

<!-- BEGIN_TF_DOCS -->
## Hunterkiller

Automated compliance enforcement with three-layer safety model: notifier/executor split, DynamoDB approval gate, and Terraform-gated permissions. 24-hour grace period before teardown.



## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.7.0 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 5.0 |
| <a name="requirement_null"></a> [null](#requirement\_null) | >= 3.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_aws"></a> [aws](#provider\_aws) | >= 5.0 |
| <a name="provider_null"></a> [null](#provider\_null) | >= 3.0 |

## Resources

| Name | Type |
|------|------|
| [aws_cloudwatch_log_group.executor](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_group) | resource |
| [aws_cloudwatch_log_group.notifier](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_group) | resource |
| [aws_dynamodb_table.state](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/dynamodb_table) | resource |
| [aws_iam_role.executor](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role) | resource |
| [aws_iam_role.notifier](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role) | resource |
| [aws_iam_role.scheduler](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role) | resource |
| [aws_iam_role_policy.executor_permissions](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy) | resource |
| [aws_iam_role_policy.notifier_permissions](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy) | resource |
| [aws_iam_role_policy.scheduler_permissions](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy) | resource |
| [aws_iam_role_policy_attachment.notifier_read_only](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy_attachment) | resource |
| [aws_lambda_function.executor](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_function) | resource |
| [aws_lambda_function.notifier](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_function) | resource |
| [aws_lambda_function_event_invoke_config.notifier](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_function_event_invoke_config) | resource |
| [aws_lambda_permission.allow_scanner_invoke](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_permission) | resource |
| [aws_lambda_permission.allow_scheduler_executor](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_permission) | resource |
| [null_resource.lambda_zip](https://registry.terraform.io/providers/hashicorp/null/latest/docs/resources/resource) | resource |
| [aws_caller_identity.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/caller_identity) | data source |
| [aws_iam_policy_document.executor_assume_role](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_iam_policy_document.executor_permissions](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_iam_policy_document.notifier_assume_role](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_iam_policy_document.notifier_permissions](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_iam_policy_document.scheduler_assume_role](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_iam_policy_document.scheduler_permissions](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_region.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/region) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_alarm_sns_topic_arn"></a> [alarm\_sns\_topic\_arn](#input\_alarm\_sns\_topic\_arn) | SNS topic that receives this module's own failure alarms. Empty (the default) creates no alarm: a function that stops or fails is then not reported by this module. The topic's policy must allow cloudwatch.amazonaws.com to publish. | `string` | `""` | no |
| <a name="input_compliance_reports_bucket"></a> [compliance\_reports\_bucket](#input\_compliance\_reports\_bucket) | S3 bucket where the compliance scanner stores JSON reports | `string` | n/a | yes |
| <a name="input_compliance_scanner_function_name"></a> [compliance\_scanner\_function\_name](#input\_compliance\_scanner\_function\_name) | Name of the compliance scanner Lambda function (for invoke permissions) | `string` | n/a | yes |
| <a name="input_contact_allowed_email_domains"></a> [contact\_allowed\_email\_domains](#input\_contact\_allowed\_email\_domains) | Mail domains a contact address taken from a resource tag (or an IAM user's Email tag) may be in. A warning for a resource whose contact is outside these domains goes to the admins instead. Empty accepts any domain: anyone who can tag a resource then chooses where its warning email is sent. | `list(string)` | `[]` | no |
| <a name="input_environment"></a> [environment](#input\_environment) | Environment name | `string` | n/a | yes |
| <a name="input_project"></a> [project](#input\_project) | Project name | `string` | n/a | yes |
| <a name="input_auto_approve_expired"></a> [auto\_approve\_expired](#input\_auto\_approve\_expired) | Approve teardown automatically for resources whose ExpiresAt is in the past. For non-production environments only. | `bool` | `false` | no |
| <a name="input_bedrock_summary_model_id"></a> [bedrock\_summary\_model\_id](#input\_bedrock\_summary\_model\_id) | Bedrock model or inference-profile id for an LLM-written summary at the top of enforcement emails. Empty (the default) turns the summary off and grants no Bedrock permission. | `string` | `""` | no |
| <a name="input_bedrock_summary_region"></a> [bedrock\_summary\_region](#input\_bedrock\_summary\_region) | Region of the Bedrock runtime endpoint to call. Empty uses the function's own region. | `string` | `""` | no |
| <a name="input_contact_tag_keys"></a> [contact\_tag\_keys](#input\_contact\_tag\_keys) | Tag keys to check for direct contact email (checked in order, first non-empty wins). The default reads the contract's Contact tag only; add keys such as OwnerEmail to read others. | `list(string)` | <pre>[<br/>  "Contact"<br/>]</pre> | no |
| <a name="input_dashboard_url"></a> [dashboard\_url](#input\_dashboard\_url) | URL of the compliance dashboard, linked from warning emails so an owner can object before enforcement | `string` | `""` | no |
| <a name="input_destroy_now_report_max_age_hours"></a> [destroy\_now\_report\_max\_age\_hours](#input\_destroy\_now\_report\_max\_age\_hours) | "Destroy now" acts only on a resource with a HIGH finding in the newest compliance report generated no more than this many hours ago. | `number` | `36` | no |
| <a name="input_dry_run"></a> [dry\_run](#input\_dry\_run) | When true, the hunterkiller logs teardown actions but does not execute them. Safety net. | `bool` | `true` | no |
| <a name="input_executor_armed"></a> [executor\_armed](#input\_executor\_armed) | When true, creates the executor Lambda with destructive IAM permissions. Default false — no destructive infrastructure exists. | `bool` | `false` | no |
| <a name="input_grace_period_hours"></a> [grace\_period\_hours](#input\_grace\_period\_hours) | Hours between the warning and the enforcement action | `number` | `24` | no |
| <a name="input_protected_environments"></a> [protected\_environments](#input\_protected\_environments) | Environment tag values that are exempt from teardown. Null uses the contract's protected environment names (prod, production) | `list(string)` | `null` | no |
| <a name="input_protected_resource_types"></a> [protected\_resource\_types](#input\_protected\_resource\_types) | Resource types that are always protected from teardown. Require manual action to delete. | `list(string)` | <pre>[<br/>  "route53:hostedzone",<br/>  "payments:payment-instrument",<br/>  "s3:bucket"<br/>]</pre> | no |
| <a name="input_protected_resources_ssm_name"></a> [protected\_resources\_ssm\_name](#input\_protected\_resources\_ssm\_name) | SSM Parameter Store name containing protected resources JSON. Executor reads at runtime. | `string` | `""` | no |
| <a name="input_protected_tag_key"></a> [protected\_tag\_key](#input\_protected\_tag\_key) | Tag key that marks a resource as exempt from teardown. Null uses the contract's key for the protected fact (Protected) | `string` | `null` | no |
| <a name="input_ses_admin_emails"></a> [ses\_admin\_emails](#input\_ses\_admin\_emails) | Admin email addresses: the fallback contacts when a resource names no owner | `list(string)` | `[]` | no |
| <a name="input_ses_sender_email"></a> [ses\_sender\_email](#input\_ses\_sender\_email) | Verified SES sender address for warning and teardown notification emails | `string` | `""` | no |
| <a name="input_state_ttl_days"></a> [state\_ttl\_days](#input\_state\_ttl\_days) | Days to retain DynamoDB notification records before TTL cleanup | `number` | `30` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | Additional tags | `map(string)` | `{}` | no |
| <a name="input_unrecoverable_teardown"></a> [unrecoverable\_teardown](#input\_unrecoverable\_teardown) | Remove data-holding resources with no recovery point. Default false: an RDS instance is deleted with a final snapshot and its automated backups retained, an EBS volume (and the volumes an instance termination deletes) is snapshotted first, a single-node Redis or Valkey cache cluster takes a final snapshot, a secret gets a 30-day recovery window, a KMS key a 30-day pending window, and a bucket or image repository that still holds data is refused. true selects the plain delete for all of these and grants the role s3:DeleteObject. | `bool` | `false` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_executor_armed"></a> [executor\_armed](#output\_executor\_armed) | Whether the executor Lambda is deployed |
| <a name="output_executor_function_arn"></a> [executor\_function\_arn](#output\_executor\_function\_arn) | ARN of the hunterkiller executor Lambda function (empty when executor\_armed=false) |
| <a name="output_executor_function_name"></a> [executor\_function\_name](#output\_executor\_function\_name) | Name of the hunterkiller executor Lambda function (empty when executor\_armed=false) |
| <a name="output_lambda_function_arn"></a> [lambda\_function\_arn](#output\_lambda\_function\_arn) | ARN of the hunterkiller Lambda function (alias for notifier\_function\_arn) |
| <a name="output_lambda_function_name"></a> [lambda\_function\_name](#output\_lambda\_function\_name) | Name of the hunterkiller Lambda function (alias for notifier\_function\_name) |
| <a name="output_notifier_function_arn"></a> [notifier\_function\_arn](#output\_notifier\_function\_arn) | ARN of the hunterkiller notifier Lambda function |
| <a name="output_notifier_function_name"></a> [notifier\_function\_name](#output\_notifier\_function\_name) | Name of the hunterkiller notifier Lambda function |
| <a name="output_state_table_arn"></a> [state\_table\_arn](#output\_state\_table\_arn) | ARN of the DynamoDB state tracking table |
| <a name="output_state_table_name"></a> [state\_table\_name](#output\_state\_table\_name) | Name of the DynamoDB state tracking table |
<!-- END_TF_DOCS -->

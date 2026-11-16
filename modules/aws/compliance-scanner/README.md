# compliance-scanner

<!-- BEGIN_TF_DOCS -->
## Compliance Scanner

Nightly Lambda that discovers all AWS resources, checks tag compliance, detects Terraform drift, and generates reports (JSON, Markdown, styled HTML email via SES).



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
| [aws_cloudwatch_event_rule.nightly_scan](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_event_rule) | resource |
| [aws_cloudwatch_event_target.lambda](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_event_target) | resource |
| [aws_cloudwatch_log_group.lambda](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_group) | resource |
| [aws_dynamodb_table.report_data](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/dynamodb_table) | resource |
| [aws_iam_role.lambda](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role) | resource |
| [aws_iam_role_policy.lambda_write](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy) | resource |
| [aws_iam_role_policy_attachment.read_only](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy_attachment) | resource |
| [aws_lambda_function.compliance_scanner](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_function) | resource |
| [aws_lambda_permission.eventbridge](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_permission) | resource |
| [aws_s3_bucket.reports](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket) | resource |
| [aws_s3_bucket_lifecycle_configuration.reports](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_lifecycle_configuration) | resource |
| [aws_s3_bucket_public_access_block.reports](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_public_access_block) | resource |
| [aws_s3_bucket_server_side_encryption_configuration.reports](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_server_side_encryption_configuration) | resource |
| [aws_s3_bucket_versioning.reports](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_versioning) | resource |
| [null_resource.lambda_zip](https://registry.terraform.io/providers/hashicorp/null/latest/docs/resources/resource) | resource |
| [aws_caller_identity.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/caller_identity) | data source |
| [aws_iam_policy_document.lambda_assume_role](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_iam_policy_document.lambda_write](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_region.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/region) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_alarm_sns_topic_arn"></a> [alarm\_sns\_topic\_arn](#input\_alarm\_sns\_topic\_arn) | SNS topic that receives this module's own failure alarms (function error, failed scan, no successful scan). Empty (the default) creates no alarm: a function that stops or fails is then not reported by this module. The topic's policy must allow cloudwatch.amazonaws.com to publish. | `string` | `""` | no |
| <a name="input_environment"></a> [environment](#input\_environment) | Environment name | `string` | n/a | yes |
| <a name="input_manage_reports_bucket_policy"></a> [manage\_reports\_bucket\_policy](#input\_manage\_reports\_bucket\_policy) | Attach a bucket policy to the reports bucket that refuses requests not made over TLS. Set to false when the bucket's policy is managed elsewhere (a bucket has one policy), and include the same statement there. | `bool` | `true` | no |
| <a name="input_project"></a> [project](#input\_project) | Project name | `string` | n/a | yes |
| <a name="input_repository_url"></a> [repository\_url](#input\_repository\_url) | URL of the repository that deploys this module. Used for report links and to recognize that repository's own entry in the IaC registry. | `string` | n/a | yes |
| <a name="input_scan_stale_alarm_hours"></a> [scan\_stale\_alarm\_hours](#input\_scan\_stale\_alarm\_hours) | Hours without a successful scan after which the no-successful-scan alarm fires. The default allows a daily scan to run two hours late. | `number` | `26` | no |
| <a name="input_tf_state_bucket"></a> [tf\_state\_bucket](#input\_tf\_state\_bucket) | S3 bucket that holds the Terraform state of the root that deploys the scanner (for drift detection) | `string` | n/a | yes |
| <a name="input_tf_state_key"></a> [tf\_state\_key](#input\_tf\_state\_key) | S3 key of that state file | `string` | n/a | yes |
| <a name="input_cli_prefix"></a> [cli\_prefix](#input\_cli\_prefix) | CLI prefix for teardown commands in reports. Set it when a credential helper must precede `aws`. | `string` | `"aws"` | no |
| <a name="input_exclusion_arns"></a> [exclusion\_arns](#input\_exclusion\_arns) | Specific ARNs to exclude from drift detection (bootstrap resources, TF state bucket, etc.) | `list(string)` | `[]` | no |
| <a name="input_exclusion_patterns"></a> [exclusion\_patterns](#input\_exclusion\_patterns) | Regex patterns for ARNs to exclude from drift detection | `list(string)` | `[]` | no |
| <a name="input_hunterkiller_function_name"></a> [hunterkiller\_function\_name](#input\_hunterkiller\_function\_name) | Name of the hunterkiller Lambda function. When set, the scanner invokes it in warn mode after each scan. | `string` | `""` | no |
| <a name="input_iac_registry_prefix"></a> [iac\_registry\_prefix](#input\_iac\_registry\_prefix) | SSM parameter path prefix for IaC registry entries. Defaults to /{project}/{environment}/iac-registry. | `string` | `""` | no |
| <a name="input_iac_registry_state_buckets"></a> [iac\_registry\_state\_buckets](#input\_iac\_registry\_state\_buckets) | S3 bucket names that hold cross-repo TF state for IaC registry entries. The scanner Lambda gets s3:GetObject on these so it can read other repos' state for drift detection. | `list(string)` | `[]` | no |
| <a name="input_overrides_table_arn"></a> [overrides\_table\_arn](#input\_overrides\_table\_arn) | DynamoDB table ARN for compliance dashboard overrides. Required for IAM permissions when overrides\_table\_name is set. | `string` | `""` | no |
| <a name="input_overrides_table_name"></a> [overrides\_table\_name](#input\_overrides\_table\_name) | DynamoDB table name for compliance dashboard overrides (false positives / exemptions). When set, the scanner excludes overridden resources from findings. | `string` | `""` | no |
| <a name="input_protected_resources_ssm_name"></a> [protected\_resources\_ssm\_name](#input\_protected\_resources\_ssm\_name) | SSM Parameter Store name containing protected resources JSON. Lambda reads at runtime. | `string` | `""` | no |
| <a name="input_purpose_hints"></a> [purpose\_hints](#input\_purpose\_hints) | Map of resource-name fragment to purpose text, used to describe resources that carry no descriptive tag (e.g. { wiki = "Team wiki" }). | `map(string)` | `{}` | no |
| <a name="input_report_retention_days"></a> [report\_retention\_days](#input\_report\_retention\_days) | Days to retain compliance reports in S3 | `number` | `90` | no |
| <a name="input_required_tags"></a> [required\_tags](#input\_required\_tags) | List of tag keys that all resources should have. Null uses the required tags of the contract. | `list(string)` | `null` | no |
| <a name="input_scanner_source_url"></a> [scanner\_source\_url](#input\_scanner\_source\_url) | URL of the scanner's source, linked from the footer of each report. Empty omits the link. | `string` | `""` | no |
| <a name="input_schedule_expression"></a> [schedule\_expression](#input\_schedule\_expression) | EventBridge schedule expression for the scan | `string` | `"cron(0 3 ? * * *)"` | no |
| <a name="input_ses_recipient_emails"></a> [ses\_recipient\_emails](#input\_ses\_recipient\_emails) | Email addresses to receive the HTML compliance report via SES | `list(string)` | `[]` | no |
| <a name="input_ses_sender_email"></a> [ses\_sender\_email](#input\_ses\_sender\_email) | Verified SES sender address for HTML compliance emails (e.g. compliance@example.com). Leave empty to skip SES. | `string` | `""` | no |
| <a name="input_standards_url"></a> [standards\_url](#input\_standards\_url) | URL of the document that states the deployment's compliance standards, linked from the footer of each report. Empty omits the link. | `string` | `""` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | Additional tags | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_eventbridge_rule_arn"></a> [eventbridge\_rule\_arn](#output\_eventbridge\_rule\_arn) | ARN of the nightly compliance scan EventBridge rule |
| <a name="output_lambda_function_arn"></a> [lambda\_function\_arn](#output\_lambda\_function\_arn) | ARN of the compliance scanner Lambda function |
| <a name="output_lambda_function_name"></a> [lambda\_function\_name](#output\_lambda\_function\_name) | Name of the compliance scanner Lambda function |
| <a name="output_report_data_table_arn"></a> [report\_data\_table\_arn](#output\_report\_data\_table\_arn) | ARN of the DynamoDB table for compliance report data |
| <a name="output_report_data_table_name"></a> [report\_data\_table\_name](#output\_report\_data\_table\_name) | Name of the DynamoDB table for compliance report data |
| <a name="output_reports_bucket"></a> [reports\_bucket](#output\_reports\_bucket) | Name of the S3 bucket for compliance reports |
| <a name="output_reports_bucket_arn"></a> [reports\_bucket\_arn](#output\_reports\_bucket\_arn) | ARN of the S3 bucket for compliance reports |
<!-- END_TF_DOCS -->

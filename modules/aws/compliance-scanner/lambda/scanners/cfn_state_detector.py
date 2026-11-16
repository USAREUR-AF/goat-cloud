"""Detect resources managed by CloudFormation stacks (Serverless Framework / CDK).

Lists all CloudFormation stacks in active regions, extracts physical resource
IDs, and maps them to AWS ARNs. Cross-references with the IaC registry to
attribute resources to their source repo.
"""

import boto3

# CloudFormation resource type -> ARN construction.
# {cfn_type: (arn_template, id_is_arn)}
# Templates use {region}, {account}, {id} placeholders.
_CFN_TYPE_TO_ARN = {
    "AWS::Lambda::Function": "arn:aws:lambda:{region}:{account}:function:{id}",
    "AWS::DynamoDB::Table": "arn:aws:dynamodb:{region}:{account}:table/{id}",
    "AWS::S3::Bucket": "arn:aws:s3:::{id}",
    "AWS::ApiGateway::RestApi": "arn:aws:apigateway:{region}::/restapis/{id}",
    "AWS::ApiGatewayV2::Api": "arn:aws:apigateway:{region}::/apis/{id}",
    "AWS::IAM::Role": "arn:aws:iam::{account}:role/{id}",
    "AWS::IAM::Policy": None,  # physical ID is already an ARN
    "AWS::Logs::LogGroup": "arn:aws:logs:{region}:{account}:log-group:{id}",
    "AWS::EC2::SecurityGroup": "arn:aws:ec2:{region}:{account}:security-group/{id}",
    "AWS::EC2::Instance": "arn:aws:ec2:{region}:{account}:instance/{id}",
    "AWS::EC2::Volume": "arn:aws:ec2:{region}:{account}:volume/{id}",
    "AWS::EC2::Subnet": "arn:aws:ec2:{region}:{account}:subnet/{id}",
    "AWS::EC2::VPC": "arn:aws:ec2:{region}:{account}:vpc/{id}",
    "AWS::SNS::Topic": None,  # physical ID is already an ARN
    "AWS::SQS::Queue": "SQS_URL",  # special: URL -> ARN conversion below
    "AWS::CloudWatch::Alarm": "arn:aws:cloudwatch:{region}:{account}:alarm:{id}",
    "AWS::Events::Rule": "arn:aws:events:{region}:{account}:rule/{id}",
    "AWS::ECR::Repository": "arn:aws:ecr:{region}:{account}:repository/{id}",
    "AWS::ECS::Cluster": "arn:aws:ecs:{region}:{account}:cluster/{id}",
    "AWS::ECS::Service": None,  # physical ID is already an ARN
    "AWS::ECS::TaskDefinition": None,  # physical ID is already an ARN
    "AWS::ElastiCache::CacheCluster": "arn:aws:elasticache:{region}:{account}:cluster:{id}",
    "AWS::RDS::DBInstance": "arn:aws:rds:{region}:{account}:db:{id}",
    "AWS::KMS::Key": "arn:aws:kms:{region}:{account}:key/{id}",
    "AWS::SecretsManager::Secret": None,  # physical ID is already an ARN
    "AWS::SSM::Parameter": None,  # physical ID is already an ARN-like path
    "AWS::CloudFront::Distribution": "arn:aws:cloudfront::{account}:distribution/{id}",
    "AWS::ApiGatewayV2::Stage": "APIGW_STAGE",  # special: needs parent API ID
}

# Stack statuses to include (exclude deleted/failed rollback stacks)
_ACTIVE_STATUSES = [
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
    "UPDATE_ROLLBACK_COMPLETE",
    "IMPORT_COMPLETE",
    "CREATE_IN_PROGRESS",
    "UPDATE_IN_PROGRESS",
    "REVIEW_IN_PROGRESS",
]


def _physical_id_to_arn(physical_id, cfn_resource_type, region, account_id):
    """Map a CloudFormation PhysicalResourceId to an AWS ARN.

    Returns the ARN string, or None if unmappable.
    """
    if not physical_id:
        return None

    # If physical ID is already an ARN, return it directly
    if physical_id.startswith("arn:aws:"):
        return physical_id

    template = _CFN_TYPE_TO_ARN.get(cfn_resource_type)

    if template is None:
        # Check if we recognize the type but it's ARN-based
        if cfn_resource_type in _CFN_TYPE_TO_ARN:
            return None  # type is known but ID isn't an ARN — skip
        return None  # unknown type

    # SQS: physical ID is a URL like https://sqs.{region}.amazonaws.com/{account}/{name}
    if template == "SQS_URL":
        # e.g. https://sqs.eu-central-1.amazonaws.com/123456789012/my-queue
        parts = physical_id.rstrip("/").rsplit("/", 2)
        if len(parts) >= 3:
            queue_name = parts[-1]
            return f"arn:aws:sqs:{region}:{account_id}:{queue_name}"
        return None

    # API Gateway V2 stage: needs parent API context (resolved in _extract_stack_resources)
    if template == "APIGW_STAGE":
        return None  # handled separately

    return template.format(region=region, account=account_id, id=physical_id)


def _classify_stack(stack, registered_stacks=None):
    """Classify a CloudFormation stack by IaC tool.

    Checks registry first (most reliable), then falls back to tag heuristics.
    Returns 'serverless', 'cdk', or 'cloudformation'.
    """
    stack_name = stack.get("StackName", "")

    # Registry is the most reliable source — trust it over tag heuristics
    if registered_stacks and stack_name in registered_stacks:
        return registered_stacks[stack_name].get("iac_type", "cloudformation")

    tags = {t["Key"]: t["Value"] for t in stack.get("Tags", [])}
    if "aws:cdk:id" in tags or "aws:cdk:path" in tags:
        return "cdk"
    if "STAGE" in tags:
        return "serverless"
    return "cloudformation"


def detect_cfn_managed_resources(registry_entries, active_regions, account_id):
    """Detect resources managed by CloudFormation stacks.

    For each registry entry with iac_type in ('serverless', 'cdk'), list the
    specified CloudFormation stacks and extract resource ARNs.

    Also discovers unregistered stacks for reporting.

    Args:
        registry_entries: Registry entries with iac_type in ('serverless', 'cdk')
        active_regions: List of AWS region names to scan
        account_id: AWS account ID

    Returns:
        Tuple of:
        - dict: {arn: {repo_url, iac_type, source, repo_slug}}
        - list: Unregistered stack summaries for reporting
    """
    # Build lookup: stack_name -> registry entry
    registered_stacks = {}
    for entry in registry_entries:
        loc = entry.get("state_location", {})
        for stack_name in loc.get("stack_names", []):
            registered_stacks[stack_name] = entry

    managed_resources = {}
    unregistered_stacks = []

    for region in active_regions:
        try:
            cfn = boto3.client("cloudformation", region_name=region)
            _scan_region_stacks(
                cfn,
                region,
                account_id,
                registered_stacks,
                managed_resources,
                unregistered_stacks,
            )
        except Exception as e:
            print(f"WARNING: CloudFormation scan failed for {region}: {e}")

    print(
        f"CloudFormation detection: {len(managed_resources)} resources "
        f"from {len(registered_stacks)} registered stacks, "
        f"{len(unregistered_stacks)} unregistered stacks found"
    )

    return managed_resources, unregistered_stacks


def _scan_region_stacks(cfn, region, account_id, registered_stacks, managed_resources, unregistered_stacks):
    """Scan a single region for CloudFormation stacks and their resources."""
    paginator = cfn.get_paginator("list_stacks")
    for page in paginator.paginate(StackStatusFilter=_ACTIVE_STATUSES):
        for summary in page.get("StackSummaries", []):
            stack_name = summary["StackName"]

            if stack_name in registered_stacks:
                entry = registered_stacks[stack_name]
                _extract_stack_resources(
                    cfn,
                    stack_name,
                    region,
                    account_id,
                    managed_resources,
                    repo_url=entry.get("repo_url", ""),
                    iac_type=entry.get("iac_type", "cloudformation"),
                    repo_slug=entry.get("repo_slug", stack_name),
                )
            else:
                # Classify and record unregistered stack
                try:
                    desc = cfn.describe_stacks(StackName=stack_name)
                    stack = desc["Stacks"][0] if desc.get("Stacks") else {}
                except Exception:
                    stack = {}

                iac_type = _classify_stack(stack, registered_stacks)
                # Only report non-AWS-internal stacks
                if not stack_name.startswith("StackSet-") and stack_name != "CDKToolkit":
                    resource_count = _count_stack_resources(cfn, stack_name)
                    if resource_count > 0:
                        unregistered_stacks.append(
                            {
                                "stack_name": stack_name,
                                "region": region,
                                "iac_type": iac_type,
                                "resource_count": resource_count,
                                "recommendation": "Register this stack in the IaC registry",
                            }
                        )


def _extract_stack_resources(cfn, stack_name, region, account_id, managed_resources, repo_url, iac_type, repo_slug):
    """Extract all resource ARNs from a CloudFormation stack.

    Also infers related resources not directly in the stack:
    - API Gateway V2 stages (need parent API ID for ARN construction)
    - Lambda execution roles (Serverless Framework creates these outside the stack)
    """
    attribution = {
        "repo_url": repo_url,
        "iac_type": iac_type,
        "source": f"cfn:{stack_name}",
        "repo_slug": repo_slug,
    }

    # Collect API IDs and Lambda function names for post-processing
    api_ids = []
    lambda_function_names = []

    try:
        paginator = cfn.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=stack_name):
            for resource in page.get("StackResourceSummaries", []):
                physical_id = resource.get("PhysicalResourceId", "")
                cfn_type = resource.get("ResourceType", "")

                arn = _physical_id_to_arn(physical_id, cfn_type, region, account_id)
                if arn:
                    managed_resources[arn] = attribution

                # Track resources for inference passes
                if cfn_type == "AWS::ApiGatewayV2::Api" and physical_id:
                    api_ids.append(physical_id)
                elif cfn_type == "AWS::Lambda::Function" and physical_id:
                    lambda_function_names.append(physical_id)
    except Exception as e:
        print(f"WARNING: Failed to list resources for stack {stack_name}: {e}")
        return

    # Inference pass 1: API Gateway V2 stages
    if api_ids:
        _infer_api_gateway_stages(
            region,
            api_ids,
            managed_resources,
            attribution,
        )

    # Inference pass 2: Lambda execution roles
    if lambda_function_names:
        _infer_lambda_execution_roles(
            region,
            lambda_function_names,
            managed_resources,
            attribution,
        )


def _infer_api_gateway_stages(region, api_ids, managed_resources, attribution):
    """Add API Gateway V2 stage ARNs for APIs found in the stack.

    Stage physical IDs in CFN are just the stage name (e.g. '$default')
    but the ARN needs the parent API ID. We resolve this by listing
    stages for each API.
    """
    try:
        apigw = boto3.client("apigatewayv2", region_name=region)
        for api_id in api_ids:
            try:
                resp = apigw.get_stages(ApiId=api_id)
                for stage in resp.get("Items", []):
                    stage_name = stage.get("StageName", "")
                    stage_arn = f"arn:aws:apigateway:{region}::/apis/{api_id}/stages/{stage_name}"
                    managed_resources[stage_arn] = attribution
            except Exception as e:
                print(f"WARNING: Failed to list stages for API {api_id}: {e}")
    except Exception as e:
        print(f"WARNING: API Gateway stage inference failed: {e}")


def _infer_lambda_execution_roles(region, function_names, managed_resources, attribution):
    """Add Lambda execution role ARNs for functions found in the stack.

    Serverless Framework (and sometimes CDK) creates execution roles
    outside the CloudFormation stack. By looking up each function's
    configuration, we can attribute these roles to the correct repo.
    """
    try:
        lam = boto3.client("lambda", region_name=region)
        for func_name in function_names:
            try:
                resp = lam.get_function(FunctionName=func_name)
                role_arn = resp.get("Configuration", {}).get("Role")
                if role_arn and role_arn not in managed_resources:
                    managed_resources[role_arn] = attribution
            except Exception as e:
                print(f"WARNING: Failed to get config for Lambda {func_name}: {e}")
    except Exception as e:
        print(f"WARNING: Lambda role inference failed: {e}")


def _count_stack_resources(cfn, stack_name):
    """Count resources in a stack (for unregistered stack reporting)."""
    try:
        count = 0
        paginator = cfn.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=stack_name):
            count += len(page.get("StackResourceSummaries", []))
        return count
    except Exception:
        return 0

"""Compliance data schema — shared between scanner and dashboard.

This file defines the data contract for the compliance report JSON.
Both Lambda functions import it at build time (copied into their
respective zip archives, together with the generated compliance_contract.py). External consumers of the S3 report can
also reference this module as the canonical field/type specification.

Cost semantics (three-state):
    float > 0  — scanner has a pricing model; this is the estimated monthly rate
    float == 0 — genuinely free (config-only resources: IAM, SGs, ACM, etc.)
    None       — scanner has NO pricing model (usage-based costs like CloudTrail,
                 GuardDuty, SNS, SQS) or insufficient data to estimate
"""

# The tag contract (required tags, accepted values, key names) is generated
# from contract/contract.yaml into compliance_contract.py and re-exported here.
# The REQUIRED_TAGS environment variable of a Lambda overrides REQUIRED_TAGS
# for one deployment.
from compliance_contract import (  # noqa: F401
    ALLOWED_VALUES,
    CONTRACT_VERSION,
    ENVIRONMENT_ALLOWED,
    EXPIRES_AT_PATTERN,
    EXPIRY_REQUIRED_LIFECYCLES,
    IAC_TYPES,
    LIFECYCLE_VALUES,
    OPTIONAL_TAGS,
    PATTERNS,
    PROTECTED_ENVIRONMENTS,
    REPOSITORY_PATTERN,
    REQUIRED_FACTS,
    REQUIRED_TAGS,
    RULES,
    TAG_KEYS,
    VALID_LIFECYCLE,
    VALID_MANAGED_BY,
    expires_at_is_valid,
    expiry_required,
    normalize_repository,
    parse_expires_at,
    same_repository,
)

SCHEMA_VERSION = "1.0"

# Severity levels (ordered high to low)
SEVERITIES = ["HIGH", "MEDIUM", "LOW"]
SEVERITY_ORDER = {s: i for i, s in enumerate(SEVERITIES)}  # {"HIGH": 0, ...}

# Resource states that should be excluded from burn rate and enforcement.
# Stopped/shutting-down are transitional but not actively costing (or about to stop).
INACTIVE_STATES = frozenset({"terminated", "stopped", "deleted", "deleting", "shutting-down"})

# Finding categories — keys used in the report JSON
FINDING_CATEGORIES = [
    "drift_detection",
    "tag_compliance",
    "schedule_compliance",
    "lifecycle_compliance",
]

# IaC registry schema version
REGISTRY_SCHEMA_VERSION = "1.0"

# Resource type → Cost Explorer service name mapping.
# Used by the dashboard burn page to correlate per-resource estimates
# with actual billed spend from Cost Explorer.
RTYPE_TO_CE_SERVICE = {
    # EC2 compute
    "ec2:instance": "Amazon Elastic Compute Cloud - Compute",
    # EBS, EIPs, snapshots
    "ec2:volume": "EC2 - Other",
    "ec2:eip": "EC2 - Other",
    "ec2:elastic-ip": "EC2 - Other",
    "ec2:snapshot": "EC2 - Other",
    # NAT, VPC
    "ec2:natgateway": "Amazon Virtual Private Cloud",
    "ec2:vpc": "Amazon Virtual Private Cloud",
    "ec2:vpc-endpoint": "Amazon Virtual Private Cloud",
    # Load balancers
    "elasticloadbalancing:loadbalancer": "Amazon Elastic Load Balancing",
    "elasticloadbalancing:targetgroup": "Amazon Elastic Load Balancing",
    # RDS
    "rds:db": "Amazon Relational Database Service",
    "rds:cluster": "Amazon Relational Database Service",
    # ECS / EKS
    "ecs:service": "Amazon Elastic Container Service",
    "ecs:cluster": "Amazon Elastic Container Service",
    "eks:pod": "Amazon Elastic Container Service for Kubernetes",
    # Storage and data
    "s3:bucket": "Amazon Simple Storage Service",
    "efs:filesystem": "Amazon Elastic File System",
    "dynamodb:table": "Amazon DynamoDB",
    # Cache
    "elasticache:cluster": "Amazon ElastiCache",
    # Compute (serverless)
    "lambda:function": "AWS Lambda",
    # Security and management
    "secretsmanager:secret": "AWS Secrets Manager",
    "kms:key": "AWS Key Management Service",
    "acm:certificate": "AWS Certificate Manager",
    # Networking
    "route53:hostedzone": "Amazon Route 53",
    # Monitoring
    "logs:log-group": "AmazonCloudWatch",
    "cloudwatch:alarm": "AmazonCloudWatch",
    # Containers
    "ecr:repository": "Amazon EC2 Container Registry (ECR)",
    # AI
    "bedrock:model": "Amazon Bedrock",
}

# ---------------------------------------------------------------------------
# Report validation (data contract)
# ---------------------------------------------------------------------------

# Top-level required keys and their expected types
_REPORT_REQUIRED = {
    "generated_at": str,
    "discovered_resources": list,
    "service_costs": dict,
    "summary": dict,
    "drift_detection": dict,
    "tag_compliance": dict,
    "schedule_compliance": dict,
    "lifecycle_compliance": dict,
}

# Per-resource required fields
_RESOURCE_REQUIRED = {
    "arn": (str,),
    "resource_type": (str,),
    "estimated_monthly_cost": (float, int, type(None)),  # three-state
}


def validate_report(data):
    """Validate a compliance report against the schema contract.

    Returns a list of issues (empty = valid). Does not raise.
    Useful as a scanner self-check before writing to S3, or for
    external tools that consume the report JSON.
    """
    issues = []
    for key, expected_type in _REPORT_REQUIRED.items():
        if key not in data:
            issues.append(f"missing top-level key: {key}")
        elif not isinstance(data[key], expected_type):
            issues.append(f"{key}: expected {expected_type.__name__}, got {type(data[key]).__name__}")
    for i, res in enumerate(data.get("discovered_resources", [])):
        for field, ftypes in _RESOURCE_REQUIRED.items():
            if field not in res:
                issues.append(f"resource[{i}] missing field: {field}")
            elif not isinstance(res[field], ftypes):
                issues.append(f"resource[{i}].{field}: unexpected type {type(res[field]).__name__}")
    for cat in FINDING_CATEGORIES:
        findings = data.get(cat, {})
        if isinstance(findings, dict):
            findings = findings.get("findings", [])
        for i, finding in enumerate(findings):
            if "severity" not in finding:
                issues.append(f"{cat}[{i}] missing severity")
            elif finding["severity"] not in SEVERITIES:
                issues.append(f"{cat}[{i}] invalid severity: {finding['severity']}")
    return issues


def require_env(name):
    """Return a required environment variable; stop with its name when it is unset or empty."""
    import os

    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Required environment variable {name} is not set. "
            "The Terraform module sets it from a required input; when running outside Lambda, export it first."
        )
    return value


def repo_slug(repo_url):
    """Return the last path segment of a repository URL, without a .git suffix."""
    slug = repo_url.rstrip("/").replace(":", "/").split("/")[-1]
    return slug[:-4] if slug.endswith(".git") else slug


# Every static price in the scanner and the dashboard is the on-demand rate in
# this one region. For a resource elsewhere, a region-dependent rate is not
# applied: its cost stays unknown (None).
PRICE_TABLE_REGION = "eu-central-1"  # oss-allow: the region the price tables describe

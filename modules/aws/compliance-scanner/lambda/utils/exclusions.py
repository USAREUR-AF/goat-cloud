"""
Exclusion Engine for Drift Detection

Three-layer exclusion system to minimize false positives:
  1. Hardcoded: auto-created resources (ENIs, service-linked roles, default VPC, etc.)
  2. Pattern-based: regex patterns for ARN prefixes (configurable)
  3. Explicit: specific ARNs to exclude (TF state bucket, lock table, etc.)
"""

import re

import boto3

# Resources that AWS creates automatically and should never be flagged as drift
HARDCODED_EXCLUSIONS = [
    # Service-linked IAM roles (AWS creates these under this path only; a
    # role elsewhere whose name merely starts with AWSServiceRole is not one)
    r"arn:aws:iam::\d+:role/aws-service-role/",
    # Default VPC resources
    r"arn:aws:ec2:[^:]+:\d+:vpc/vpc-[a-f0-9]+",  # Handled by is_default check
    # ECS task ENIs (ephemeral, created per task)
    r"arn:aws:ec2:[^:]+:\d+:network-interface/eni-",
    # CloudWatch automatic metrics and dashboards
    r"arn:aws:cloudwatch:[^:]+:\d+:alarm:TargetTracking",
    # ACM validation records
    r"arn:aws:acm:[^:]+:\d+:certificate/.+",  # ACM certs are often auto-renewed
    # Lambda ENIs (created for VPC-attached functions)
    r"arn:aws:ec2:[^:]+:\d+:network-interface/eni-",
    # CloudFormation stacks (may exist from CDK or console)
    r"arn:aws:cloudformation:",
    # AWS Config recorder and delivery channel
    r"arn:aws:config:",
    # GuardDuty detector
    r"arn:aws:guardduty:",
    # CloudTrail trails
    r"arn:aws:cloudtrail:",
    # SSM default parameters
    r"arn:aws:ssm:[^:]+:\d+:parameter/aws/",
    # SSM sessions (ECS execute-command, etc.) — ephemeral, not infra
    r"arn:aws:ssm:[^:]+:\d+:session/",
    # EKS pods — ephemeral workload metadata, not infrastructure
    r"arn:aws:eks:[^:]+:\d+:pod/",
    # KMS AWS-managed keys
    r"arn:aws:kms:[^:]+:\d+:key/aws/",
    # Container Insights log groups (auto-created by ECS, recreated if deleted)
    r"arn:aws:logs:[^:]+:\d+:log-group:/aws/ecs/containerinsights/",
    # AWS Backup recovery points (auto-created by backup plans, not click-ops)
    r"arn:aws:backup:[^:]+:\d+:recovery-point:",
    # Default security groups
    # (these are identified by name "default" in the scanner)
]

# Resource types that are typically auto-created, version-accumulated, or not worth flagging
AUTO_CREATED_TYPES = {
    "ec2:network-interface",  # ENIs from ECS/Lambda/NAT
    "ec2:route-table",  # Default route tables
    "ec2:subnet",  # When created by VPC wizard
    "ec2:internet-gateway",  # Default VPC
    "ec2:key-pair",  # Often created by TF but ARN format varies
    "ecs:task-definition",  # Old revisions accumulate in AWS — not click-ops
    "payments:payment-instrument",  # AWS-managed billing, not taggable or destroyable
}


class ExclusionEngine:
    """Manages the three-layer exclusion system."""

    def __init__(self, explicit_arns=None, patterns=None, tf_arns=None, account_id=""):
        # Resolve wildcards in explicit ARNs (e.g., * in account-ID position)
        resolved_arns = set()
        for arn in explicit_arns or []:
            if "*" in arn and account_id:
                resolved_arns.add(arn.replace("*", account_id))
            else:
                resolved_arns.add(arn)
        self.explicit_arns = resolved_arns
        self.tf_arns = tf_arns or set()
        self.custom_patterns = [re.compile(p) for p in (patterns or [])]
        self.hardcoded_patterns = [re.compile(p) for p in HARDCODED_EXCLUSIONS]
        self.override_arns = set()  # Populated from dashboard overrides table

    def load_overrides(self, overrides_table_name):
        """Load false positive/exemption overrides from the compliance dashboard DynamoDB table."""
        if not overrides_table_name:
            return

        try:
            dynamodb = boto3.resource("dynamodb")
            table = dynamodb.Table(overrides_table_name)
            response = table.scan()
            items = response.get("Items", [])
            while "LastEvaluatedKey" in response:
                response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
                items.extend(response.get("Items", []))

            for item in items:
                arn = item.get("resource_arn", "")
                if arn:
                    self.override_arns.add(arn)

            print(f"  Loaded {len(self.override_arns)} overrides from {overrides_table_name}")
        except Exception as e:
            print(f"  Warning: could not load overrides from {overrides_table_name}: {e}")

    def should_exclude(self, resource):
        """
        Check if a resource should be excluded from drift detection.

        Returns:
            tuple: (should_exclude: bool, reason: str or None)
        """
        arn = resource.get("arn", "")
        resource_type = resource.get("resource_type", "")
        name = resource.get("name", "")
        resource.get("tags", {})

        # Layer 0: Dashboard overrides (false positives / exemptions)
        if arn in self.override_arns:
            return True, "dashboard_override"

        # Layer 1: Explicit ARN exclusions
        if arn in self.explicit_arns:
            return True, "explicit_exclusion"

        # Also check if any explicit pattern is a prefix match
        for excluded_arn in self.explicit_arns:
            if arn.startswith(excluded_arn):
                return True, "explicit_exclusion"

        # Layer 2: Hardcoded auto-created resource patterns
        for pattern in self.hardcoded_patterns:
            if pattern.search(arn):
                return True, "auto_created"

        # Layer 3: Custom regex patterns
        for pattern in self.custom_patterns:
            if pattern.search(arn):
                return True, "custom_pattern"

        # Auto-created resource types
        if resource_type in AUTO_CREATED_TYPES:
            return True, "auto_created_type"

        # Default security groups (every VPC has one, AWS-created)
        if resource_type == "ec2:security-group" and name == "default":
            return True, "default_security_group"

        # Default VPCs
        if resource_type == "ec2:vpc" and resource.get("is_default"):
            return True, "default_vpc"

        # Terminated EC2 instances — AWS cleans them up within ~1 hour.
        # They linger in the tagging API briefly but are dead resources.
        if resource_type == "ec2:instance" and resource.get("state") == "terminated":
            return True, "terminated_instance"

        # EBS root volumes attached to EC2 instances managed by OUR Terraform state
        # are implicit sub-resources — the instance resource owns them. But root
        # volumes on externally-managed or unmanaged instances should still be
        # flagged (they're real resources that need compliance tracking).
        if resource_type == "ec2:volume" and resource.get("attached_to"):
            device = resource.get("attached_device", "")
            if device in ("/dev/sda1", "/dev/xvda", "/dev/sda", "/dev/nvme0n1"):
                instance_id = resource.get("attached_to")
                if instance_id:
                    # tf_arns contains full ARNs (arn:aws:ec2:...:instance/i-xxx)
                    # but attached_to is a bare instance ID (i-xxx). Check suffix.
                    instance_suffix = f"instance/{instance_id}"
                    if any(a.endswith(instance_suffix) for a in self.tf_arns):
                        return True, "root_volume"

        return False, None

    def classify_confidence(self, resource, in_terraform_state):
        """
        Assign confidence level to a drift finding.

        Returns:
            str: HIGH, MEDIUM, or LOW
        """
        tags = resource.get("tags", {})
        name = resource.get("name", "")
        resource_type = resource.get("resource_type", "")

        if in_terraform_state:
            return None  # Not drift

        # Resources tagged ManagedBy=terraform are likely managed by IaC but the ARN
        # didn't match state (e.g., Secrets Manager suffix mismatch). Downgrade to
        # MEDIUM — these are state-parsing gaps, not click-ops.
        if tags.get("ManagedBy", "").lower() == "terraform":
            return "MEDIUM"

        # HIGH: Has user-created tags or meaningful name, but not in TF state
        if tags.get("Name") or tags.get("Project") or tags.get("ManagedBy"):
            return "HIGH"

        # HIGH: Resource types that are almost never auto-created
        high_confidence_types = {
            "ec2:instance",
            "rds:instance",
            "ecs:cluster",
            "ecs:service",
            "elasticache:cluster",
            "elbv2:application",
            "elbv2:network",
            "lambda:function",
            "dynamodb:table",
            "efs:filesystem",
            "apigateway:api",
        }
        if resource_type in high_confidence_types:
            return "HIGH"

        # MEDIUM: Has a non-default name
        if name and name not in ("default", ""):
            return "MEDIUM"

        # LOW: Likely auto-created or AWS-managed
        return "LOW"

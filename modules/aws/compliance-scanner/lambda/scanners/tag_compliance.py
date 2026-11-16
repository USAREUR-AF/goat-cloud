"""
Tag Compliance Scanner

Checks all discovered resources for required tags.
Flags resources missing tags or with a ManagedBy, Lifecycle, Environment,
Repository or ExpiresAt value the contract does not accept. Key names,
accepted values and patterns come from the generated contract constants.
"""

import re

from compliance_schema import (
    ENVIRONMENT_ALLOWED,
    EXPIRES_AT_PATTERN,
    REPOSITORY_PATTERN,
    TAG_KEYS,
    VALID_LIFECYCLE,
    VALID_MANAGED_BY,
    expires_at_is_valid,
)

_MANAGED_BY = TAG_KEYS["managed_by"]
_LIFECYCLE = TAG_KEYS["lifecycle"]
_REPOSITORY = TAG_KEYS["repository"]
_PROJECT = TAG_KEYS["project"]
_ENVIRONMENT = TAG_KEYS["environment"]
_EXPIRES_AT = TAG_KEYS["expires_at"]
_REPOSITORY_RE = re.compile(REPOSITORY_PATTERN)


def scan_tag_compliance(resources, required_tags):
    """
    Check all resources for required tag compliance.

    Args:
        resources: List of resource dicts from discovery
        required_tags: List of required tag key names

    Returns:
        List of finding dicts
    """
    findings = []

    for resource in resources:
        arn = resource.get("arn", "")
        tags = resource.get("tags", {})
        resource_type = resource.get("resource_type", "")

        # Skip resource types that typically can't have tags
        if resource_type in _UNTAGGABLE_TYPES:
            continue

        # Skip child resources managed by a parent (ALB-allocated EIPs,
        # automated RDS snapshots, etc.) — compliance comes from the parent.
        if resource.get("attached_to"):
            continue

        # An expiry in the wrong form is reported whatever else the resource
        # carries: the lifecycle scan reads the value leniently, and a value it
        # cannot read at all would otherwise never expire and never be seen.
        _check_expires_at(findings, resource, arn, tags, resource_type)

        missing_tags = [t for t in required_tags if t not in tags]

        if not missing_tags:
            # All required tags present — validate tag values
            _check_managed_by(findings, resource, arn, tags, resource_type)
            _check_lifecycle(findings, resource, arn, tags, resource_type)
            _check_environment(findings, resource, arn, tags, resource_type)
            _check_repository(findings, resource, arn, tags, resource_type)
            continue

        # Determine severity based on how many tags are missing
        if len(missing_tags) == len(required_tags):
            severity = "HIGH"  # No required tags at all
        elif _MANAGED_BY in missing_tags or _PROJECT in missing_tags:
            severity = "HIGH"  # Missing critical tags
        else:
            severity = "MEDIUM"

        findings.append(
            {
                "category": "tag_compliance",
                "severity": severity,
                "resource_arn": arn,
                "resource_type": resource_type,
                "resource_name": resource.get("name", ""),
                "region": resource.get("region", ""),
                "state": resource.get("state"),
                "issue": f"Missing required tags: {', '.join(missing_tags)}",
                "missing_tags": missing_tags,
                "existing_tags": tags,
                "created_at": resource.get("created_at"),
                "created_by": resource.get("created_by"),
                "running_duration_days": resource.get("running_duration_days"),
                "estimated_monthly_cost": resource.get("estimated_monthly_cost"),
                "inferred_purpose": resource.get("inferred_purpose"),
                "attached_to": resource.get("attached_to"),
                "recommendation": f"Add missing tags: {', '.join(missing_tags)}",
            }
        )

    return findings


def _check_managed_by(findings, resource, arn, tags, resource_type):
    """Flag resources where ManagedBy is not a recognized IaC tool.

    Accepted values are VALID_MANAGED_BY, generated from the contract. The
    value compares without regard to case: Terraform and terraform are the
    same tool.
    """
    managed_by = tags.get(_MANAGED_BY, "")
    if not managed_by or managed_by.lower() in {v.lower() for v in VALID_MANAGED_BY}:
        return

    valid_list = ", ".join(sorted(VALID_MANAGED_BY))
    findings.append(
        _make_finding(
            resource,
            arn,
            tags,
            resource_type,
            severity="MEDIUM",
            issue=f"{_MANAGED_BY} tag is '{managed_by}' (expected one of: {valid_list})",
            recommendation=f"Set {_MANAGED_BY} to the IaC tool that manages this resource (e.g. 'terraform', 'serverless', 'pulumi').",
        )
    )


def _check_lifecycle(findings, resource, arn, tags, resource_type):
    """Flag resources where Lifecycle is not a value the contract accepts."""
    lifecycle = tags.get(_LIFECYCLE, "")
    if not lifecycle or lifecycle in VALID_LIFECYCLE:
        return

    valid_list = ", ".join(sorted(VALID_LIFECYCLE))
    findings.append(
        _make_finding(
            resource,
            arn,
            tags,
            resource_type,
            severity="MEDIUM",
            issue=f"{_LIFECYCLE} tag is '{lifecycle}' (expected one of: {valid_list})",
            recommendation=f"Set {_LIFECYCLE} to one of: {valid_list}.",
        )
    )


def _check_environment(findings, resource, arn, tags, resource_type):
    """Flag an Environment value outside the contract's list, when the contract has one."""
    environment = tags.get(_ENVIRONMENT, "")
    if not ENVIRONMENT_ALLOWED or not environment or environment in ENVIRONMENT_ALLOWED:
        return

    valid_list = ", ".join(ENVIRONMENT_ALLOWED)
    findings.append(
        _make_finding(
            resource,
            arn,
            tags,
            resource_type,
            severity="MEDIUM",
            issue=f"{_ENVIRONMENT} tag is '{environment}' (expected one of: {valid_list})",
            recommendation=f"Set {_ENVIRONMENT} to one of: {valid_list}.",
        )
    )


def _check_expires_at(findings, resource, arn, tags, resource_type):
    """Flag an ExpiresAt value that is not in the contract's form."""
    expires_at = tags.get(_EXPIRES_AT, "")
    if not expires_at or expires_at_is_valid(expires_at):
        return

    findings.append(
        _make_finding(
            resource,
            arn,
            tags,
            resource_type,
            severity="MEDIUM",
            issue=f"{_EXPIRES_AT} tag '{expires_at}' is not in the contract's form (expected to match {EXPIRES_AT_PATTERN})",
            recommendation=f"Set {_EXPIRES_AT} to a UTC timestamp ending in Z, for example 2030-01-31T00:00:00Z.",
        )
    )


def _check_repository(findings, resource, arn, tags, resource_type):
    """Validate the Repository tag contains a valid repository URL."""
    repo = tags.get(_REPOSITORY, "")
    if not repo:
        return

    # Must match the contract's repository pattern (an https:// or git@ URL)
    if not _REPOSITORY_RE.match(repo):
        findings.append(
            _make_finding(
                resource,
                arn,
                tags,
                resource_type,
                severity="MEDIUM",
                issue=f"{_REPOSITORY} tag '{repo}' is not a valid URL (expected to match {REPOSITORY_PATTERN})",
                recommendation=f"Set {_REPOSITORY} to the full git repo URL, e.g. 'https://github.com/your-org/your-repo'.",
            )
        )
        return
    # The URL is checked for form only. The scanner makes no request to it: a
    # tag value must never choose a network destination for the scanner.


def _make_finding(resource, arn, tags, resource_type, severity, issue, recommendation):
    """Build a standard tag compliance finding dict."""
    return {
        "category": "tag_compliance",
        "severity": severity,
        "resource_arn": arn,
        "resource_type": resource_type,
        "resource_name": resource.get("name", ""),
        "region": resource.get("region", ""),
        "state": resource.get("state"),
        "issue": issue,
        "tags": tags,
        "created_at": resource.get("created_at"),
        "created_by": resource.get("created_by"),
        "running_duration_days": resource.get("running_duration_days"),
        "estimated_monthly_cost": resource.get("estimated_monthly_cost"),
        "inferred_purpose": resource.get("inferred_purpose"),
        "attached_to": resource.get("attached_to"),
        "recommendation": recommendation,
    }


# Resource types that typically don't support tags or aren't worth checking
_UNTAGGABLE_TYPES = {
    "ec2:route-table",
    "ec2:network-interface",
    "ec2:internet-gateway",
    "route53:hostedzone",
    "iam:group",  # AWS IAM groups do not support tags
    "payments:payment-instrument",  # AWS-managed billing, not taggable
}

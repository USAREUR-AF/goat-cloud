"""
Lifecycle Compliance Scanner

Flags resources that:
  - Have expired (past ExpiresAt date)
  - Are expiring soon (within 14 days)
  - Have a lifecycle that requires an expiry (temporary, experiment) and none
  - Have been running longer than expected without lifecycle metadata

An ExpiresAt value is read leniently: a date alone, or a timestamp without a
zone, is taken as UTC, so a value in the wrong form still expires. The wrong
form itself is a tag_compliance finding (scanners/tag_compliance.py). A value
that names no moment at all counts as no expiry.
"""

from datetime import UTC, datetime

from compliance_schema import TAG_KEYS, expiry_required, parse_expires_at

_EXPIRING_SOON_DAYS = 14
_LIFECYCLE = TAG_KEYS["lifecycle"]
_EXPIRES_AT = TAG_KEYS["expires_at"]


def scan_lifecycle_compliance(resources):
    """
    Check all resources for lifecycle tag compliance.

    Args:
        resources: List of resource dicts from discovery

    Returns:
        List of finding dicts
    """
    findings = []
    now = datetime.now(UTC)

    for resource in resources:
        tags = resource.get("tags", {})
        resource_type = resource.get("resource_type", "")

        # Skip resource types that don't need lifecycle management
        if resource_type in _EXEMPT_TYPES:
            continue

        lifecycle = tags.get(_LIFECYCLE, "")
        expires_at = tags.get(_EXPIRES_AT, "")
        expiry = parse_expires_at(expires_at)

        # Check 1: Expired resources
        if expiry is not None:
            if expiry < now:
                days_expired = (now - expiry).days
                findings.append(
                    _finding(
                        resource,
                        severity="HIGH",
                        issue=f"Resource expired {days_expired} days ago ({_EXPIRES_AT}: {expires_at})",
                        recommendation=f"Delete this resource or update {_EXPIRES_AT} to extend its lifecycle.",
                    )
                )
                continue

            # Check 1b: Expiring soon (within threshold)
            days_remaining = (expiry - now).days
            if days_remaining <= _EXPIRING_SOON_DAYS:
                findings.append(
                    _finding(
                        resource,
                        severity="INFO",
                        issue=f"Resource expires in {days_remaining} day{'s' if days_remaining != 1 else ''} ({_EXPIRES_AT}: {expires_at})",
                        recommendation=f"Extend {_EXPIRES_AT} if still needed, or plan for teardown.",
                    )
                )
                continue

        # Check 2: A lifecycle that requires an expiry, with no readable expiry
        if expiry_required(lifecycle) and expiry is None:
            duration = resource.get("running_duration_days")
            if duration and duration > 30:
                kind = lifecycle.capitalize()
                findings.append(
                    _finding(
                        resource,
                        severity="MEDIUM",
                        issue=f"{kind} resource running for {duration} days without {_EXPIRES_AT} tag",
                        recommendation=f"Add an {_EXPIRES_AT} tag or change {_LIFECYCLE} to persistent if this resource is needed long-term.",
                    )
                )
                continue

        # Check 3: Long-running resources with no Lifecycle tag at all
        if not lifecycle and resource_type in _COSTLY_TYPES:
            duration = resource.get("running_duration_days")
            cost = resource.get("estimated_monthly_cost")

            if duration and duration > 60:
                cost_str = f" (est. ${cost:.2f}/mo)" if cost is not None else ""
                findings.append(
                    _finding(
                        resource,
                        severity="LOW",
                        issue=f"Resource running for {duration} days without {_LIFECYCLE} tag{cost_str}",
                        recommendation=f"Add {_LIFECYCLE}=persistent if intentional, or {_LIFECYCLE}=temporary with {_EXPIRES_AT} if short-lived.",
                    )
                )

    return findings


def _finding(resource, severity, issue, recommendation):
    """Build a standardized finding dict."""
    return {
        "category": "lifecycle_compliance",
        "severity": severity,
        "resource_arn": resource.get("arn", ""),
        "resource_type": resource.get("resource_type", ""),
        "resource_name": resource.get("name", ""),
        "region": resource.get("region", ""),
        "issue": issue,
        "tags": resource.get("tags", {}),
        "created_at": resource.get("created_at"),
        "created_by": resource.get("created_by"),
        "running_duration_days": resource.get("running_duration_days"),
        "estimated_monthly_cost": resource.get("estimated_monthly_cost"),
        "inferred_purpose": resource.get("inferred_purpose"),
        "recommendation": recommendation,
    }


# Resource types exempt from lifecycle checking
_EXEMPT_TYPES = {
    "iam:role",
    "iam:user",
    "route53:hostedzone",
    "acm:certificate",
    "ec2:vpc",
    "ec2:security-group",
    "ec2:route-table",
    "ec2:network-interface",
    "ec2:internet-gateway",
    "ec2:subnet",
    "sns:topic",
    "events:rule",
    "ssm:parameter",
    "logs:log-group",
    "kms:key",
    "payments:payment-instrument",  # AWS-managed billing, not taggable
}

# Resource types that have ongoing cost and should have lifecycle tags
_COSTLY_TYPES = {
    "ec2:instance",
    "rds:instance",
    "ecs:service",
    "elasticache:cluster",
    "ec2:volume",
    "ec2:natgateway",
    "elbv2:application",
    "elbv2:network",
    "efs:filesystem",
}

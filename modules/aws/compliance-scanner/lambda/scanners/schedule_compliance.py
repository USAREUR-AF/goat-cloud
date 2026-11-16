"""
Schedule Compliance Scanner

Flags long-running resources (EC2, ECS, RDS) that lack schedule tags
and could be stopped during off-hours to save costs.
Respects Lifecycle=persistent.

The contract's schedule tag satisfies the check. The earlier tag names
(LEGACY_SCHEDULE_KEYS, compared without regard to case) still satisfy it and
produce a LOW finding that names the contract's key.
"""

from compliance_schema import TAG_KEYS, expiry_required, parse_expires_at

_LIFECYCLE = TAG_KEYS["lifecycle"]
_EXPIRES_AT = TAG_KEYS["expires_at"]
_SCHEDULE = TAG_KEYS["schedule"]

# Tag names accepted before the contract named one key.
LEGACY_SCHEDULE_KEYS = ("schedule", "autostop", "autoshutdown", "businesshours", "offhours")

# Resource types that could benefit from scheduling
SCHEDULABLE_TYPES = {
    "ec2:instance",
    "rds:instance",
    "ecs:service",
    "elasticache:cluster",
}


def scan_schedule_compliance(resources):
    """
    Check schedulable resources for off-hours management tags.

    Args:
        resources: List of resource dicts from discovery

    Returns:
        List of finding dicts
    """
    findings = []

    for resource in resources:
        resource_type = resource.get("resource_type", "")

        if resource_type not in SCHEDULABLE_TYPES:
            continue

        tags = resource.get("tags", {})
        lifecycle = tags.get(_LIFECYCLE, "").lower()

        # Persistent resources are explicitly opted out of scheduling
        if lifecycle == "persistent":
            continue

        # Resources whose lifecycle carries an expiry (temporary, experiment)
        # are lifecycle-managed: scheduling is irrelevant since they will be
        # torn down by the reaper.
        if expiry_required(lifecycle) and parse_expires_at(tags.get(_EXPIRES_AT, "")) is not None:
            continue

        # Skip stopped/inactive resources
        state = resource.get("state", "")
        if state in ("stopped", "terminated", "deleted", "inactive"):
            continue

        # The contract's schedule tag satisfies the check.
        if tags.get(_SCHEDULE):
            continue

        cost = resource.get("estimated_monthly_cost")
        duration = resource.get("running_duration_days")

        # An earlier tag name also satisfies it, and is reported so the tag
        # moves to the contract's key.
        legacy = sorted(k for k in tags if k != _SCHEDULE and k.lower() in LEGACY_SCHEDULE_KEYS)
        if legacy:
            findings.append(
                {
                    "category": "schedule_compliance",
                    "severity": "LOW",
                    "resource_arn": resource.get("arn", ""),
                    "resource_type": resource_type,
                    "resource_name": resource.get("name", ""),
                    "region": resource.get("region", ""),
                    "issue": f"Schedule is stated under the tag {', '.join(legacy)}; the contract's key is {_SCHEDULE}",
                    "tags": tags,
                    "created_at": resource.get("created_at"),
                    "created_by": resource.get("created_by"),
                    "running_duration_days": duration,
                    "estimated_monthly_cost": cost,
                    "inferred_purpose": resource.get("inferred_purpose"),
                    "recommendation": f"Move the value to a {_SCHEDULE} tag and remove {', '.join(legacy)}.",
                }
            )
            continue

        # Determine cost impact
        severity = "MEDIUM" if (cost is not None and cost > 10) else "LOW"

        cost_str = f"${cost:.2f}/mo" if cost is not None else "unknown"
        duration_str = f"{duration} days" if duration else "unknown"

        findings.append(
            {
                "category": "schedule_compliance",
                "severity": severity,
                "resource_arn": resource.get("arn", ""),
                "resource_type": resource_type,
                "resource_name": resource.get("name", ""),
                "region": resource.get("region", ""),
                "issue": f"Schedulable resource running without schedule tags (cost: {cost_str}, running: {duration_str})",
                "tags": tags,
                "created_at": resource.get("created_at"),
                "created_by": resource.get("created_by"),
                "running_duration_days": duration,
                "estimated_monthly_cost": cost,
                "inferred_purpose": resource.get("inferred_purpose"),
                "recommendation": (
                    f"Add a {_SCHEDULE} tag (e.g., 'businesshours') to enable off-hours shutdown, "
                    f"or set {_LIFECYCLE}=persistent to opt out of scheduling."
                ),
            }
        )

    return findings

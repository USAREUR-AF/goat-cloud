"""
Data layer — Protected resources and stopped EC2 cost estimation.

Auto-discovers S3 buckets and TF lock tables from the compliance report
so they don't need to be manually listed in SSM.
"""

import json
import os
import re

import boto3
from compliance_schema import PRICE_TABLE_REGION
from data_core import PROTECTED_RESOURCES_SSM
from data_reports import get_compliance_report, get_latest_report_date

_EBS_COST_PER_GB = 0.0952  # gp3, PRICE_TABLE_REGION

# Patterns that indicate a bucket holds Terraform state
_TF_STATE_BUCKET_RE = re.compile(r"terraform|tf-state|tfstate", re.IGNORECASE)

# Patterns that indicate a DynamoDB table is a Terraform lock table
_TF_LOCK_TABLE_RE = re.compile(r"tf-lock|terraform-lock|tfstate|terraform.*lock|lock.*terraform", re.IGNORECASE)


def _estimate_stopped_ec2_cost(instance_arn, report):
    """Estimate monthly storage cost for a stopped EC2 instance.

    Stopped instances don't incur compute charges but still pay for attached
    EBS volumes. Sum the volume sizes attached to this instance.
    """
    if not report:
        return 0
    instance_id = instance_arn.rsplit("/", 1)[-1] if "/" in instance_arn else ""
    if not instance_id:
        return 0
    arn_parts = instance_arn.split(":")
    if len(arn_parts) > 3 and arn_parts[3] != PRICE_TABLE_REGION:
        return 0  # no storage rate for this region
    total_gb = 0
    for r in report.get("discovered_resources", []):
        if r.get("resource_type") == "ec2:volume" and r.get("attached_to") == instance_id:
            total_gb += float(r.get("size_gb") or 0)
    if total_gb == 0:
        total_gb = 30  # conservative default if no volumes found
    return round(total_gb * _EBS_COST_PER_GB, 2)


def _auto_discover_protected(report):
    """Discover S3 buckets and TF lock tables from the compliance report.

    Returns a list of synthetic protected-resource entries with
    auto_discovered=True so the UI can distinguish them.
    """
    if not report:
        return []

    entries = []
    for r in report.get("discovered_resources", []):
        rtype = r.get("resource_type", "")
        arn = r.get("arn", "")
        name = r.get("name", "")

        if rtype == "s3:bucket":
            is_tf = bool(_TF_STATE_BUCKET_RE.search(name))
            entries.append(
                {
                    "arn_pattern": arn,
                    "category": "terraform_state" if is_tf else "data",
                    "name": name,
                    "reason": "Terraform state bucket" if is_tf else "S3 data — deletion is irreversible",
                    "action": "never_destroy",
                    "auto_discovered": True,
                }
            )

        elif rtype == "dynamodb:table" and _TF_LOCK_TABLE_RE.search(name):
            entries.append(
                {
                    "arn_pattern": arn,
                    "category": "terraform_state",
                    "name": name,
                    "reason": "Terraform lock table",
                    "action": "never_destroy",
                    "auto_discovered": True,
                }
            )

    return entries


def get_protected_resources(report=None):
    """Load protected resources and auto-discover S3 + TF lock tables.

    Combines manual SSM config entries with auto-discovered resources
    from the compliance report. SSM entries take precedence if the same
    ARN appears in both.

    Returns dict with:
      - config: the raw protected resources config (resources + notes)
      - resources: list of enriched resource entries with compliance status
      - summary: counts by category, compliant/non-compliant, total cost
    """
    config = _load_protected_config() or {"resources": [], "notes": []}

    if report is None:
        date = get_latest_report_date()
        report = get_compliance_report(date) if date else None

    # Merge SSM entries + auto-discovered (SSM wins on duplicate ARNs)
    ssm_entries = config.get("resources", [])
    auto_entries = _auto_discover_protected(report)

    ssm_arns = {e.get("arn_pattern", "") for e in ssm_entries}
    merged = list(ssm_entries)
    for entry in auto_entries:
        if entry["arn_pattern"] not in ssm_arns:
            merged.append(entry)

    # Gather all findings for cross-referencing
    all_findings = []
    if report:
        for section in ("tag_compliance", "drift_detection", "schedule_compliance", "lifecycle_compliance"):
            all_findings.extend(report.get(section, {}).get("findings", []))

    # Resolve account ID for wildcard matching
    account_id = os.environ.get("ACCOUNT_ID", "")

    enriched = []
    by_category = {}
    total_cost = 0
    compliant_count = 0
    noncompliant_count = 0

    for entry in merged:
        pattern = entry.get("arn_pattern", "")
        resolved = pattern.replace("*", account_id) if "*" in pattern and account_id else pattern

        # Find matching findings
        matching = [
            f
            for f in all_findings
            if f.get("resource_arn") == resolved or f.get("resource_arn", "").startswith(resolved)
        ]

        # Find matching discovered resource for cost/state
        cost = 0
        state = ""
        resource_type = ""
        region = ""
        if report:
            for r in report.get("discovered_resources", []):
                r_arn = r.get("arn", "")
                if r_arn == resolved or r_arn.startswith(resolved):
                    raw_cost = r.get("estimated_monthly_cost")
                    cost = float(raw_cost) if raw_cost is not None else None
                    state = r.get("state", "")
                    resource_type = r.get("resource_type", "")
                    region = r.get("region", "")
                    break

        # Fallback cost estimation for resources the scanner underprices:
        # 1. Stopped EC2 instances: scanner reports $0 but EBS storage still costs
        # 2. Config-provided estimate: for resources scanner can't discover (snapshots)
        if (cost is not None and cost == 0) and state == "stopped" and resource_type == "ec2:instance":
            cost = _estimate_stopped_ec2_cost(resolved, report)
        if (cost is None or cost == 0) and entry.get("estimated_cost"):
            cost = float(entry["estimated_cost"])

        is_compliant = len(matching) == 0
        if is_compliant:
            compliant_count += 1
        else:
            noncompliant_count += 1
        if cost is not None:
            total_cost += cost

        cat = entry.get("category", "unknown")
        by_category[cat] = by_category.get(cat, 0) + 1

        enriched.append(
            {
                **entry,
                "resolved_arn": resolved,
                "is_compliant": is_compliant,
                "findings": matching,
                "finding_count": len(matching),
                "estimated_monthly_cost": cost,
                "state": state,
                "resource_type": resource_type,
                "region": region,
            }
        )

    return {
        "config": config,
        "resources": enriched,
        "summary": {
            "total": len(enriched),
            "compliant": compliant_count,
            "noncompliant": noncompliant_count,
            "total_cost": total_cost,
            "by_category": by_category,
        },
    }


def _load_protected_config():
    """Load protected resources config from SSM Parameter Store (no caching)."""
    if not PROTECTED_RESOURCES_SSM:
        return None
    try:
        ssm = boto3.client("ssm")
        resp = ssm.get_parameter(Name=PROTECTED_RESOURCES_SSM)
        return json.loads(resp["Parameter"]["Value"])
    except Exception as e:
        print(f"Warning: could not load protected resources from SSM: {e}")
        return None


def is_protected_arn(arn, config=None):
    """Check if an ARN matches any protected resource pattern or auto-protected type.

    Used by views to add PROTECTED badge to findings.
    All S3 buckets and TF lock tables are auto-protected.
    """
    # Auto-protect all S3 buckets
    if ":s3:::" in arn:
        return True

    # SSM pattern matching
    if config is None:
        config = _load_protected_config()
    if config:
        account_id = os.environ.get("ACCOUNT_ID", "")
        for entry in config.get("resources", []):
            pattern = entry.get("arn_pattern", "")
            if not pattern:
                continue
            resolved = pattern.replace("*", account_id) if "*" in pattern and account_id else pattern
            if arn == resolved or arn.startswith(resolved):
                return True

    return False

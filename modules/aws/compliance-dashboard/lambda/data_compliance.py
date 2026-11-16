"""
Data layer — findings, HK state, overrides, and compliance summaries.
"""

from datetime import UTC, datetime, timedelta

from data_core import (
    _CACHE_TTL_HK,
    _CACHE_TTL_OVERRIDES,
    HK_STATE_TABLE,
    OVERRIDES_TABLE,
    _cache_get,
    _cache_set,
    dynamodb,
)
from data_reports import get_compliance_report


def get_findings_summary(report):
    """Extract summary stats from a compliance report."""
    if not report:
        return {"total": 0, "high": 0, "medium": 0, "low": 0, "resources": 0}

    summary = report.get("summary", {})
    by_sev = summary.get("findings_by_severity", {})
    return {
        "total": sum(by_sev.values()),
        "high": by_sev.get("HIGH", 0),
        "medium": by_sev.get("MEDIUM", 0),
        "low": by_sev.get("LOW", 0),
        "resources": summary.get("total_resources_discovered", summary.get("total_resources", 0)),
    }


def get_high_findings(report):
    """Extract all HIGH severity findings from a report."""
    if not report:
        return []

    findings = []
    for category in ["drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"]:
        cat_data = report.get(category, {})
        cat_findings = cat_data.get("findings", [])
        for f in cat_findings:
            if f.get("severity") == "HIGH":
                findings.append(f)
    return findings


def get_all_findings(report, severity_filter=None, include_info=False):
    """Extract all findings from a report, optionally filtered by severity.

    INFO findings are excluded by default because they are informational
    (e.g. expiring-soon) and do not represent non-compliance.
    """
    if not report:
        return []

    findings = []
    for category in ["drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"]:
        cat_data = report.get(category, {})
        cat_findings = cat_data.get("findings", [])
        for f in cat_findings:
            if severity_filter and f.get("severity") != severity_filter:
                continue
            if not include_info and f.get("severity") == "INFO":
                continue
            findings.append(f)
    return findings


_TYPE_LABELS = {
    "ec2": "EC2",
    "rds": "RDS",
    "lambda": "Lambda",
    "s3": "S3",
    "sagemaker": "SageMaker",
    "ecs": "ECS",
    "dynamodb": "DynamoDB",
    "elasticache": "ElastiCache",
    "elbv2": "ALB/NLB",
    "secretsmanager": "Secrets Manager",
    "efs": "EFS",
    "kms": "KMS",
    "sns": "SNS",
    "sqs": "SQS",
    "apigateway": "API Gateway",
    "ecr": "ECR",
    "iam": "IAM",
}


def get_unmanaged_resources(report=None):
    """Extract unmanaged resources from drift findings, grouped for display.

    Groups by:
      1. ArchZone=ad-hoc tag -> "ad-hoc" group (user-tagged experiments)
      2. Purpose tag -> group by purpose value
      3. Lifecycle=temporary -> "temporary" group
      4. Fallback -> group by AWS service prefix (ec2, lambda, etc.)

    Returns:
        dict with "groups" (list of group dicts) and "total" count.
        Each group: {"key", "label", "resources": [...], "total_cost"}
        Each resource: {arn, name, resource_type, region, confidence, created_by,
                        created_at, running_duration_days, estimated_monthly_cost,
                        tags, recommendation}
    """
    if not report:
        report = get_compliance_report()
    if not report:
        return {"groups": [], "total": 0, "total_cost": 0}

    drift = report.get("drift_detection", {})
    findings = drift.get("findings", [])

    # Completely hidden — infrastructure noise, not user-facing sprawl.
    _HIDDEN_TYPES = {
        "kms:key",  # KMS — AWS-managed or TF-managed
        "logs:log-group",  # CloudWatch log groups — auto-created
        "events:rule",  # EventBridge rules — auto-created
        "ec2:security-group",  # Security groups — not user-facing
        "ec2:volume",  # EBS volumes — sub-resources of instances
        "route53:record",  # DNS records — infrastructure detail
    }

    # Shown but with name redacted — contain PII, secrets, IPs, or financial data.
    _REDACTED_TYPES = {
        "iam:user",
        "iam:role",
        "iam:policy",
        "secretsmanager:secret",
        "ssm:parameter",
        "payments:payment-instrument",
        "ec2:eip",
    }

    # Only show HIGH and MEDIUM confidence — LOW are auto-created AWS noise
    groups = {}
    for f in findings:
        confidence = f.get("confidence", f.get("severity", "LOW"))
        if confidence not in ("HIGH", "MEDIUM"):
            continue

        tags = f.get("tags", {}) or {}
        resource_type = f.get("resource_type", "")

        if resource_type in _HIDDEN_TYPES:
            continue

        redact = resource_type in _REDACTED_TYPES

        # Determine group
        if tags.get("ArchZone", "").lower() == "ad-hoc":
            key = "ad-hoc"
            label = "Ad-Hoc / Experiments"
        elif tags.get("Purpose"):
            purpose = tags["Purpose"].lower().replace(" ", "-")
            key = f"purpose:{purpose}"
            label = tags["Purpose"].title()
        elif tags.get("Lifecycle", "").lower() == "temporary":
            key = "temporary"
            label = "Temporary"
        else:
            svc_prefix = resource_type.split(":")[0] if ":" in resource_type else resource_type
            key = f"aws:{svc_prefix}"
            label = _TYPE_LABELS.get(svc_prefix, svc_prefix.upper())

        if key not in groups:
            groups[key] = {"key": key, "label": label, "resources": [], "total_cost": 0}

        cost = f.get("estimated_monthly_cost")  # None = no pricing model
        display_name = "[redacted]" if redact else f.get("resource_name", "")
        groups[key]["resources"].append(
            {
                "arn": "[redacted]" if redact else f.get("resource_arn", ""),
                "name": display_name,
                "resource_type": resource_type,
                "region": f.get("region", ""),
                "confidence": confidence,
                "created_by": "",
                "created_at": f.get("created_at", ""),
                "running_duration_days": f.get("running_duration_days"),
                "estimated_monthly_cost": cost,
                "tags": tags,
                "recommendation": f.get("recommendation", ""),
            }
        )
        groups[key]["total_cost"] += cost if cost is not None else 0

    # Sort groups: ad-hoc first, then purpose/temporary, then by resource count desc
    group_order = {"ad-hoc": 0, "temporary": 1}
    sorted_groups = sorted(
        groups.values(),
        key=lambda g: (
            group_order.get(g["key"], 2 if g["key"].startswith("purpose:") else 3),
            -len(g["resources"]),
        ),
    )

    total = sum(len(g["resources"]) for g in sorted_groups)
    total_cost = sum(g["total_cost"] for g in sorted_groups)
    return {"groups": sorted_groups, "total": total, "total_cost": total_cost}


def get_compliant_summary(report):
    """Derive compliant resource counts from total discovered minus non-compliant.

    Returns dict with total_resources, total_compliant, by_type, by_region.
    """
    if not report:
        return {"total_resources": 0, "total_compliant": 0, "by_type": {}, "by_region": {}}

    summary = report.get("summary", {})
    total_resources = summary.get("total_resources_discovered", summary.get("total_resources", 0))
    type_counts = report.get("resource_type_counts", {})
    region_type_counts = report.get("region_type_counts", {})

    # Count non-compliant resources by ARN (deduplicate across categories).
    # INFO findings are informational (e.g. expiring-soon) and don't count
    # as non-compliant.
    nc_arns = set()
    nc_by_type = {}
    nc_by_region = {}
    for category in ["drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"]:
        for f in report.get(category, {}).get("findings", []):
            if f.get("severity") == "INFO":
                continue
            arn = f.get("resource_arn", "")
            if arn in nc_arns:
                continue
            nc_arns.add(arn)
            rtype = f.get("resource_type", "unknown")
            region = f.get("region", "global")
            nc_by_type[rtype] = nc_by_type.get(rtype, 0) + 1
            nc_by_region[region] = nc_by_region.get(region, 0) + 1

    total_nc = len(nc_arns)
    total_compliant = max(0, total_resources - total_nc)

    # By type
    by_type = {}
    for rtype, total in type_counts.items():
        nc = nc_by_type.get(rtype, 0)
        by_type[rtype] = {"total": total, "compliant": max(0, total - nc)}

    # By region
    by_region = {}
    for region, types in region_type_counts.items():
        total = sum(types.values())
        nc = nc_by_region.get(region, 0)
        by_region[region] = {"total": total, "compliant": max(0, total - nc)}

    return {
        "total_resources": total_resources,
        "total_compliant": total_compliant,
        "by_type": by_type,
        "by_region": by_region,
    }


_EXPIRING_SOON_DAYS = 14


def get_expiring_soon(report):
    """Find resources with ExpiresAt within the next 14 days.

    Scans discovered_resources for tags with a future ExpiresAt that falls
    within the threshold.  Returns a list of dicts sorted by expiry date
    (soonest first).
    """
    if not report:
        return []

    now = datetime.now(UTC)
    threshold = now + timedelta(days=_EXPIRING_SOON_DAYS)
    expiring = []

    for r in report.get("discovered_resources", []):
        expires_at = r.get("tags", {}).get("ExpiresAt", "")
        if not expires_at:
            continue
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if now < expiry <= threshold:
            days_remaining = (expiry - now).days
            expiring.append(
                {
                    "arn": r.get("arn", ""),
                    "name": r.get("name", ""),
                    "resource_type": r.get("resource_type", ""),
                    "region": r.get("region", "global"),
                    "expires_at": expires_at,
                    "days_remaining": days_remaining,
                    "estimated_monthly_cost": r.get("estimated_monthly_cost"),
                    "tags": r.get("tags", {}),
                }
            )

    expiring.sort(key=lambda x: x["days_remaining"])
    return expiring


def get_pending_destroys():
    """Get all resources with pending 24-hour destroys from hunterkiller state.

    Cached 30s — invalidated on approve/abort/extend actions.
    """
    if not HK_STATE_TABLE:
        return []

    cached = _cache_get("pending_destroys")
    if cached is not None:
        return cached

    table = dynamodb.Table(HK_STATE_TABLE)
    try:
        response = table.scan()
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            items.extend(response.get("Items", []))

        # Filter to warned status only (pending enforcement)
        pending = [item for item in items if item.get("status") == "warned"]
        # Sort by grace_deadline (soonest first)
        pending.sort(key=lambda x: x.get("grace_deadline", ""))
        _cache_set("pending_destroys", pending, _CACHE_TTL_HK)
        return pending
    except Exception as e:
        print(f"Error scanning HK state table: {e}")
        return []


def get_hk_record(resource_arn):
    """Get a single record from the hunterkiller state table."""
    if not HK_STATE_TABLE:
        return None

    table = dynamodb.Table(HK_STATE_TABLE)
    try:
        response = table.get_item(Key={"resource_arn": resource_arn})
        return response.get("Item")
    except Exception as e:
        print(f"Error getting HK record for {resource_arn}: {e}")
        return None


def get_all_hk_records():
    """Get all records from hunterkiller state table.

    Cached 30s — invalidated on approve/abort/extend actions.
    """
    if not HK_STATE_TABLE:
        return []

    cached = _cache_get("hk_records")
    if cached is not None:
        return cached

    table = dynamodb.Table(HK_STATE_TABLE)
    try:
        response = table.scan()
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            items.extend(response.get("Items", []))
        _cache_set("hk_records", items, _CACHE_TTL_HK)
        return items
    except Exception as e:
        print(f"Error scanning HK state: {e}")
        return []


def get_overrides():
    """Get all false positive/exemption overrides.

    Cached 30s — invalidated on create/delete override actions.
    """
    if not OVERRIDES_TABLE:
        return []

    cached = _cache_get("overrides")
    if cached is not None:
        return cached

    table = dynamodb.Table(OVERRIDES_TABLE)
    try:
        response = table.scan()
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            items.extend(response.get("Items", []))

        # Filter out non-override records and expired overrides
        now = datetime.now(UTC).isoformat()
        active = []
        for item in items:
            arn = item.get("resource_arn", "")
            if arn.startswith(("VIEWER#", "VISIT#")):
                continue
            expires = item.get("expires_at", "")
            if expires and expires < now:
                continue
            active.append(item)
        _cache_set("overrides", active, _CACHE_TTL_OVERRIDES)
        return active
    except Exception as e:
        print(f"Error scanning overrides table: {e}")
        return []


def get_override(resource_arn):
    """Get a single override record."""
    if not OVERRIDES_TABLE:
        return None

    table = dynamodb.Table(OVERRIDES_TABLE)
    try:
        response = table.get_item(Key={"resource_arn": resource_arn})
        return response.get("Item")
    except Exception as e:
        print(f"Error getting override for {resource_arn}: {e}")
        return None


def get_latest_hk_run_report():
    """Build a structured report from the most recent hunterkiller batch.

    Groups all HK DynamoDB records by batch_id, picks the latest batch,
    and returns a summary + full resource list for download.

    Returns None if no HK records exist.
    """
    records = get_all_hk_records()
    if not records:
        return None

    # Group by batch_id
    by_batch = {}
    for r in records:
        bid = r.get("batch_id", "")
        if not bid:
            continue
        by_batch.setdefault(bid, []).append(r)

    if not by_batch:
        return None

    # Latest batch (batch_id format is YYYYMMDDTHHmmss, sorts lexically)
    latest_bid = max(by_batch.keys())
    batch_records = by_batch[latest_bid]

    # Count by status
    by_status = {}
    savings = 0.0
    for r in batch_records:
        status = r.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        if status == "torn_down":
            raw = r.get("estimated_monthly_cost")
            savings += float(raw) if raw is not None else 0

    return {
        "batch_id": latest_bid,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "total": len(batch_records),
            "by_status": by_status,
            "savings_monthly": round(savings, 2),
        },
        "resources": batch_records,
    }


def get_latest_hk_run_html():
    """Render a styled HTML report for the most recent hunterkiller batch.

    Returns None if no HK records exist.
    """
    import html as html_mod

    report = get_latest_hk_run_report()
    if not report:
        return None

    batch_id = report["batch_id"]
    summary = report["summary"]
    resources = report["resources"]
    by_status = summary["by_status"]
    savings = summary["savings_monthly"]

    def esc(val):
        return html_mod.escape(str(val), quote=True) if val else ""

    # Group resources by status for display
    status_groups = {}
    for r in resources:
        s = r.get("status", "unknown")
        status_groups.setdefault(s, []).append(r)

    # Status display config: (label, header_color, description)
    _STATUS_META = {
        "warned": ("Warned", "#f59e0b", "Awaiting grace period expiry"),
        "torn_down": ("Torn Down", "#dc2626", "Resources removed"),
        "dry_run": ("Dry Run", "#f59e0b", "Would be removed (dry run mode)"),
        "resolved": ("Resolved", "#16a34a", "Came into compliance during grace period"),
        "protected": ("Protected", "#3b82f6", "Exempt from teardown"),
        "aborted": ("Aborted", "#6b7280", "Manually aborted via dashboard"),
        "error": ("Error", "#ef4444", "Teardown failed"),
    }

    h = []
    h.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    h.append("<style>")
    h.append(
        "body{font-family:system-ui,-apple-system,sans-serif;max-width:900px;margin:0 auto;padding:20px;color:#333}"
    )
    h.append("table{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0 20px}")
    h.append("th,td{padding:6px 8px;border:1px solid #e5e7eb;text-align:left}")
    h.append("th{background:#f9fafb}")
    h.append(".mono{font-family:monospace;font-size:11px}")
    h.append(
        ".badge{display:inline-block;padding:4px 12px;border-radius:12px;color:#fff;font-size:13px;margin:0 4px 4px 0}"
    )
    h.append("</style></head><body>")

    # Header
    h.append("<div style='background:#1e293b;color:#fff;padding:16px 24px;border-radius:8px 8px 0 0'>")
    h.append("<h1 style='margin:0;font-size:20px'>Hunterkiller Run Report</h1>")
    h.append(
        f"<p style='margin:4px 0 0;opacity:0.9;font-size:14px'>Batch: {esc(batch_id)} | Generated: {esc(report['generated_at'][:19])}</p>"
    )
    h.append("</div>")
    h.append("<div style='border:1px solid #e5e7eb;border-top:none;padding:20px 24px;border-radius:0 0 8px 8px'>")

    # Summary badges
    h.append("<div style='margin-bottom:16px'>")
    for status, count in sorted(by_status.items(), key=lambda x: -x[1]):
        meta = _STATUS_META.get(status, (status, "#6b7280", ""))
        h.append(f"<span class='badge' style='background:{meta[1]}'>{count} {esc(meta[0])}</span>")
    h.append("</div>")

    # Savings card
    if savings > 0:
        h.append(
            "<div style='background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px 16px;margin-bottom:16px'>"
        )
        h.append("<div style='font-size:12px;color:#166534'>Monthly Savings</div>")
        h.append(
            f"<div style='font-size:22px;font-weight:700;color:#16a34a'>${savings:.0f}<span style='font-size:13px;font-weight:400'>/mo</span></div>"
        )
        h.append("</div>")

    # Resource tables by status
    for status in ["torn_down", "dry_run", "warned", "resolved", "protected", "aborted", "error"]:
        items = status_groups.get(status, [])
        if not items:
            continue
        meta = _STATUS_META.get(status, (status, "#6b7280", ""))
        h.append(f"<h2 style='font-size:16px;margin:16px 0 4px;color:{meta[1]}'>{esc(meta[0])} ({len(items)})</h2>")
        if meta[2]:
            h.append(f"<p style='color:#6b7280;font-size:13px;margin:0 0 8px'>{esc(meta[2])}</p>")
        h.append("<table>")
        h.append(
            "<tr><th>Resource</th><th>Type</th><th>Region</th><th style='text-align:right'>$/mo</th><th>Contact</th></tr>"
        )
        for r in sorted(items, key=lambda x: -(float(x.get("estimated_monthly_cost") or 0))):
            name = esc(r.get("resource_name", "")) or esc(str(r.get("resource_arn", ""))[-40:])
            raw_c = r.get("estimated_monthly_cost")
            cost_cell = f"${float(raw_c):.2f}" if raw_c is not None else "-"
            h.append("<tr>")
            h.append(f"<td class='mono'>{name}</td>")
            h.append(f"<td>{esc(r.get('resource_type', ''))}</td>")
            h.append(f"<td>{esc(r.get('region', ''))}</td>")
            h.append(f"<td style='text-align:right'>{cost_cell}</td>")
            h.append(f"<td>{esc(r.get('contact_email', ''))}</td>")
            h.append("</tr>")
        h.append("</table>")

    # Any remaining statuses not in the ordered list
    for status, items in sorted(status_groups.items()):
        if status in _STATUS_META:
            continue
        h.append(f"<h2 style='font-size:16px;margin:16px 0 8px'>{esc(status)} ({len(items)})</h2>")
        h.append("<table>")
        h.append("<tr><th>Resource</th><th>Type</th><th>Region</th><th style='text-align:right'>$/mo</th></tr>")
        for r in items:
            name = esc(r.get("resource_name", "")) or esc(str(r.get("resource_arn", ""))[-40:])
            raw_c = r.get("estimated_monthly_cost")
            cost_cell = f"${float(raw_c):.2f}" if raw_c is not None else "-"
            h.append(f"<tr><td class='mono'>{name}</td><td>{esc(r.get('resource_type', ''))}</td>")
            h.append(f"<td>{esc(r.get('region', ''))}</td><td style='text-align:right'>{cost_cell}</td></tr>")
        h.append("</table>")

    h.append(
        f"<p style='color:#6b7280;font-size:12px;margin-top:16px'>Total resources in batch: {summary['total']}</p>"
    )
    h.append("</div></body></html>")
    return "\n".join(h)


def get_finding_by_arn(report, resource_arn):
    """Find all findings for a specific resource ARN."""
    if not report:
        return []
    findings = []
    for category in ["drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"]:
        for f in report.get(category, {}).get("findings", []):
            if f.get("resource_arn") == resource_arn:
                findings.append(f)
    return findings

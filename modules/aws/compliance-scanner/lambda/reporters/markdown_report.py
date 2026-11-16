"""
Markdown Report Generator

Produces the human-readable compliance report with tables, severity indicators,
and actionable recommendations. This is the primary output for humans.

Teardown commands use the CLI_PREFIX env var (default `aws`), so a deployment
can prefix them with its own credential wrapper.
These are only generated for drift detection findings (orphaned click-ops).
"""

import os
import re

from compliance_schema import INACTIVE_STATES

# CLI prefix for all teardown commands in the report
_CLI_PREFIX = os.environ.get("CLI_PREFIX") or "aws"


def generate_markdown_report(results):
    """
    Generate a markdown compliance report.

    Args:
        results: Full results dict from the scanner

    Returns:
        str: Markdown string
    """
    lines = []
    project = _md_text(results.get("project", "unknown"))
    env = _md_text(results.get("environment", "unknown"))
    generated = _md_text(results.get("generated_at", ""))
    duration = _md_text(results.get("scan_duration_seconds", 0))
    account_id = _md_text(results.get("account_id", ""))
    summary = results.get("summary", {})

    # Header
    lines.append(f"# Compliance Scan Report -- {project}-{env}")
    lines.append(f"**Date**: {generated} | **Duration**: {duration}s | **Account**: {account_id}")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append(f"**Resources discovered**: {summary.get('total_resources_discovered', 0)}")
    lines.append(f"**Total findings**: {summary.get('total_findings', 0)}")

    # Aggregate burn rate for out-of-compliance resources
    total_burn = _calculate_burn_rate(results)
    if total_burn > 0:
        lines.append("")
        lines.append(f"> **Out-of-compliance burn: ${total_burn:.2f}/month**")
    lines.append("")

    by_cat = summary.get("findings_by_category", {})
    by_sev = summary.get("findings_by_severity", {})

    lines.append("| Category | Findings |")
    lines.append("|----------|----------|")
    for cat, count in sorted(by_cat.items()):
        count_str = f"**{count}**" if count > 0 else "0"
        lines.append(f"| {_format_category(cat)} | {count_str} |")
    lines.append("")

    lines.append("| Severity | Count |")
    lines.append("|----------|-------|")
    for sev in ("HIGH", "MEDIUM", "LOW"):
        count = by_sev.get(sev, 0)
        indicator = _severity_indicator(sev)
        count_str = f"**{count}**" if count > 0 else "0"
        lines.append(f"| {indicator} {sev} | {count_str} |")
    lines.append("")

    # Regional Compliance Overview — per-region burn and status
    regions = results.get("regions_scanned", [])
    region_costs = results.get("region_costs", {})
    region_stats = _build_region_stats(results)

    lines.append("## Regional Overview")
    lines.append("")
    lines.append("| Region | 30-Day Spend | Findings | HIGH | Status |")
    lines.append("|--------|-------------|----------|------|--------|")

    # Sort regions by cost descending, include pseudo-regions with spend
    all_regions_with_cost = sorted(region_costs.items(), key=lambda x: -x[1])
    for region_name, cost in all_regions_with_cost:
        if cost < 0.01:
            continue
        stats = region_stats.get(region_name, {})
        finding_count = stats.get("total", 0)
        high_count = stats.get("high", 0)
        stats.get("burn", 0)

        if finding_count == 0:
            status = "**COMPLIANT**"
        elif high_count > 0:
            status = f"[!!!] {high_count} HIGH"
        else:
            status = f"[!] {finding_count} issues"

        lines.append(f"| **{_md_text(region_name)}** | ${cost:.2f} | {finding_count} | {high_count} | {status} |")

    lines.append("")

    # Show global/pseudo-regions that have spend but aren't scannable
    pseudo = [
        f"{_md_text(r)} (${c:.2f})"
        for r, c in all_regions_with_cost
        if c > 0.01 and r not in regions and r not in ("global", "NoRegion", "")
    ]
    if pseudo:
        lines.append(f"*Non-scannable spend*: {', '.join(pseudo)}")
        lines.append("")

    # Drift Detection
    drift = results.get("drift_detection", {})
    drift_findings = drift.get("findings", [])
    lines.append("## Drift Detection (Click-Ops)")
    lines.append("")
    lines.append(
        f"**Terraform-managed**: {drift.get('terraform_managed', 0)} | **Unmanaged**: {drift.get('unmanaged', 0)}"
    )
    lines.append("")

    if drift_findings:
        _render_findings_table(lines, drift_findings, include_teardown=True)
    else:
        lines.append("No drift findings.")
    lines.append("")

    # Multi-State Detection
    multi_state = results.get("multi_state_detection", {})
    tf_state_files = multi_state.get("state_files", [])
    tf_lock_tables = multi_state.get("lock_tables", [])
    if tf_state_files or tf_lock_tables:
        lines.append("## Multi-State Detection")
        lines.append("")
        lines.append(
            "Other Terraform management traces found in this account beyond the "
            f"known state (`{os.environ.get('TF_STATE_BUCKET', '')}`)."
        )
        lines.append("")
        if tf_state_files:
            lines.append(f"### Terraform State Files ({len(tf_state_files)})")
            lines.append("")
            for sf in tf_state_files:
                bucket = sf.get("bucket", "")
                key = sf.get("key", "")
                size = sf.get("size_bytes", 0)
                modified = sf.get("last_modified", "")
                size_str = f"{size / 1024:.1f} KB" if size else ""
                lines.append(f"- `s3://{_md_code(bucket)}/{_md_code(key)}` — {size_str}, modified {_md_text(modified)}")
            lines.append("")
        if tf_lock_tables:
            lines.append(f"### Terraform Lock Tables ({len(tf_lock_tables)})")
            lines.append("")
            for tbl in tf_lock_tables:
                table_name = tbl.get("table_name", "")
                region = tbl.get("region", "")
                item_count = tbl.get("item_count", 0)
                lines.append(
                    f"- `{_md_code(table_name)}` in {_md_text(region)} — {_md_text(item_count)} items "
                    "(DynamoDB table with LockID hash key)"
                )
            lines.append("")
        if multi_state.get("notes"):
            for note in multi_state["notes"]:
                lines.append(f"> {_md_text(note)}")
            lines.append("")

    # Tag Compliance
    tag_findings = results.get("tag_compliance", {}).get("findings", [])
    lines.append("## Tag Compliance")
    lines.append("")
    if tag_findings:
        _render_findings_table(lines, tag_findings)
    else:
        lines.append("All resources have required tags.")
    lines.append("")

    # Schedule Compliance
    schedule_findings = results.get("schedule_compliance", {}).get("findings", [])
    lines.append("## Schedule Compliance")
    lines.append("")
    if schedule_findings:
        _render_findings_table(lines, schedule_findings)
    else:
        lines.append("No schedule compliance issues.")
    lines.append("")

    # Lifecycle Compliance
    lifecycle_findings = results.get("lifecycle_compliance", {}).get("findings", [])
    lines.append("## Lifecycle Compliance")
    lines.append("")
    if lifecycle_findings:
        _render_findings_table(lines, lifecycle_findings)
    else:
        lines.append("No lifecycle compliance issues.")
    lines.append("")

    # Errors
    errors = results.get("errors", [])
    if errors:
        lines.append("## Errors")
        lines.append("")
        for err in errors:
            lines.append(f"- {_md_text(err[:200])}")
        lines.append("")

    # Footer — self-documenting provenance
    scanner_url = os.environ.get("SCANNER_SOURCE_URL", "")
    standards_url = os.environ.get("STANDARDS_URL", "")

    lines.append("---")
    lines.append("")
    lines.append("## About This Report")
    lines.append("")
    lines.append(
        f"**Generated by**: `{project}-{env}-compliance-scanner` Lambda "
        f"({duration}s scan of {summary.get('total_resources_discovered', 0)} resources)"
    )
    if scanner_url:
        lines.append(f"**Scanner source code**: [{scanner_url}]({scanner_url})")
    if standards_url:
        lines.append(f"**Compliance standards**: [{standards_url}]({standards_url})")
    lines.append("")
    lines.append("All resources in this account are expected to comply with these standards:")
    lines.append("- **Required tags**: `Project`, `Environment`, `ManagedBy`, `Lifecycle`, `Repository`")
    lines.append(
        "- **Managed by Terraform**: All infrastructure should be codified "
        "(`ManagedBy=terraform`). Unmanaged resources are flagged as click-ops."
    )
    lines.append(
        "- **Lifecycle tracking**: Temporary resources must have `ExpiresAt` tags. "
        "Persistent resources must have `Lifecycle=persistent`."
    )
    lines.append("- **Schedule compliance**: Always-on resources should have schedule tags or be flagged for review.")
    lines.append("")
    lines.append(
        "**Capabilities**: Full account discovery across all active regions (via "
        "Cost Explorer), Terraform state drift detection, per-resource cost estimates "
        "(instance-type lookup), multi-state detection, teardown CLI commands for "
        "orphaned resources."
    )
    lines.append("")
    lines.append(
        "**Limitations**: Cannot see resources in zero-spend regions, security group "
        "rules, IAM policy contents, cross-account resources, real-time CUR costs, "
        "or who created a resource (CloudTrail attribution not yet integrated)."
    )

    return "\n".join(lines)


def _render_findings_table(lines, findings, include_teardown=False):
    """Render a findings table grouped by severity with full resource details.

    Terminated/stopped resources are separated into a collapsed section at the end.

    Args:
        lines: Output list of markdown lines
        findings: List of finding dicts
        include_teardown: If True, append CLI teardown commands (drift findings only)
    """
    # Partition: active vs already-terminated/stopped
    active = [f for f in findings if f.get("state") not in INACTIVE_STATES]
    inactive = [f for f in findings if f.get("state") in INACTIVE_STATES]

    # Render active findings by severity
    for severity in ("HIGH", "MEDIUM", "LOW"):
        sev_findings = [f for f in active if f.get("severity") == severity]
        if not sev_findings:
            continue

        indicator = _severity_indicator(severity)
        lines.append(f"### {indicator} {severity} ({len(sev_findings)})")
        lines.append("")

        _render_finding_items(lines, sev_findings, include_teardown, cap=50)

        if len(sev_findings) > 50:
            lines.append(f"*... and {len(sev_findings) - 50} more {severity} findings*")
            lines.append("")

    # Render inactive (terminated/stopped) findings as a separate section
    if inactive:
        lines.append(f"### Already Terminated / Stopped ({len(inactive)})")
        lines.append("")
        lines.append(
            "*These resources are already shut down. No action needed unless "
            "cleanup of associated resources (volumes, security groups, ENIs) is desired.*"
        )
        lines.append("")
        _render_finding_items(lines, inactive, include_teardown=False, cap=30)

        if len(inactive) > 30:
            lines.append(f"*... and {len(inactive) - 30} more terminated/stopped resources*")
            lines.append("")


def _render_finding_items(lines, findings, include_teardown, cap=50):
    """Render individual finding items."""
    for f in findings[:cap]:
        arn = f.get("resource_arn", f.get("arn", ""))
        name = _md_text(f.get("resource_name", ""))
        rtype = f.get("resource_type", "")
        state = _md_text(f.get("state") or "")
        region = f.get("region", "")
        cost = f.get("estimated_monthly_cost")
        cost_str = f"${cost:.2f}/mo" if cost is not None else ""
        duration = f.get("running_duration_days")
        age_str = f"{duration}d old" if duration is not None else ""
        issue = _md_text(f.get("issue") or "")
        recommendation = _md_text(f.get("recommendation") or "")
        resource_id = _extract_id(arn)
        console_link = _md_url(_console_link(arn, region, rtype))

        # Resource header with key details
        state_badge = f" **{state}**" if state else ""
        cost_badge = f" | {cost_str}" if cost_str else ""
        age_badge = f" | {age_str}" if age_str else ""

        if console_link:
            lines.append(f"- **[{name}]({console_link})** `{_md_code(rtype)}`{state_badge}{cost_badge}{age_badge}")
        else:
            lines.append(f"- **{name}** `{_md_code(rtype)}`{state_badge}{cost_badge}{age_badge}")

        if resource_id and resource_id != f.get("resource_name", ""):
            lines.append(f"  ID: `{_md_code(resource_id)}` | ARN: `{_md_code(arn[:80])}`")
        else:
            lines.append(f"  ARN: `{_md_code(arn[:80])}`")

        # Context: what is this resource associated with / used for
        purpose = f.get("inferred_purpose") or ""
        attached_to = f.get("attached_to") or ""
        if purpose:
            lines.append(f"  _Context: {_md_text(purpose)}_")
        elif attached_to:
            lines.append(f"  _Context: Attached to `{_md_code(attached_to)}`_")

        # Console link as plain URL (clickable in email clients)
        if console_link:
            lines.append(f"  Console: {console_link}")

        lines.append(f"  {issue}")
        if recommendation:
            lines.append(f"  *{recommendation}*")

        # Teardown command for orphaned click-ops resources
        if include_teardown:
            cmd = _teardown_command(rtype, resource_id, arn, region)
            if cmd:
                lines.append("  ```")
                lines.append(f"  {_md_code(cmd)}")
                lines.append("  ```")

        lines.append("")


def _build_region_stats(results):
    """Build per-region finding counts, HIGH counts, and burn rates.

    Deduplicates by ARN so a resource flagged by multiple scanners
    is only counted once per region.
    """
    stats = {}  # region -> {total, high, burn, seen_arns}

    for section in ("tag_compliance", "drift_detection", "schedule_compliance", "lifecycle_compliance"):
        for f in results.get(section, {}).get("findings", []):
            region = f.get("region", "global")
            arn = f.get("resource_arn", "")

            if region not in stats:
                stats[region] = {"total": 0, "high": 0, "burn": 0.0, "seen_arns": set()}

            # Only count each resource once (deduplicate by ARN)
            if arn and arn in stats[region]["seen_arns"]:
                continue
            if arn:
                stats[region]["seen_arns"].add(arn)

            stats[region]["total"] += 1
            if f.get("severity") == "HIGH":
                stats[region]["high"] += 1
            if f.get("state") not in INACTIVE_STATES:
                cost = f.get("estimated_monthly_cost")
                if cost:
                    stats[region]["burn"] += cost

    # Clean up: remove seen_arns set before returning
    for region in stats:
        del stats[region]["seen_arns"]

    return stats


def _calculate_burn_rate(results):
    """Sum estimated monthly costs across all findings (deduplicated by ARN).

    Excludes inactive/terminated resources to avoid inflating the burn rate.
    """
    seen_arns = set()
    total = 0.0
    for section in ("tag_compliance", "drift_detection", "schedule_compliance", "lifecycle_compliance"):
        for f in results.get(section, {}).get("findings", []):
            if f.get("state") in INACTIVE_STATES:
                continue
            arn = f.get("resource_arn", "")
            if arn in seen_arns:
                continue
            seen_arns.add(arn)
            cost = f.get("estimated_monthly_cost")
            if cost:
                total += cost
    return round(total, 2)


def _extract_id(arn):
    """Extract the resource ID from the last segment of an ARN."""
    if not arn:
        return ""
    # ARNs like arn:aws:ec2:region:acct:instance/i-1234 → i-1234
    # or arn:aws:ec2:region:acct:volume/vol-1234 → vol-1234
    parts = arn.split("/")
    if len(parts) > 1:
        return parts[-1]
    parts = arn.split(":")
    return parts[-1] if parts else ""


def _console_link(arn, region, resource_type):
    """Generate AWS Console link for a resource."""
    if not arn or not region:
        return ""

    resource_id = _extract_id(arn)
    base = f"https://{region}.console.aws.amazon.com"

    if resource_type == "ec2:instance":
        return f"{base}/ec2/home?region={region}#InstanceDetails:instanceId={resource_id}"
    if resource_type == "ec2:volume":
        return f"{base}/ec2/home?region={region}#VolumeDetails:volumeId={resource_id}"
    if resource_type == "ec2:security-group":
        return f"{base}/ec2/home?region={region}#SecurityGroup:groupId={resource_id}"
    if resource_type == "rds:instance":
        return f"{base}/rds/home?region={region}#database:id={resource_id}"
    if resource_type == "rds:cluster":
        return f"{base}/rds/home?region={region}#database:id={resource_id}"
    if resource_type == "ecs:service":
        return f"{base}/ecs/home?region={region}#/services"
    if resource_type == "ecs:cluster":
        return f"{base}/ecs/home?region={region}#/clusters/{resource_id}"
    if resource_type == "lambda:function":
        return f"{base}/lambda/home?region={region}#/functions/{resource_id}"
    if resource_type == "s3:bucket":
        return f"https://s3.console.aws.amazon.com/s3/buckets/{resource_id}"
    if resource_type == "secretsmanager:secret":
        return f"{base}/secretsmanager/secret?name={resource_id}&region={region}"
    if resource_type == "eks:cluster":
        return f"{base}/eks/home?region={region}#/clusters/{resource_id}"
    if resource_type in ("elbv2:application", "elbv2:network"):
        return f"{base}/ec2/home?region={region}#LoadBalancers:"
    if resource_type == "ecr:repository":
        return f"{base}/ecr/repositories/private/{resource_id}?region={region}"
    if resource_type == "efs:filesystem":
        return f"{base}/efs/home?region={region}#/file-systems/{resource_id}"
    if resource_type == "elasticache:cluster":
        return f"{base}/elasticache/home?region={region}#/redis/{resource_id}"
    if resource_type == "dynamodb:table":
        return f"{base}/dynamodb/home?region={region}#tables:selected={resource_id}"
    if resource_type == "sns:topic":
        return f"{base}/sns/v3/home?region={region}#/topic/{arn}"
    if resource_type == "sqs:queue":
        return f"{base}/sqs/v3/home?region={region}#/queues"

    return ""


def _teardown_command(resource_type, resource_id, arn, region):
    """Generate a CLI teardown command for an orphaned click-ops resource.

    Only produces commands for resource types where a single CLI call can
    safely remove the resource. Returns None for types that need manual
    investigation (e.g., VPCs with dependencies).
    """
    r = f"--region {region}" if region else ""

    if resource_type == "ec2:instance":
        return f"{_CLI_PREFIX} ec2 terminate-instances --instance-ids {resource_id} {r}"
    if resource_type == "ec2:volume":
        return f"{_CLI_PREFIX} ec2 delete-volume --volume-id {resource_id} {r}"
    if resource_type == "ec2:security-group":
        return f"{_CLI_PREFIX} ec2 delete-security-group --group-id {resource_id} {r}"
    if resource_type == "ec2:eip":
        return f"{_CLI_PREFIX} ec2 release-address --allocation-id {resource_id} {r}"
    if resource_type == "ec2:nat-gateway":
        return f"{_CLI_PREFIX} ec2 delete-nat-gateway --nat-gateway-id {resource_id} {r}"
    if resource_type == "rds:instance":
        return f"{_CLI_PREFIX} rds delete-db-instance --db-instance-identifier {resource_id} --skip-final-snapshot {r}"
    if resource_type == "rds:cluster":
        return f"{_CLI_PREFIX} rds delete-db-cluster --db-cluster-identifier {resource_id} --skip-final-snapshot {r}"
    if resource_type == "ecs:service":
        # ECS service ARN: arn:aws:ecs:region:acct:service/cluster/service-name
        parts = arn.split("/")
        if len(parts) >= 3:
            cluster = parts[-2]
            svc = parts[-1]
            return (
                f"{_CLI_PREFIX} ecs update-service --cluster {cluster} --service {svc} "
                f"--desired-count 0 {r} && "
                f"{_CLI_PREFIX} ecs delete-service --cluster {cluster} --service {svc} {r}"
            )
    if resource_type == "ecs:cluster":
        return f"{_CLI_PREFIX} ecs delete-cluster --cluster {resource_id} {r}"
    if resource_type == "lambda:function":
        return f"{_CLI_PREFIX} lambda delete-function --function-name {resource_id} {r}"
    if resource_type == "s3:bucket":
        return f"{_CLI_PREFIX} s3 rb s3://{resource_id} --force  # WARNING: deletes all objects in the bucket"
    if resource_type == "dynamodb:table":
        return f"{_CLI_PREFIX} dynamodb delete-table --table-name {resource_id} {r}"
    if resource_type == "elasticache:cluster":
        return f"{_CLI_PREFIX} elasticache delete-cache-cluster --cache-cluster-id {resource_id} {r}"
    if resource_type == "efs:filesystem":
        return f"{_CLI_PREFIX} efs delete-file-system --file-system-id {resource_id} {r}"
    if resource_type == "secretsmanager:secret":
        return (
            f"{_CLI_PREFIX} secretsmanager delete-secret --secret-id {resource_id} --force-delete-without-recovery {r}"
        )
    if resource_type == "eks:cluster":
        return f"{_CLI_PREFIX} eks delete-cluster --name {resource_id} {r}"
    if resource_type == "sns:topic":
        return f"{_CLI_PREFIX} sns delete-topic --topic-arn {arn} {r}"
    if resource_type == "sqs:queue":
        return f"{_CLI_PREFIX} sqs delete-queue --queue-url {arn} {r}"
    if resource_type in ("elbv2:application", "elbv2:network"):
        return f"{_CLI_PREFIX} elbv2 delete-load-balancer --load-balancer-arn {arn} {r}"
    if resource_type == "apigateway:api":
        return f"{_CLI_PREFIX} apigatewayv2 delete-api --api-id {resource_id} {r}"
    if resource_type == "ecr:repository":
        return f"{_CLI_PREFIX} ecr delete-repository --repository-name {resource_id} --force {r}"

    # No safe single-command teardown for this type
    return None


def _severity_indicator(severity):
    """Return a text-based severity indicator for markdown."""
    # Uses unicode block characters for visual weight without requiring emoji support
    if severity == "HIGH":
        return "[!!!]"
    if severity == "MEDIUM":
        return "[!!]"
    return "[!]"


def _format_category(cat):
    """Format category name for display."""
    return _md_text(cat.replace("_", " ").title())


def _md_text(value):
    """Free text for the report: one line, no raw HTML, no link, image or code-span syntax."""
    text = re.sub(r"[\r\n]+", " ", str(value))
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("`", "\\`")


def _md_code(value):
    """A value placed inside a code span or fence: one line, and it cannot close the span."""
    text = re.sub(r"[\r\n]+", " ", str(value)).replace("`", "'")
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _md_url(url):
    """A link target that cannot end the link early or carry markup."""
    for char, encoded in ((" ", "%20"), ("(", "%28"), (")", "%29"), ("<", "%3C"), (">", "%3E"), ("\r", ""), ("\n", "")):
        url = url.replace(char, encoded)
    return url

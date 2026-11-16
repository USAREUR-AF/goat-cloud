"""
HTML Email Reporter (via SES)

Sends a professionally formatted HTML email with:
  - Color-coded severity indicators (red/orange/yellow)
  - Styled tables for regional overview and findings
  - Clickable console links
  - Teardown commands in monospace blocks
  - Full HTML report attached as a .html file

Uses SES send_raw_email() with MIME multipart for body + attachment.
"""

import html
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3
from compliance_schema import INACTIVE_STATES, require_env

# CLI prefix for teardown commands (a wrapper such as a credential helper may precede `aws`)
_CLI_PREFIX = os.environ.get("CLI_PREFIX") or "aws"


def _footer_links():
    """Links for the report footer, from two optional inputs; empty when neither is set."""
    pairs = [
        ("Scanner source code", os.environ.get("SCANNER_SOURCE_URL", "")),
        ("Compliance standards", os.environ.get("STANDARDS_URL", "")),
    ]
    links = " |\n        ".join(
        f'<a href="{_html_escape(_safe_url(url))}">{label}</a>' for label, url in pairs if _safe_url(url)
    )
    return links + "<br>" if links else ""


# Resource type → emoji for visual scanning
_RESOURCE_EMOJI = {
    "ec2:instance": "\U0001f5a5\ufe0f",  # 🖥️
    "ec2:volume": "\U0001f4be",  # 💾
    "ec2:security-group": "\U0001f6e1\ufe0f",  # 🛡️
    "ec2:eip": "\U0001f310",  # 🌐
    "ec2:nat-gateway": "\U0001f6aa",  # 🚪
    "ecs:service": "\U0001f4e6",  # 📦
    "ecs:cluster": "\U0001f4e6",  # 📦
    "lambda:function": "\u26a1",  # ⚡
    "s3:bucket": "\U0001faa3",  # 🪣
    "rds:instance": "\U0001f5c4\ufe0f",  # 🗄️
    "elbv2:application": "\u2696\ufe0f",  # ⚖️
    "elbv2:network": "\u2696\ufe0f",  # ⚖️
    "elasticache:cluster": "\U0001f9ca",  # 🧊
    "efs:filesystem": "\U0001f4c1",  # 📁
    "dynamodb:table": "\U0001f4ca",  # 📊
    "eks:cluster": "\u2638\ufe0f",  # ☸️
    "cloudfront:distribution": "\U0001f30d",  # 🌍
    "route53:hostedzone": "\U0001f517",  # 🔗
    "iam:role": "\U0001f511",  # 🔑
    "iam:user": "\U0001f511",  # 🔑
    "iam:policy": "\U0001f511",  # 🔑
}

# Severity colors
_SEV_COLORS = {
    "HIGH": {"bg": "#fde8e8", "text": "#b91c1c", "badge": "#dc2626"},
    "MEDIUM": {"bg": "#fff3cd", "text": "#92400e", "badge": "#d97706"},
    "LOW": {"bg": "#e8f5e9", "text": "#1b5e20", "badge": "#16a34a"},
}


def send_html_email(results, sender_email, recipient_emails, reports_bucket, date_prefix, delta=None):
    """
    Send a formatted HTML compliance report via SES.

    Args:
        results: Full results dict from the scanner
        sender_email: Verified SES sender address
        recipient_emails: List of recipient email addresses
        reports_bucket: S3 bucket name for report links
        date_prefix: Date string for S3 key prefix (YYYY-MM-DD)
        delta: Optional delta dict from compute_delta() for 24-hour changes
    """
    if not recipient_emails:
        print("No SES recipient emails configured — skipping HTML email")
        return

    ses = boto3.client("ses")

    project = results.get("project", "unknown")
    results.get("environment", "unknown")
    summary = results.get("summary", {})
    by_sev = summary.get("findings_by_severity", {})

    total_burn = _calculate_burn_rate(results)
    burn_str = f" | ${total_burn:.0f}/mo burn" if total_burn > 0 else ""

    subject = (
        f"[{project}] Compliance: {by_sev.get('HIGH', 0)} HIGH, "
        f"{by_sev.get('MEDIUM', 0)} MED, "
        f"{by_sev.get('LOW', 0)} LOW{burn_str}"
    )

    # Build HTML body (email summary) and full HTML report (attachment)
    html_body = _build_email_body(results, reports_bucket, date_prefix, delta=delta)
    html_report = _build_full_html_report(results, reports_bucket, date_prefix, delta=delta)

    # Build MIME message with HTML body + HTML attachment
    msg = MIMEMultipart("mixed")
    msg["Subject"] = _header_text(subject)
    msg["From"] = f"Compliance Scanner <{_header_text(sender_email)}>"
    msg["To"] = ", ".join(_header_text(r) for r in recipient_emails)

    # HTML body
    body_part = MIMEText(html_body, "html", "utf-8")
    msg.attach(body_part)

    # HTML report attachment
    attachment = MIMEText(html_report, "html", "utf-8")
    attachment.add_header("Content-Disposition", "attachment", filename=f"compliance-report-{date_prefix}.html")
    msg.attach(attachment)

    ses.send_raw_email(
        Source=sender_email,
        Destinations=recipient_emails,
        RawMessage={"Data": msg.as_string()},
    )
    print(f"HTML email sent via SES to {', '.join(recipient_emails)}")


def _build_email_body(results, reports_bucket, date_prefix, delta=None):
    """Build the HTML email body — a styled summary with key metrics."""
    project = _html_escape(results.get("project", "unknown"))
    env = _html_escape(results.get("environment", "unknown"))
    generated = _html_escape(results.get("generated_at", ""))
    duration = _html_escape(results.get("scan_duration_seconds", 0))
    account_id = _html_escape(results.get("account_id", ""))
    summary = results.get("summary", {})
    by_sev = summary.get("findings_by_severity", {})
    by_cat = summary.get("findings_by_category", {})

    total_burn = _calculate_burn_rate(results)
    region_costs = results.get("region_costs", {})
    region_stats = _build_region_stats(results)

    h = []
    h.append("""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         color: #1a1a1a; max-width: 700px; margin: 0 auto; padding: 16px;
         background: #f8f9fa; }
  h1 { color: #111827; font-size: 22px; margin-bottom: 4px; }
  h2 { color: #374151; font-size: 16px; border-bottom: 2px solid #e5e7eb;
        padding-bottom: 6px; margin-top: 24px; }
  .meta { color: #6b7280; font-size: 13px; margin-bottom: 16px; }
  .burn { background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px;
           padding: 10px 14px; font-size: 18px; font-weight: 700; color: #991b1b;
           text-align: center; margin: 12px 0; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; margin: 8px 0; }
  th { background: #f3f4f6; color: #374151; padding: 8px 10px; text-align: left;
        border-bottom: 2px solid #d1d5db; font-weight: 600; }
  td { padding: 6px 10px; border-bottom: 1px solid #e5e7eb; }
  tr:hover td { background: #f9fafb; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
            font-size: 11px; font-weight: 700; color: #fff; }
  .badge-high { background: #dc2626; }
  .badge-medium { background: #d97706; }
  .badge-low { background: #16a34a; }
  .compliant { color: #16a34a; font-weight: 600; }
  .finding { background: #fff; border: 1px solid #e5e7eb; border-radius: 6px;
              padding: 10px 14px; margin: 8px 0; }
  .finding-header { font-weight: 600; font-size: 14px; }
  .finding-meta { color: #6b7280; font-size: 12px; margin: 4px 0; }
  .finding-issue { color: #374151; font-size: 13px; margin: 4px 0; }
  .teardown { background: #1e293b; color: #e2e8f0; padding: 8px 12px;
               border-radius: 4px; font-family: 'SF Mono', Monaco, monospace;
               font-size: 11px; overflow-x: auto; margin: 6px 0; white-space: pre-wrap;
               word-break: break-all; }
  .context { color: #6b7280; font-size: 12px; font-style: italic; }
  a { color: #2563eb; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .footer { margin-top: 24px; padding-top: 12px; border-top: 1px solid #d1d5db;
             color: #9ca3af; font-size: 11px; }
  .footer a { color: #6b7280; }
  .section-terminated { opacity: 0.7; }
</style></head><body>""")

    # Header
    h.append(f"<h1>Compliance Report &mdash; {project}-{env}</h1>")
    h.append(
        f'<div class="meta">{generated} | {duration}s scan | '
        f"Account {account_id} | {summary.get('total_resources_discovered', 0)} resources</div>"
    )

    # Burn rate callout
    if total_burn > 0:
        h.append(f'<div class="burn">Out-of-compliance burn: ${total_burn:.2f}/month</div>')

    # Severity badges
    h.append('<div style="margin: 12px 0;">')
    for sev in ("HIGH", "MEDIUM", "LOW"):
        count = by_sev.get(sev, 0)
        css_class = f"badge-{sev.lower()}"
        h.append(f'<span class="badge {css_class}">{count} {sev}</span> ')
    total_findings = summary.get("total_findings", 0)
    total_resources = summary.get("total_resources_discovered", 0)
    h.append(
        f'<div class="context" style="margin-top:4px">'
        f"{total_findings} findings across {total_resources} resources "
        f"(one resource can have multiple violations).</div>"
    )
    h.append("</div>")

    # 24-hour delta section
    if delta:
        h.append(_build_delta_section(delta))

    # Regional Overview — unified table with total spend + NC burn
    active_regions = [(r, c) for r, c in sorted(region_costs.items(), key=lambda x: -x[1]) if c >= 0.01]

    if active_regions:
        h.append("<h2>Regional Overview</h2>")
        h.append(
            '<p class="context">NC Burn = estimated cost of non-compliant resources. '
            "Compliant = Total Spend minus NC Burn.</p>"
        )
        h.append(
            '<table><tr><th>Region</th><th style="text-align:right">Total Spend</th>'
            '<th style="text-align:right">NC Burn</th>'
            '<th style="text-align:right">Compliant</th>'
            '<th style="text-align:right">Findings</th>'
            '<th style="text-align:right">HIGH</th><th>Status</th></tr>'
        )
        for region_name, total_cost in active_regions:
            stats = region_stats.get(region_name, {})
            fc = stats.get("total", 0)
            hc = stats.get("high", 0)
            burn = stats.get("burn", 0.0)
            compliant_spend = max(0, total_cost - burn)
            if fc == 0:
                status = '<span class="compliant">COMPLIANT</span>'
            elif hc > 0:
                status = f'<span class="badge badge-high">{hc} HIGH</span>'
            else:
                status = f'<span class="badge badge-medium">{fc} issues</span>'
            burn_cell = f'<strong style="color:#b91c1c">${burn:.2f}</strong>' if burn > 0 else "—"
            comp_cell = f'<span style="color:#16a34a">${compliant_spend:.2f}</span>'
            h.append(
                f"<tr><td><strong>{_html_escape(region_name)}</strong></td>"
                f'<td style="text-align:right">${total_cost:.2f}</td>'
                f'<td style="text-align:right">{burn_cell}</td>'
                f'<td style="text-align:right">{comp_cell}</td>'
                f'<td style="text-align:right">{fc}</td>'
                f'<td style="text-align:right">{hc}</td>'
                f"<td>{status}</td></tr>"
            )
        h.append("</table>")

    # Category breakdown
    h.append("<h2>Violations by Check</h2>")
    h.append('<table><tr><th>Compliance Check</th><th style="text-align:right">Violations</th></tr>')
    for cat, count in sorted(by_cat.items()):
        bold = ' style="font-weight:700; color:#b91c1c"' if count > 0 else ""
        h.append(
            f'<tr><td>{_html_escape(cat.replace("_", " ").title())}</td><td style="text-align:right"{bold}>{count}</td></tr>'
        )
    h.append("</table>")

    # Charts
    h.append(_build_charts_html(results, region_stats, region_costs))

    # Drift summary
    drift = results.get("drift_detection", {})
    h.append("<h2>Drift Detection (Click-Ops)</h2>")
    h.append(
        f"<p>Terraform-managed: <strong>{drift.get('terraform_managed', 0)}</strong> "
        f"| Unmanaged: <strong>{drift.get('unmanaged', 0)}</strong></p>"
    )

    # Other IaC management — split into "our workspaces" vs "external"
    other_iac = results.get("other_iac_analysis", [])
    ours = [sa for sa in other_iac if sa.get("is_ours")]
    external = [sa for sa in other_iac if not sa.get("is_ours")]

    if ours:
        h.append("<h2>Terraform State Storage</h2>")
        h.append('<p class="context">State files in the Terraform state backend for this cloud management repo.</p>')
        for sa in ours:
            h.append(_render_iac_analysis(sa, style="compliant"))

    if external:
        h.append("<h2>External IaC</h2>")
        h.append(
            '<p class="context">Terraform state files from other repos/teams. '
            "These resources may need review, consolidation, or teardown.</p>"
        )
        for sa in external:
            h.append(_render_iac_analysis(sa, style="warning"))

    # Top HIGH findings
    high_findings = _collect_high_findings(results)
    if high_findings:
        grouped = _group_findings(high_findings)
        active = [g for g in grouped if g["representative"].get("state") not in INACTIVE_STATES]
        inactive = [g for g in grouped if g["representative"].get("state") in INACTIVE_STATES]

        if active:
            h.append(f"<h2>Action Required &mdash; {len(active)} Active HIGH Findings</h2>")
            # Show first 5 unconditionally, then only items >= $15/mo, cap at 10
            shown = 0
            for i, item in enumerate(active):
                if shown >= 10:
                    break
                if i >= 5 and item["total_cost"] < 15:
                    continue
                h.append(_render_finding_html(item))
                shown += 1
            omitted = len(active) - shown
            if omitted > 0:
                h.append(f'<p class="context">&hellip; and {omitted} more (see attached report for full list)</p>')

        if inactive:
            h.append(f'<h2 class="section-terminated">Already Terminated / Stopped ({len(inactive)})</h2>')
            h.append('<p class="context">No action needed. Listed for awareness.</p>')
            for item in inactive[:10]:
                f = item["representative"]
                name = _html_escape(f.get("resource_name", "unknown"))
                rtype = _html_escape(f.get("resource_type", ""))
                state = _html_escape(f.get("state", ""))
                h.append(f'<div class="finding-meta">{name} ({rtype}) &mdash; <em>{state}</em></div>')

    # Report links
    if reports_bucket:
        h.append("<h2>Full Reports</h2>")
        h.append("<p>See the attached HTML report for complete details, or download from S3:</p>")
        download_cmd = (
            f"{_CLI_PREFIX} s3 cp "
            f"s3://{reports_bucket}/reports/{date_prefix}/compliance-report.md "
            f"./compliance-report.md --region {require_env('AWS_REGION')}"
        )
        h.append(f'<div class="teardown">{_html_escape(download_cmd)}</div>')

    # Footer
    links = _footer_links()

    h.append(f"""<div class="footer">
        Compliance Scanner | {project}-{env} | Report-only mode &mdash; no destructive actions taken<br>
        {links}
        Required tags: Project, Environment, ManagedBy, Lifecycle, Repository<br>
        All resources must be Terraform-managed (ManagedBy=terraform)
    </div>""")

    h.append("</body></html>")
    return "\n".join(h)


def _build_full_html_report(results, reports_bucket, date_prefix, delta=None):
    """Build the full HTML report for attachment — all findings with full detail."""
    project = _html_escape(results.get("project", "unknown"))
    env = _html_escape(results.get("environment", "unknown"))
    generated = _html_escape(results.get("generated_at", ""))
    duration = _html_escape(results.get("scan_duration_seconds", 0))
    account_id = _html_escape(results.get("account_id", ""))
    summary = results.get("summary", {})
    by_sev = summary.get("findings_by_severity", {})
    by_cat = summary.get("findings_by_category", {})

    total_burn = _calculate_burn_rate(results)
    region_costs = results.get("region_costs", {})
    region_stats = _build_region_stats(results)

    h = []
    h.append(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Compliance Report — {project}-{env}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         color: #1a1a1a; max-width: 900px; margin: 0 auto; padding: 20px; }}
  h1 {{ color: #111827; font-size: 24px; }}
  h2 {{ color: #374151; font-size: 18px; border-bottom: 2px solid #e5e7eb;
        padding-bottom: 6px; margin-top: 28px; }}
  h3 {{ color: #4b5563; font-size: 15px; margin-top: 20px; }}
  .meta {{ color: #6b7280; font-size: 13px; }}
  .burn {{ background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px;
           padding: 12px 16px; font-size: 20px; font-weight: 700; color: #991b1b;
           text-align: center; margin: 16px 0; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 10px 0; }}
  th {{ background: #f3f4f6; color: #374151; padding: 8px 10px; text-align: left;
        border-bottom: 2px solid #d1d5db; font-weight: 600; }}
  td {{ padding: 6px 10px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }}
  tr:hover td {{ background: #f9fafb; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
            font-size: 11px; font-weight: 700; color: #fff; }}
  .badge-high {{ background: #dc2626; }}
  .badge-medium {{ background: #d97706; }}
  .badge-low {{ background: #16a34a; }}
  .compliant {{ color: #16a34a; font-weight: 600; }}
  .finding {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 6px;
              padding: 10px 14px; margin: 8px 0; }}
  .finding-high {{ border-left: 4px solid #dc2626; }}
  .finding-medium {{ border-left: 4px solid #d97706; }}
  .finding-low {{ border-left: 4px solid #16a34a; }}
  .finding-header {{ font-weight: 600; font-size: 14px; }}
  .finding-meta {{ color: #6b7280; font-size: 12px; margin: 3px 0; }}
  .finding-issue {{ color: #374151; font-size: 13px; margin: 4px 0; }}
  .teardown {{ background: #1e293b; color: #e2e8f0; padding: 8px 12px;
               border-radius: 4px; font-family: 'SF Mono', Monaco, monospace;
               font-size: 11px; overflow-x: auto; margin: 6px 0; white-space: pre-wrap;
               word-break: break-all; }}
  .context {{ color: #6b7280; font-size: 12px; font-style: italic; }}
  a {{ color: #2563eb; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .footer {{ margin-top: 28px; padding-top: 12px; border-top: 1px solid #d1d5db;
             color: #9ca3af; font-size: 11px; }}
  .section-terminated {{ opacity: 0.65; }}
</style></head><body>""")

    # Header
    h.append(f"<h1>Compliance Scan Report &mdash; {project}-{env}</h1>")
    h.append(
        f'<div class="meta">{generated} | {duration}s scan | '
        f"Account {account_id} | {summary.get('total_resources_discovered', 0)} resources</div>"
    )

    if total_burn > 0:
        h.append(f'<div class="burn">Out-of-compliance burn: ${total_burn:.2f}/month</div>')

    # Severity + category summary
    h.append("<h2>Summary</h2>")
    h.append('<div style="margin: 12px 0;">')
    for sev in ("HIGH", "MEDIUM", "LOW"):
        count = by_sev.get(sev, 0)
        h.append(f'<span class="badge badge-{sev.lower()}">{count} {sev}</span> ')
    total_findings = summary.get("total_findings", 0)
    total_resources = summary.get("total_resources_discovered", 0)
    h.append(
        f'<div class="context" style="margin-top:4px">'
        f"{total_findings} findings across {total_resources} resources "
        f"(one resource can have multiple violations).</div>"
    )
    h.append("</div>")

    # 24-hour delta section
    if delta:
        h.append(_build_delta_section(delta))

    # Charts (same histograms as the email body)
    h.append(_build_charts_html(results, region_stats, region_costs))

    # Category breakdown
    h.append('<table><tr><th>Category</th><th style="text-align:right">Count</th></tr>')
    for cat, count in sorted(by_cat.items()):
        h.append(
            f"<tr><td>{_html_escape(cat.replace('_', ' ').title())}</td>"
            f'<td style="text-align:right"><strong>{count}</strong></td></tr>'
        )
    h.append("</table>")

    # Regional Overview — unified table
    active_regions = [(r, c) for r, c in sorted(region_costs.items(), key=lambda x: -x[1]) if c >= 0.01]

    if active_regions:
        h.append("<h2>Regional Overview</h2>")
        h.append(
            '<p class="context">NC Burn = estimated cost of non-compliant resources. '
            "Compliant = Total Spend minus NC Burn.</p>"
        )
        h.append(
            '<table><tr><th>Region</th><th style="text-align:right">Total Spend</th>'
            '<th style="text-align:right">NC Burn</th>'
            '<th style="text-align:right">Compliant</th>'
            '<th style="text-align:right">Findings</th>'
            '<th style="text-align:right">HIGH</th><th>Status</th></tr>'
        )
        for region_name, total_cost in active_regions:
            stats = region_stats.get(region_name, {})
            fc = stats.get("total", 0)
            hc = stats.get("high", 0)
            burn = stats.get("burn", 0.0)
            compliant_spend = max(0, total_cost - burn)
            if fc == 0:
                status = '<span class="compliant">COMPLIANT</span>'
            elif hc > 0:
                status = f'<span class="badge badge-high">{hc} HIGH</span>'
            else:
                status = f'<span class="badge badge-medium">{fc} issues</span>'
            burn_cell = f'<strong style="color:#b91c1c">${burn:.2f}</strong>' if burn > 0 else "—"
            comp_cell = f'<span style="color:#16a34a">${compliant_spend:.2f}</span>'
            h.append(
                f"<tr><td><strong>{_html_escape(region_name)}</strong></td>"
                f'<td style="text-align:right">${total_cost:.2f}</td>'
                f'<td style="text-align:right">{burn_cell}</td>'
                f'<td style="text-align:right">{comp_cell}</td>'
                f'<td style="text-align:right">{fc}</td>'
                f'<td style="text-align:right">{hc}</td>'
                f"<td>{status}</td></tr>"
            )
        h.append("</table>")

    # All findings by category
    for section_key, section_title, include_teardown in [
        ("drift_detection", "Drift Detection (Click-Ops)", True),
        ("tag_compliance", "Tag Compliance", True),
        ("schedule_compliance", "Schedule Compliance", True),
        ("lifecycle_compliance", "Lifecycle Compliance", True),
    ]:
        section_data = results.get(section_key, {})
        findings = section_data.get("findings", [])
        h.append(f"<h2>{section_title}</h2>")

        if section_key == "drift_detection":
            h.append(
                f"<p>Terraform-managed: <strong>{section_data.get('terraform_managed', 0)}</strong> "
                f"| Unmanaged: <strong>{section_data.get('unmanaged', 0)}</strong></p>"
            )

        if not findings:
            h.append("<p><em>No findings.</em></p>")
            continue

        active = [f for f in findings if f.get("state") not in INACTIVE_STATES]
        inactive = [f for f in findings if f.get("state") in INACTIVE_STATES]

        for severity in ("HIGH", "MEDIUM", "LOW"):
            sev_findings = [f for f in active if f.get("severity") == severity]
            if not sev_findings:
                continue
            h.append(f'<h3><span class="badge badge-{severity.lower()}">{severity}</span> ({len(sev_findings)})</h3>')
            for f in sev_findings[:50]:
                h.append(_render_single_finding_html(f, include_teardown))
            if len(sev_findings) > 50:
                h.append(f"<p><em>... and {len(sev_findings) - 50} more</em></p>")

        if inactive:
            h.append('<div class="section-terminated">')
            h.append(f"<h3>Already Terminated / Stopped ({len(inactive)})</h3>")
            h.append('<p class="context">No action needed unless cleanup desired.</p>')
            for f in inactive[:30]:
                h.append(_render_single_finding_html(f, include_teardown=False))
            if len(inactive) > 30:
                h.append(f"<p><em>... and {len(inactive) - 30} more</em></p>")
            h.append("</div>")

    # Multi-state detection
    multi_state = results.get("multi_state_detection", {})
    state_files = multi_state.get("state_files", [])
    lock_tables = multi_state.get("lock_tables", [])
    if state_files or lock_tables:
        h.append("<h2>Multi-State Detection</h2>")
        h.append("<p>Other Terraform management traces beyond the known state.</p>")
        if state_files:
            h.append(f"<h3>State Files ({len(state_files)})</h3><ul>")
            for sf in state_files:
                h.append(
                    f"<li><code>s3://{_html_escape(sf.get('bucket', ''))}/{_html_escape(sf.get('key', ''))}</code> "
                    f"&mdash; {sf.get('size_bytes', 0) / 1024:.1f} KB, "
                    f"modified {_html_escape(sf.get('last_modified', ''))}</li>"
                )
            h.append("</ul>")
        if lock_tables:
            h.append(f"<h3>Lock Tables ({len(lock_tables)})</h3><ul>")
            for tbl in lock_tables:
                h.append(
                    f"<li><code>{_html_escape(tbl.get('table_name', ''))}</code> in "
                    f"{_html_escape(tbl.get('region', ''))} &mdash; "
                    f"{_html_escape(tbl.get('item_count', 0))} items</li>"
                )
            h.append("</ul>")

    # Other IaC cross-reference analysis — split by ownership
    other_iac = results.get("other_iac_analysis", [])
    ours_attach = [sa for sa in other_iac if sa.get("is_ours")]
    external_attach = [sa for sa in other_iac if not sa.get("is_ours")]

    if ours_attach:
        h.append("<h2>Terraform State Storage</h2>")
        h.append("<p>State files in the Terraform state backend for this cloud management repo.</p>")
        for sa in ours_attach:
            h.append(_render_iac_detail_table(sa, header_color="#16a34a"))

    if external_attach:
        h.append("<h2>External IaC</h2>")
        h.append("<p>Terraform state files from other repos or teams in this account.</p>")
        for sa in external_attach:
            h.append(_render_iac_detail_table(sa, header_color="#d97706"))

    # Errors
    errors = results.get("errors", [])
    if errors:
        h.append("<h2>Errors</h2><ul>")
        for err in errors[:10]:
            h.append(f"<li>{_html_escape(err[:300])}</li>")
        h.append("</ul>")

    # Footer
    links = _footer_links()

    h.append(f"""<div class="footer">
        <strong>About This Report</strong><br>
        Generated by <code>{project}-{env}-compliance-scanner</code> Lambda
        ({duration}s scan of {summary.get("total_resources_discovered", 0)} resources)<br>
        {links}<br>
        <strong>Required tags:</strong> Project, Environment, ManagedBy, Lifecycle, Repository<br>
        <strong>Managed by Terraform:</strong> All infrastructure should be codified (ManagedBy=terraform).
        Unmanaged resources are flagged as click-ops.<br>
        <strong>Lifecycle tracking:</strong> Temporary resources must have ExpiresAt tags.
        Persistent resources must have Lifecycle=persistent.<br><br>
        <strong>Capabilities:</strong> Full account discovery across all active regions (via Cost Explorer),
        Terraform state drift detection, per-resource cost estimates (instance-type lookup),
        multi-state detection, teardown CLI commands for orphaned resources.<br>
        <strong>Limitations:</strong> Cannot see resources in zero-spend regions, security group rules,
        IAM policy contents, cross-account resources, real-time CUR costs,
        or who created a resource (CloudTrail attribution not yet integrated).
    </div>""")

    h.append("</body></html>")
    return "\n".join(h)


def _render_iac_analysis(sa, style="warning"):
    """Render an IaC state file analysis block."""
    path = sa.get("state_path", "")
    modified = _html_escape(sa.get("last_modified", "")[:10])
    managed = _html_escape(sa.get("managed_count", 0))
    visible = _html_escape(sa.get("visible_count", 0))
    cost = sa.get("total_cost", 0)
    rec = sa.get("recommendation", "")
    error = sa.get("error", "")

    border_color = "#16a34a" if style == "compliant" else "#d97706"
    parts = [f'<div class="finding" style="border-left: 4px solid {border_color}">']
    parts.append(f'<div class="finding-header"><code>{_html_escape(path)}</code></div>')

    if error:
        parts.append(f'<div class="finding-meta" style="color:#b91c1c">Error: {_html_escape(error)}</div>')
    else:
        meta = f"Last modified: {modified} | Manages {managed} ARNs | {visible} visible"
        if cost > 0:
            meta += f" | <strong>${cost:.2f}/mo</strong>"
        parts.append(f'<div class="finding-meta">{meta}</div>')

        overlapping = sa.get("overlapping", [])
        if overlapping:
            for r in overlapping[:5]:
                emoji = _RESOURCE_EMOJI.get(r.get("resource_type", ""), "\U0001f4cb")
                rcost = r.get("estimated_monthly_cost") or 0
                cost_str = f" &mdash; ${rcost:.2f}/mo" if rcost > 0 else ""
                parts.append(
                    f'<div class="finding-issue">{emoji} '
                    f"{_html_escape(r.get('resource_name', ''))} "
                    f"<code>{_html_escape(r.get('resource_type', ''))}</code>"
                    f"{cost_str}</div>"
                )
            if len(overlapping) > 5:
                parts.append(f'<div class="context">&hellip; and {len(overlapping) - 5} more</div>')

        parts.append(f'<div class="finding-meta"><em>{_html_escape(rec)}</em></div>')

    parts.append("</div>")
    return "\n".join(parts)


def _render_iac_detail_table(sa, header_color="#d97706"):
    """Render a detailed IaC analysis block with resource table (for full report)."""
    path = sa.get("state_path", "")
    modified = _html_escape(sa.get("last_modified", "")[:10])
    managed = _html_escape(sa.get("managed_count", 0))
    visible = _html_escape(sa.get("visible_count", 0))
    cost = sa.get("total_cost", 0)
    rec = sa.get("recommendation", "")
    error = sa.get("error", "")

    parts = [
        f'<h3 style="border-left: 4px solid {header_color}; padding-left: 8px"><code>{_html_escape(path)}</code></h3>'
    ]
    if error:
        parts.append(f'<p style="color:#b91c1c">Error reading state: {_html_escape(error)}</p>')
        return "\n".join(parts)

    meta = f"Last modified: {modified} | Manages {managed} ARNs | {visible} visible"
    if cost > 0:
        meta += f" | Total cost: <strong>${cost:.2f}/mo</strong>"
    parts.append(f"<p>{meta}</p>")

    overlapping = sa.get("overlapping", [])
    if overlapping:
        parts.append(
            '<table><tr><th>Resource</th><th>Type</th><th>Region</th><th style="text-align:right">Cost</th></tr>'
        )
        for r in overlapping:
            rcost = r.get("estimated_monthly_cost") or 0
            cost_cell = f"${rcost:.2f}" if rcost > 0 else "\u2014"
            emoji = _RESOURCE_EMOJI.get(r.get("resource_type", ""), "\U0001f4cb")
            parts.append(
                f"<tr><td>{emoji} {_html_escape(r.get('resource_name', ''))}</td>"
                f"<td><code>{_html_escape(r.get('resource_type', ''))}</code></td>"
                f"<td>{_html_escape(r.get('region', ''))}</td>"
                f'<td style="text-align:right">{cost_cell}</td></tr>'
            )
        parts.append("</table>")

    parts.append(f'<p class="context"><em>{_html_escape(rec)}</em></p>')
    return "\n".join(parts)


def _render_finding_html(grouped_item):
    """Render a grouped HIGH finding for the email body."""
    f = grouped_item["representative"]
    name = f.get("resource_name", "unknown")
    rtype = f.get("resource_type", "")
    arn = f.get("resource_arn", "")
    state = f.get("state", "")
    region = f.get("region", "")
    cost = grouped_item["total_cost"]
    resource_id = _extract_id(arn)

    cost_str = f"${cost:.2f}/mo" if cost else ""
    categories = grouped_item.get("categories", [f.get("category", "")])
    cat_str = _html_escape(" + ".join(c.replace("_", " ").title() for c in categories))

    console_link = _console_link(arn, region, rtype)
    name_html = f'<a href="{_safe_url(console_link)}">{_html_escape(name)}</a>' if console_link else _html_escape(name)

    state_label = _html_escape(state) if state else "always-on"

    emoji = _RESOURCE_EMOJI.get(rtype, "\U0001f4cb")  # 📋 default
    parts = ['<div class="finding finding-high">']
    parts.append(f'<div class="finding-header">{emoji} {name_html} <code>{_html_escape(rtype)}</code></div>')
    parts.append('<div class="finding-meta">')
    parts.append(f"State: <strong>{state_label}</strong> | ")
    if cost_str:
        parts.append(f"Cost: <strong>{cost_str}</strong> | ")
    parts.append(f"Flagged: {cat_str}")
    parts.append("</div>")

    # Context
    purpose = f.get("inferred_purpose", "")
    attached_to = f.get("attached_to", "")
    if purpose:
        parts.append(f'<div class="context">{_html_escape(purpose)}</div>')
    elif attached_to:
        parts.append(f'<div class="context">Attached to {_html_escape(attached_to)}</div>')

    # Issues
    issues = grouped_item.get("issues", [])
    if issues:
        for issue in issues:
            parts.append(f'<div class="finding-issue">&bull; {_html_escape(issue)}</div>')
    else:
        issue = f.get("issue", "")
        if issue:
            parts.append(f'<div class="finding-issue">&bull; {_html_escape(issue)}</div>')

    # Recommendation
    rec = f.get("recommendation", "")
    if rec:
        parts.append(f'<div class="finding-meta"><em>{_html_escape(rec)}</em></div>')

    # Teardown command for all HIGH findings
    cmd = _teardown_command(rtype, resource_id, arn, region)
    if cmd:
        parts.append(f'<div class="teardown">{_html_escape(cmd)}</div>')

    parts.append("</div>")
    return "\n".join(parts)


def _render_single_finding_html(finding, include_teardown=False):
    """Render a single finding for the full HTML report."""
    arn = finding.get("resource_arn", finding.get("arn", ""))
    name = finding.get("resource_name", "")
    rtype = finding.get("resource_type", "")
    severity = finding.get("severity", "LOW")
    state = finding.get("state") or ""
    region = finding.get("region", "")
    cost = finding.get("estimated_monthly_cost")
    cost_str = f"${cost:.2f}/mo" if cost is not None else ""
    duration = finding.get("running_duration_days")
    age_str = _html_escape(f"{duration}d old") if duration is not None else ""
    issue = finding.get("issue") or ""
    recommendation = finding.get("recommendation") or ""
    resource_id = _extract_id(arn)

    console_link = _console_link(arn, region, rtype)
    name_html = f'<a href="{_safe_url(console_link)}">{_html_escape(name)}</a>' if console_link else _html_escape(name)

    css_class = _html_escape(f"finding-{severity.lower()}")

    emoji = _RESOURCE_EMOJI.get(rtype, "\U0001f4cb")  # 📋 default
    parts = [f'<div class="finding {css_class}">']
    parts.append(f'<div class="finding-header">{emoji} {name_html} <code>{_html_escape(rtype)}</code></div>')

    state_label = _html_escape(state) if state else "always-on"

    meta_parts = []
    meta_parts.append(f"State: <strong>{state_label}</strong>")
    if cost_str:
        meta_parts.append(f"Cost: <strong>{cost_str}</strong>")
    if age_str:
        meta_parts.append(age_str)
    if resource_id and resource_id != name:
        meta_parts.append(f"ID: <code>{_html_escape(resource_id)}</code>")
    if meta_parts:
        parts.append(f'<div class="finding-meta">{" | ".join(meta_parts)}</div>')

    if arn:
        parts.append(f'<div class="finding-meta">ARN: <code style="font-size:10px">{_html_escape(arn)}</code></div>')

    # Context
    purpose = finding.get("inferred_purpose") or ""
    attached_to = finding.get("attached_to") or ""
    if purpose:
        parts.append(f'<div class="context">{_html_escape(purpose)}</div>')
    elif attached_to:
        parts.append(f'<div class="context">Attached to {_html_escape(attached_to)}</div>')

    if issue:
        parts.append(f'<div class="finding-issue">&bull; {_html_escape(issue)}</div>')
    if recommendation:
        parts.append(f'<div class="finding-meta"><em>{_html_escape(recommendation)}</em></div>')

    if include_teardown:
        cmd = _teardown_command(rtype, resource_id, arn, region)
        if cmd:
            parts.append(f'<div class="teardown">{_html_escape(cmd)}</div>')

    parts.append("</div>")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════
# Charts
# ═══════════════════════════════════════════════════════


def _build_charts_html(results, region_stats, region_costs):
    """Build CSS horizontal bar charts for the email body."""
    try:
        return _build_charts_html_inner(results, region_stats, region_costs)
    except Exception as e:
        print(f"Chart generation error (non-fatal): {e}")
        return '<p class="context"><em>Charts unavailable due to data error.</em></p>'


def _build_charts_html_inner(results, region_stats, region_costs):
    """Inner chart builder — separated for error isolation."""
    h = []

    # Collect per-type NC stats from findings (deduplicated by ARN)
    nc_type_count = {}
    nc_type_burn = {}
    nc_seen = set()
    for section in ("tag_compliance", "drift_detection", "schedule_compliance", "lifecycle_compliance"):
        for f in results.get(section, {}).get("findings", []):
            if f.get("state") in INACTIVE_STATES:
                continue
            arn = f.get("resource_arn", "")
            if arn and arn in nc_seen:
                continue
            if arn:
                nc_seen.add(arn)
            rtype = f.get("resource_type", "unknown")
            nc_type_count[rtype] = nc_type_count.get(rtype, 0) + 1
            cost = f.get("estimated_monthly_cost") or 0
            nc_type_burn[rtype] = nc_type_burn.get(rtype, 0) + cost

    # Total resource counts by type (from discovery)
    total_type_counts = results.get("resource_type_counts", {})

    # Compliant type counts = total - non-compliant
    c_type_count = {}
    for rtype, total in total_type_counts.items():
        nc = nc_type_count.get(rtype, 0)
        compliant = total - nc
        if compliant > 0:
            c_type_count[rtype] = compliant

    # Per-region NC stats already in region_stats
    # Per-region compliant spend = total - NC burn
    region_nc_burn = {}
    region_nc_count = {}
    region_c_spend = {}
    region_c_count = {}
    total_type_by_region = results.get("region_type_counts", {})
    for region_name, total_cost in region_costs.items():
        if total_cost < 0.01:
            continue
        stats = region_stats.get(region_name, {})
        nc_b = stats.get("burn", 0.0)
        nc_c = stats.get("total", 0)
        region_nc_burn[region_name] = nc_b
        region_nc_count[region_name] = nc_c
        region_c_spend[region_name] = max(0, total_cost - nc_b)
        # Compliant resource count = total in region - NC in region
        total_in_region = sum(total_type_by_region.get(region_name, {}).values())
        region_c_count[region_name] = max(0, total_in_region - nc_c)

    # ── Non-Compliance Charts ──
    h.append("<h2>Non-Compliance Breakdown</h2>")

    # 1. NC count by resource type
    nc_by_type_sorted = sorted(nc_type_count.items(), key=lambda x: -x[1])[:15]
    if nc_by_type_sorted:
        h.append("<h3>Non-Compliant Resource Count by Type</h3>")
        h.append(
            _bar_chart([(f"{_RESOURCE_EMOJI.get(t, chr(0x1F4CB))} {t}", v) for t, v in nc_by_type_sorted], "#dc2626")
        )

    # 2. NC burn by resource type
    nc_burn_sorted = sorted(nc_type_burn.items(), key=lambda x: -x[1])[:15]
    nc_burn_sorted = [(t, v) for t, v in nc_burn_sorted if v > 0]
    if nc_burn_sorted:
        h.append("<h3>Non-Compliant Spend by Type ($/mo)</h3>")
        h.append(
            _bar_chart(
                [(f"{_RESOURCE_EMOJI.get(t, chr(0x1F4CB))} {t}", v) for t, v in nc_burn_sorted], "#dc2626", fmt="$.2f"
            )
        )

    # 3. NC count by region
    nc_region_sorted = sorted(region_nc_count.items(), key=lambda x: -x[1])
    nc_region_sorted = [(r, v) for r, v in nc_region_sorted if v > 0]
    if nc_region_sorted:
        h.append("<h3>Non-Compliant Resource Count by Region</h3>")
        h.append(_bar_chart(nc_region_sorted, "#dc2626"))

    # 4. NC burn by region
    nc_rburn_sorted = sorted(region_nc_burn.items(), key=lambda x: -x[1])
    nc_rburn_sorted = [(r, v) for r, v in nc_rburn_sorted if v > 0]
    if nc_rburn_sorted:
        h.append("<h3>Non-Compliant Spend by Region ($/mo)</h3>")
        h.append(_bar_chart(nc_rburn_sorted, "#dc2626", fmt="$.2f"))

    # ── Compliance Charts ──
    h.append("<h2>Compliance Breakdown</h2>")

    # 5. Compliant count by resource type
    c_by_type_sorted = sorted(c_type_count.items(), key=lambda x: -x[1])[:15]
    if c_by_type_sorted:
        h.append("<h3>Compliant Resource Count by Type</h3>")
        h.append(
            _bar_chart([(f"{_RESOURCE_EMOJI.get(t, chr(0x1F4CB))} {t}", v) for t, v in c_by_type_sorted], "#16a34a")
        )

    # 6. Compliant count by region
    c_region_sorted = sorted(region_c_count.items(), key=lambda x: -x[1])
    c_region_sorted = [(r, v) for r, v in c_region_sorted if v > 0]
    if c_region_sorted:
        h.append("<h3>Compliant Resource Count by Region</h3>")
        h.append(_bar_chart(c_region_sorted, "#16a34a"))

    # 7. Compliant spend by region
    c_rspend_sorted = sorted(region_c_spend.items(), key=lambda x: -x[1])
    c_rspend_sorted = [(r, v) for r, v in c_rspend_sorted if v > 0]
    if c_rspend_sorted:
        h.append("<h3>Compliant Spend by Region ($/mo)</h3>")
        h.append(_bar_chart(c_rspend_sorted, "#16a34a", fmt="$.2f"))

    return "\n".join(h)


def _bar_chart(items, color, fmt=None):
    """Render a horizontal CSS bar chart. Works in HTML email (no JS)."""
    if not items:
        return ""
    max_val = max(v for _, v in items)
    if max_val <= 0:
        return ""
    rows = []
    for label, val in items:
        pct = min(100, (val / max_val) * 100)
        if fmt and fmt.startswith("$"):
            display = f"${val:,.2f}"
        else:
            display = f"{val:,}"
        rows.append(
            f'<div style="margin:3px 0;font-size:12px">'
            f'<div style="display:inline-block;width:140px;overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap;vertical-align:middle">{_html_escape(label)}</div>'
            f'<div style="display:inline-block;width:55%;vertical-align:middle">'
            f'<div style="background:{color};width:{pct:.0f}%;height:16px;'
            f'border-radius:3px;display:inline-block;vertical-align:middle"></div>'
            f"</div>"
            f'<span style="margin-left:6px;font-weight:600;vertical-align:middle">{display}</span>'
            f"</div>"
        )
    return "\n".join(rows)


# ═══════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════


def _html_escape(text):
    """Escape text for HTML element content and for quoted attribute values."""
    return html.escape(str(text), quote=True)


def _safe_url(url):
    """Return an http(s) URL escaped for an href attribute; anything else becomes an empty string."""
    url = str(url or "").strip()
    if not re.match(r"https?://", url, re.IGNORECASE):
        return ""
    return _html_escape(url)


def _header_text(value):
    """Collapse CR, LF and other control characters so a value cannot add a mail header."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()


def _extract_id(arn):
    if not arn:
        return ""
    parts = arn.split("/")
    if len(parts) > 1:
        return parts[-1]
    parts = arn.split(":")
    return parts[-1] if parts else ""


def _calculate_burn_rate(results):
    """Excludes terminated/stopped resources — they no longer incur cost."""
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


def _build_region_stats(results):
    stats = {}
    for section in ("tag_compliance", "drift_detection", "schedule_compliance", "lifecycle_compliance"):
        for f in results.get(section, {}).get("findings", []):
            region = f.get("region", "global")
            arn = f.get("resource_arn", "")
            if region not in stats:
                stats[region] = {"total": 0, "high": 0, "burn": 0.0, "seen_arns": set()}
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
    for region in stats:
        del stats[region]["seen_arns"]
    return stats


def _build_delta_section(delta):
    """Build HTML for the 24-hour changes section in the email body."""
    s = delta["summary"]
    h = []

    # Delta summary box
    burn_delta = s["burn_delta"]
    if burn_delta > 0:
        burn_color = "#b91c1c"
        burn_str = f"+${burn_delta:.0f}/mo"
    elif burn_delta < 0:
        burn_color = "#16a34a"
        burn_str = f"-${abs(burn_delta):.0f}/mo"
    else:
        burn_color = "#6b7280"
        burn_str = "no change"

    h.append("<h2>24-Hour Changes</h2>")
    h.append('<table style="margin-bottom:12px;">')
    h.append("<tr>")
    h.append(
        f'<td style="text-align:center; padding:8px 16px;">'
        f'<div style="font-size:22px; font-weight:700; color:#dc2626;">+{s["new_count"]}</div>'
        f'<div style="font-size:11px; color:#6b7280;">New</div></td>'
    )
    h.append(
        f'<td style="text-align:center; padding:8px 16px;">'
        f'<div style="font-size:22px; font-weight:700; color:#16a34a;">{s["resolved_count"]}</div>'
        f'<div style="font-size:11px; color:#6b7280;">Resolved</div></td>'
    )
    h.append(
        f'<td style="text-align:center; padding:8px 16px;">'
        f'<div style="font-size:22px; font-weight:700; color:#d97706;">{s["persistent_count"]}</div>'
        f'<div style="font-size:11px; color:#6b7280;">Persistent</div></td>'
    )
    h.append(
        f'<td style="text-align:center; padding:8px 16px;">'
        f'<div style="font-size:22px; font-weight:700; color:{burn_color};">{burn_str}</div>'
        f'<div style="font-size:11px; color:#6b7280;">Burn Delta</div></td>'
    )
    h.append("</tr></table>")

    # New findings (top 5)
    new_findings = delta.get("new_findings", [])
    if new_findings:
        h.append(
            f'<div style="margin:8px 0;"><strong style="color:#dc2626;">New findings ({len(new_findings)}):</strong></div>'
        )
        h.append(
            '<table><tr><th>Sev</th><th>Type</th><th>Name</th><th style="text-align:right">Cost</th><th>Issue</th></tr>'
        )
        for f in new_findings[:5]:
            sev = f.get("severity", "LOW")
            sev_c = _SEV_COLORS.get(sev, {}).get("badge", "#6b7280")
            rtype = f.get("resource_type", "")
            name = f.get("resource_name", "") or _extract_id(f.get("resource_arn", ""))
            cost = f.get("estimated_monthly_cost")
            cost_str = f"${float(cost):.2f}" if cost else "—"
            issue = _html_escape(str(f.get("issue", ""))[:80])
            h.append(
                f'<tr><td><span class="badge" style="background:{sev_c}">{_html_escape(sev)}</span></td>'
                f"<td>{_html_escape(rtype)}</td><td>{_html_escape(name)}</td>"
                f'<td style="text-align:right">{cost_str}</td><td>{issue}</td></tr>'
            )
        if len(new_findings) > 5:
            h.append(
                f'<tr><td colspan="5" style="color:#6b7280; font-style:italic;">'
                f"...and {len(new_findings) - 5} more</td></tr>"
            )
        h.append("</table>")

    # Resolved findings (top 5)
    resolved = delta.get("resolved_findings", [])
    if resolved:
        h.append(
            f'<div style="margin:8px 0;"><strong style="color:#16a34a;">Resolved ({len(resolved)}):</strong></div>'
        )
        h.append('<table><tr><th>Type</th><th>Name</th><th style="text-align:right">Was Costing</th></tr>')
        for f in resolved[:5]:
            rtype = f.get("resource_type", "")
            name = f.get("resource_name", "") or _extract_id(f.get("resource_arn", ""))
            cost = f.get("estimated_monthly_cost")
            cost_str = f"${float(cost):.2f}" if cost else "—"
            h.append(
                f"<tr><td>{_html_escape(rtype)}</td><td>{_html_escape(name)}</td>"
                f'<td style="text-align:right">{cost_str}</td></tr>'
            )
        if len(resolved) > 5:
            h.append(
                f'<tr><td colspan="3" style="color:#6b7280; font-style:italic;">'
                f"...and {len(resolved) - 5} more</td></tr>"
            )
        h.append("</table>")

    if not new_findings and not resolved:
        h.append('<div style="color:#16a34a; font-weight:600; margin:8px 0;">No changes in the last 24 hours.</div>')

    return "\n".join(h)


def _collect_high_findings(results):
    high = []
    for key in ("tag_compliance", "drift_detection", "schedule_compliance", "lifecycle_compliance"):
        for f in results.get(key, {}).get("findings", []):
            if f.get("severity") == "HIGH":
                high.append(f)
    high.sort(key=lambda f: (-(f.get("estimated_monthly_cost") or 0), f.get("category", "")))
    return high


def _group_findings(findings):
    groups = {}
    for f in findings:
        arn = f.get("resource_arn", "")
        key = arn or (f.get("resource_name", ""), f.get("resource_type", ""))
        if key not in groups:
            groups[key] = {"representative": f, "count": 0, "total_cost": 0, "categories": [], "issues": []}
        groups[key]["count"] += 1
        cost = f.get("estimated_monthly_cost") or 0
        if groups[key]["total_cost"] == 0 and cost > 0:
            groups[key]["total_cost"] = cost
        cat = f.get("category", "")
        if cat and cat not in groups[key]["categories"]:
            groups[key]["categories"].append(cat)
        issue = f.get("issue", "")
        if issue and issue not in groups[key]["issues"]:
            groups[key]["issues"].append(issue)
    return sorted(groups.values(), key=lambda g: -g["total_cost"])


def _teardown_command(resource_type, resource_id, arn, region):
    r = f"--region {region}" if region else ""
    if resource_type == "ec2:instance":
        return f"{_CLI_PREFIX} ec2 terminate-instances --instance-ids {resource_id} {r}"
    if resource_type == "ec2:volume":
        return f"{_CLI_PREFIX} ec2 delete-volume --volume-id {resource_id} {r}"
    if resource_type == "ec2:security-group":
        return f"{_CLI_PREFIX} ec2 delete-security-group --group-id {resource_id} {r}"
    if resource_type == "ec2:eip":
        return f"{_CLI_PREFIX} ec2 release-address --allocation-id {resource_id} {r}"
    if resource_type == "rds:instance":
        return f"{_CLI_PREFIX} rds delete-db-instance --db-instance-identifier {resource_id} --skip-final-snapshot {r}"
    if resource_type == "ecs:service":
        parts = arn.split("/")
        if len(parts) >= 3:
            return (
                f"{_CLI_PREFIX} ecs update-service --cluster {parts[-2]} --service {parts[-1]} "
                f"--desired-count 0 {r} && "
                f"{_CLI_PREFIX} ecs delete-service --cluster {parts[-2]} --service {parts[-1]} {r}"
            )
    if resource_type == "lambda:function":
        return f"{_CLI_PREFIX} lambda delete-function --function-name {resource_id} {r}"
    if resource_type == "s3:bucket":
        return f"{_CLI_PREFIX} s3 rb s3://{resource_id} --force"
    if resource_type == "elasticache:cluster":
        return f"{_CLI_PREFIX} elasticache delete-cache-cluster --cache-cluster-id {resource_id} {r}"
    if resource_type in ("elbv2:application", "elbv2:network"):
        return f"{_CLI_PREFIX} elbv2 delete-load-balancer --load-balancer-arn {arn} {r}"
    # Fallback: if the ARN contains known service patterns, generate command from ARN
    if arn and "elasticloadbalancing" in arn and "loadbalancer" in arn:
        return f"{_CLI_PREFIX} elbv2 delete-load-balancer --load-balancer-arn {arn} {r}"
    if resource_type == "ec2:natgateway" or (arn and ":natgateway/" in arn):
        return f"{_CLI_PREFIX} ec2 delete-nat-gateway --nat-gateway-id {resource_id} {r}"
    return None


def _console_link(arn, region, resource_type):
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
    if resource_type == "ecs:service":
        return f"{base}/ecs/home?region={region}#/services"
    if resource_type == "ecs:cluster":
        return f"{base}/ecs/home?region={region}#/clusters/{resource_id}"
    if resource_type == "lambda:function":
        return f"{base}/lambda/home?region={region}#/functions/{resource_id}"
    if resource_type == "s3:bucket":
        return f"https://s3.console.aws.amazon.com/s3/buckets/{resource_id}"
    if resource_type == "eks:cluster":
        return f"{base}/eks/home?region={region}#/clusters/{resource_id}"
    if resource_type == "efs:filesystem":
        return f"{base}/efs/home?region={region}#/file-systems/{resource_id}"
    if resource_type == "elasticache:cluster":
        return f"{base}/elasticache/home?region={region}#/redis/{resource_id}"
    if resource_type == "dynamodb:table":
        return f"{base}/dynamodb/home?region={region}#tables:selected={resource_id}"
    return ""

"""
Server-rendered HTML views for the compliance dashboard.

Split across modules for navigability:
  views_base.py  — shared constants, helpers, layout, CSS
  views.py       — data-driven pages (home, findings, compliant, pending, overrides)
  views_cure.py  — CURE remediation page
  views_docs.py  — static Guide and Docs pages
"""

import html
import urllib.parse
from datetime import UTC, datetime

from auth import is_admin
from compliance_schema import INACTIVE_STATES, TAG_KEYS, VALID_MANAGED_BY


def _safe_float(val, default=0.0):
    """Safely convert a value to float, returning default on failure."""
    try:
        return float(val) if val else default
    except (ValueError, TypeError):
        return default


# Re-export everything that index.py imports
from data import get_protected_resources  # noqa: F401 — used by render_home, render_findings
from views_base import (  # noqa: F401
    _SEV_BADGE,
    ENVIRONMENT,
    SEV_COLORS,
    STATUS_COLORS,
    TYPE_ICONS,
    _admin_only,
    _badge,
    _compliance_path,
    _console_url,
    _e,
    _get_contact,
    _get_missing_required_tags,
    _js_json,
    _layout,
    _rate_color,
    _safe_url,
    paginate,
    render_error,
    render_success,
)
from views_cure import render_cure  # noqa: F401
from views_docs import render_docs, render_guide  # noqa: F401
from views_overview import render_overview  # noqa: F401
from views_tree import render_tree  # noqa: F401


def _render_job_status_section(job_statuses):
    """Render the SYSTEM JOBS table — visible to all users."""
    if not job_statuses:
        return ""

    _JOB_LABELS = {
        "compliance-scanner": "Compliance Scanner",
        "hunterkiller": "Hunterkiller",
        "stack-reaper": "Stack Reaper",
    }

    now = datetime.now(UTC)
    rows = ""
    for job_key in ("compliance-scanner", "hunterkiller", "stack-reaper"):
        info = job_statuses.get(job_key)
        label = _JOB_LABELS.get(job_key, job_key)
        if not info or not info.get("last_run"):
            rows += f"""<tr>
              <td style="color:#e2e8f0; font-weight:500;">{_e(label)}</td>
              <td style="color:#4b5563;">Never</td>
              <td style="color:#4b5563;">-</td>
              <td><span style="color:#6b7280;">N/A</span></td>
              <td style="color:#4b5563;">-</td>
            </tr>"""
            continue

        last_run_str = info["last_run"]
        duration = info.get("duration_seconds", 0)
        status = info.get("status", "unknown")
        summary = info.get("summary", "")

        # Parse last_run and compute relative time
        try:
            last_dt = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=UTC)
            delta = now - last_dt
            total_mins = int(delta.total_seconds() // 60)
            if total_mins < 1:
                ago = "just now"
            elif total_mins < 60:
                ago = f"{total_mins}m ago"
            elif total_mins < 1440:
                ago = f"{total_mins // 60}h ago"
            else:
                ago = f"{total_mins // 1440}d ago"
            utc_display = last_dt.strftime("%H:%M UTC")
            time_display = f"{ago} ({utc_display})"
        except (ValueError, TypeError):
            time_display = _e(last_run_str[:16])

        duration_display = f"{duration:.1f}s" if duration else "-"

        if status == "ok":
            status_html = '<span style="color:#22c55e; font-weight:600;">OK</span>'
        elif status == "stale":
            status_html = '<span style="color:#f59e0b; font-weight:600;">STALE</span>'
        elif status == "error":
            status_html = '<span style="color:#ef4444; font-weight:600;">ERROR</span>'
        else:
            status_html = f'<span style="color:#6b7280;">{_e(status)}</span>'

        rows += f"""<tr>
          <td style="color:#e2e8f0; font-weight:500;">{_e(label)}</td>
          <td style="color:#94a3b8;">{time_display}</td>
          <td style="color:#94a3b8;">{duration_display}</td>
          <td>{status_html}</td>
          <td style="color:#4b5563; font-size:11px;">{_e(summary[:60])}</td>
        </tr>"""

    return f"""
    <h2>System Jobs</h2>
    <table style="margin-bottom:16px;">
      <thead><tr><th>Job</th><th>Last Run</th><th>Duration</th><th>Status</th><th>Summary</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>"""


def render_home(
    summary,
    pending_count,
    overrides_count,
    report_date,
    user_email="",
    flash="",
    job_statuses=None,
    scan_freshness=None,
):
    """Render the dashboard home page with summary cards."""
    high = summary.get("high", 0)
    medium = summary.get("medium", 0)
    low = summary.get("low", 0)
    resources = summary.get("resources", 0)

    # Compliance status indicator
    if high > 100:
        status = ("POOR", "#ef4444")
        status_reason = f"{high} HIGH findings — immediate attention required"
    elif high > 20:
        status = ("FAIR", "#f59e0b")
        status_reason = f"{high} HIGH findings — remediation recommended"
    else:
        status = ("GOOD", "#22c55e")
        status_reason = f"{high} HIGH finding{'s' if high != 1 else ''} — account well-managed"

    # The counts above describe the account only as of the last complete scan.
    # When that scan is old, failed or absent, the status says so instead.
    fresh = (scan_freshness or {}).get("status", "ok")
    age = (scan_freshness or {}).get("age_hours")
    age_text = f"last complete scan {age:.0f}h ago" if age is not None else "no complete scan on record"
    if fresh == "stale":
        status = ("STALE", "#f59e0b")
        status_reason = f"{age_text} — findings may be out of date"
    elif fresh == "error":
        status = ("SCAN FAILED", "#ef4444")
        status_reason = f"the latest scan did not complete; {age_text}"
    elif fresh == "unknown":
        status = ("UNKNOWN", "#6b7280")
        status_reason = "no completed scan to report on"

    cards = f"""
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
      <div>
        <h1 style="margin-bottom:4px;">SITUATION OVERVIEW</h1>
        <span style="color:#4b5563; font-size:12px;">Last scan: {_e(report_date or "No reports found")}</span>
      </div>
      <div style="text-align:right;">
        <div style="font-size:10px; color:#6b7280; text-transform:uppercase; letter-spacing:0.1em;">Compliance Status</div>
        <div style="font-size:18px; font-weight:700; color:{status[1]};">{status[0]}</div>
        <div style="font-size:10px; color:#6b7280; margin-top:2px;">{status_reason}</div>
      </div>
    </div>
    <div class="cards">
      <div class="card">
        <div class="label">Resources Tracked</div>
        <div class="value">{resources}</div>
      </div>
      <div class="card" style="border-color:rgba(239,68,68,0.2);">
        <div class="label">HIGH Severity</div>
        <div class="value" style="color:{SEV_COLORS["HIGH"]}">{high}</div>
      </div>
      <div class="card" style="border-color:rgba(245,158,11,0.2);">
        <div class="label">MEDIUM Severity</div>
        <div class="value" style="color:{SEV_COLORS["MEDIUM"]}">{medium}</div>
      </div>
      <div class="card">
        <div class="label">LOW Severity</div>
        <div class="value" style="color:{SEV_COLORS["LOW"]}">{low}</div>
      </div>
      <div class="card" style="border-color:rgba(245,158,11,0.2);">
        <div class="label">Pending Destroys</div>
        <div class="value" style="color:#f59e0b">{pending_count}</div>
      </div>
      <div class="card">
        <div class="label">Active Overrides</div>
        <div class="value" style="color:#6b7280">{overrides_count}</div>
      </div>
    </div>
    {_render_job_status_section(job_statuses or {})}
    <h2>Actions</h2>
    <div class="cards">
      <a href="/findings" class="card" style="text-decoration:none; text-align:center; cursor:pointer;">
        <div class="value" style="font-size:13px; color:#3b82f6;">VIEW FINDINGS &#x2192;</div>
      </a>
      <a href="/findings?severity=HIGH" class="card" style="text-decoration:none; text-align:center; cursor:pointer; border-color:rgba(239,68,68,0.2);">
        <div class="value" style="font-size:13px; color:#ef4444;">HIGH ONLY &#x2192;</div>
      </a>
      <a href="/pending" class="card" style="text-decoration:none; text-align:center; cursor:pointer;">
        <div class="value" style="font-size:13px; color:#f59e0b;">PENDING DESTROYS &#x2192;</div>
      </a>
      <a href="/overrides" class="card" style="text-decoration:none; text-align:center; cursor:pointer;">
        <div class="value" style="font-size:13px; color:#94a3b8;">MANAGE OVERRIDES &#x2192;</div>
      </a>
      <a href="/changes" class="card" style="text-decoration:none; text-align:center; cursor:pointer;">
        <div class="value" style="font-size:13px; color:#8b5cf6;">24H CHANGES &#x2192;</div>
      </a>
      <a href="/protected" class="card" style="text-decoration:none; text-align:center; cursor:pointer; border-color:rgba(59,130,246,0.2);">
        <div class="value" style="font-size:13px; color:#3b82f6;">PROTECTED RESOURCES &#x2192;</div>
      </a>
    </div>"""

    if is_admin(user_email):
        cards += """
    <div class="cards" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-top:-12px;">
      <a href="/access" class="card" style="text-decoration:none; text-align:center; cursor:pointer; border-color:rgba(34,197,94,0.2);">
        <div class="value" style="font-size:13px; color:#22c55e;">MANAGE ACCESS &#x2192;</div>
      </a>
    </div>"""

    # Flash message banner (shown after trigger actions)
    _FLASH_MESSAGES = {
        "scan_triggered": (
            "Compliance scan triggered. Results will appear after the scan completes (~30s).",
            "#3b82f6",
        ),
        "enforce_high-only": (
            "Hunterkiller triggered (HIGH severity only). Warning emails will be sent for new findings.",
            "#f59e0b",
        ),
        "enforce_all-severity": (
            "Hunterkiller triggered (ALL severities). Warning emails will be sent for HIGH, MEDIUM, and LOW findings.",
            "#ef4444",
        ),
        "sprawl_triggered": (
            "Sprawl test triggered. 5 non-compliant resources are being created. "
            "They will appear in the next compliance scan.",
            "#8b5cf6",
        ),
    }
    if flash and flash in _FLASH_MESSAGES:
        flash_msg, flash_color = _FLASH_MESSAGES[flash]
        cards += f"""
    <div style="margin-top:16px; padding:10px 14px; background:rgba({",".join(str(int(flash_color.lstrip("#")[i : i + 2], 16)) for i in (0, 2, 4))},0.12);
                border:1px solid {flash_color}; border-radius:6px; color:{flash_color}; font-size:13px;">
      {_e(flash_msg)}
    </div>"""

    # Admin action buttons + report downloads
    if is_admin(user_email):
        cards += f"""
    <h2>Admin Actions{_admin_only("Trigger actions restricted to admins")}</h2>
    <div style="display:flex; gap:8px; flex-wrap:wrap;">
      <form method="post" action="/trigger/scan" style="display:inline;">
        <button type="submit" class="btn btn-primary" style="font-size:12px; padding:8px 16px;"
          onclick="return confirm('Run a compliance scan now? This will re-scan all resources.')">
          Run Scan</button>
      </form>
      <form method="post" action="/trigger/enforce" style="display:inline;">
        <input type="hidden" name="min_severity" value="HIGH">
        <button type="submit" class="btn btn-warn" style="font-size:12px; padding:8px 16px;"
          onclick="return confirm('Trigger hunterkiller on HIGH-severity findings?\\n\\nThis will send 24-hour warning emails for new HIGH findings.')">
          Enforce HIGH</button>
      </form>
      <form method="post" action="/trigger/enforce" style="display:inline;">
        <input type="hidden" name="min_severity" value="ALL">
        <button type="submit" class="btn btn-danger" style="font-size:12px; padding:8px 16px;"
          onclick="return confirm('Trigger hunterkiller on ALL findings (HIGH + MEDIUM + LOW)?\\n\\nThis is aggressive mode — all non-compliant resources will receive 24-hour warnings.')">
          Enforce ALL</button>
      </form>
      <form method="post" action="/trigger/sprawl" style="display:inline;">
        <button type="submit" class="btn btn-purple" style="font-size:12px; padding:8px 16px;"
          onclick="return confirm('Create 5 non-compliant test resources?\\n\\nThis creates cheap AWS resources (S3 bucket, security group, log group, secret, SSM parameter) with missing compliance tags. They will be detected by the scanner and can be destroyed by the hunterkiller — validating the full enforcement pipeline.')">
          Create Test Sprawl</button>
      </form>
    </div>
    <div style="margin-top:8px; padding:8px 12px; background:rgba(239,68,68,0.08);
                border:1px solid rgba(239,68,68,0.3); border-radius:6px; font-size:11px; color:#ef4444; max-width:480px;">
      <strong>Danger:</strong> Enforcement triggers the hunterkiller pipeline. After a 24-hour grace period,
      approved resources will be <strong>permanently deleted</strong>. Review the
      <a href="/pending" style="color:#ef4444;">pending teardowns</a> page to abort or extend before expiry.
    </div>
    <div style="margin-top:12px; display:grid; grid-template-columns:1fr 1fr; gap:6px; max-width:480px;">
      <a href="/report/html" class="btn btn-secondary"
         style="font-size:12px; text-decoration:none; padding:6px 14px; text-align:center;">
        Download Latest Report (HTML)</a>
      <a href="/report/download" class="btn btn-secondary"
         style="font-size:12px; text-decoration:none; padding:6px 14px; text-align:center;">
        Download Latest Report (JSON)</a>
      <a href="/report/hunterkiller/html" class="btn btn-secondary"
         style="font-size:12px; text-decoration:none; padding:6px 14px; text-align:center;">
        Download Latest HK Report (HTML)</a>
      <a href="/report/hunterkiller" class="btn btn-secondary"
         style="font-size:12px; text-decoration:none; padding:6px 14px; text-align:center;">
        Download Latest HK Report (JSON)</a>
    </div>"""
    else:
        cards += """
    <h2>Admin Actions</h2>
    <div style="display:flex; gap:8px; flex-wrap:wrap;">
      <span class="btn btn-secondary"
         style="font-size:12px; padding:8px 16px; opacity:0.5; cursor:not-allowed;"
         title="Admin access required">Run Scan</span>
      <span class="btn btn-secondary"
         style="font-size:12px; padding:8px 16px; opacity:0.5; cursor:not-allowed;"
         title="Admin access required">Enforce HIGH</span>
      <span class="btn btn-secondary"
         style="font-size:12px; padding:8px 16px; opacity:0.5; cursor:not-allowed;"
         title="Admin access required">Enforce ALL</span>
      <span class="btn btn-secondary"
         style="font-size:12px; padding:6px 14px; opacity:0.5; cursor:not-allowed;"
         title="Admin access required to download reports">
        Download Reports</span>
    </div>"""

    return _layout("Overview", cards, user_email)


def render_compliant(compliant_summary, user_email="", expiring_soon=None):
    """Render the compliant resources page — resources that passed all checks."""
    by_type = compliant_summary.get("by_type", {})
    by_region = compliant_summary.get("by_region", {})
    total_compliant = compliant_summary.get("total_compliant", 0)
    total_resources = compliant_summary.get("total_resources", 0)
    pct = round(total_compliant / total_resources * 100, 1) if total_resources > 0 else 0
    pct_color = _rate_color(pct)

    nc_total = total_resources - total_compliant
    if expiring_soon is None:
        expiring_soon = []

    content = f"""
    <h1>COMPLIANT RESOURCES // {total_compliant} PASSING</h1>
    <div style="margin-bottom:12px; display:flex; gap:8px; flex-wrap:wrap;">
      <a href="/compliant" class="btn btn-primary" style="color:#22c55e; border-color:rgba(34,197,94,0.3); background:rgba(34,197,94,0.15);">COMPLIANT ({total_compliant})</a>
      <a href="/findings" class="btn btn-secondary" style="color:#ef4444;">NON-COMPLIANT ({nc_total})</a>
      <a href="/all" class="btn btn-secondary">BOTH ({total_resources})</a>
      <span style="border-left:1px solid #2a2a3a; margin:0 4px;"></span>
      <a href="/findings?severity=HIGH" class="btn btn-secondary">HIGH</a>
      <a href="/findings?severity=MEDIUM" class="btn btn-secondary">MEDIUM</a>
      <a href="/findings?severity=LOW" class="btn btn-secondary">LOW</a>
    </div>

    <div class="cards">
      <div class="card">
        <div class="label">Compliance Rate</div>
        <div class="value" style="color:{pct_color};">{pct}%</div>
      </div>
      <div class="card">
        <div class="label">Compliant</div>
        <div class="value" style="color:#22c55e;">{total_compliant}</div>
      </div>
      <div class="card">
        <div class="label">Non-Compliant</div>
        <div class="value" style="color:#ef4444;">{total_resources - total_compliant}</div>
      </div>
      <div class="card">
        <div class="label">Total Tracked</div>
        <div class="value">{total_resources}</div>
      </div>
    </div>"""

    # Expiring soon section
    content += f"""
        <h2 style="color:#f59e0b;">EXPIRING SOON // {len(expiring_soon)} RESOURCES</h2>"""
    if expiring_soon:
        content += """<div class="card" style="border-left:4px solid #f59e0b; margin-bottom:16px; padding:10px 14px;">
          These resources have an <code>ExpiresAt</code> date within the next 14 days.
          To extend, update the <code>ExpiresAt</code> tag in your IaC config and redeploy.
        </div>"""
        exp_rows = ""
        for r in expiring_soon:
            icon = TYPE_ICONS.get(r["resource_type"], "")
            name = r.get("name") or r["arn"].split(":")[-1][:40]
            days = r["days_remaining"]
            if days <= 3:
                urgency_color = "#ef4444"
            elif days <= 7:
                urgency_color = "#f59e0b"
            else:
                urgency_color = "#22c55e"
            cost = r.get("estimated_monthly_cost")
            try:
                cost_str = f"${float(cost):.2f}/mo" if cost is not None else "-"
            except (ValueError, TypeError):
                cost_str = "-"
            repo = _safe_url(r.get("tags", {}).get("Repository", ""))
            repo_cell = (
                f'<a href="{_e(repo)}" style="color:#3b82f6; font-size:11px;">{_e(repo.split("/")[-1])}</a>'
                if repo
                else "-"
            )
            service = _e(r.get("tags", {}).get("Service", "-"))
            exp_rows += f"""<tr>
              <td style="color:#6b7280;">{icon} {_e(r["resource_type"])}</td>
              <td>{_e(name)}</td>
              <td>{service}</td>
              <td style="color:#9ca3af;">{_e(r["region"])}</td>
              <td style="color:{urgency_color}; font-weight:600;">{days}d</td>
              <td style="color:#9ca3af;">{_e(r["expires_at"][:16])}</td>
              <td>{cost_str}</td>
              <td>{repo_cell}</td>
            </tr>"""
        content += f"""
        <table>
          <thead><tr><th>Type</th><th>Resource</th><th>Service</th><th>Region</th><th>Days Left</th><th>Expires At</th><th>Cost</th><th>Repo</th></tr></thead>
          <tbody>{exp_rows}</tbody>
        </table>"""
    else:
        content += '<div class="empty">No resources expiring within 14 days. All clear.</div>'

    # By type table
    if by_type:
        type_sorted = sorted(by_type.items(), key=lambda x: -x[1]["compliant"])
        type_rows = ""
        for rtype, stats in type_sorted:
            icon = TYPE_ICONS.get(rtype, "")
            total = stats.get("total", 0)
            compl = stats.get("compliant", 0)
            nc = total - compl
            rate = round(compl / total * 100) if total > 0 else 0
            type_rows += f"""<tr>
              <td style="color:#6b7280;">{icon} {_e(rtype)}</td>
              <td style="color:#22c55e; font-weight:600;">{compl}</td>
              <td style="color:#ef4444;">{nc}</td>
              <td>{total}</td>
              <td style="color:{_rate_color(rate)}; font-weight:600;">{rate}%</td>
            </tr>"""
        content += f"""
        <h2>BY RESOURCE TYPE</h2>
        <table>
          <thead><tr><th>Type</th><th>Compliant</th><th>Non-Compliant</th><th>Total</th><th>Rate</th></tr></thead>
          <tbody>{type_rows}</tbody>
        </table>"""

    # By region table
    if by_region:
        region_sorted = sorted(by_region.items(), key=lambda x: -x[1]["compliant"])
        region_rows = ""
        for region, stats in region_sorted:
            total = stats.get("total", 0)
            compl = stats.get("compliant", 0)
            nc = total - compl
            rate = round(compl / total * 100) if total > 0 else 0
            region_rows += f"""<tr>
              <td style="color:#e2e8f0;">{_e(region)}</td>
              <td style="color:#22c55e; font-weight:600;">{compl}</td>
              <td style="color:#ef4444;">{nc}</td>
              <td>{total}</td>
              <td style="color:{_rate_color(rate)}; font-weight:600;">{rate}%</td>
            </tr>"""
        content += f"""
        <h2>BY REGION</h2>
        <table>
          <thead><tr><th>Region</th><th>Compliant</th><th>Non-Compliant</th><th>Total</th><th>Rate</th></tr></thead>
          <tbody>{region_rows}</tbody>
        </table>"""

    return _layout("Compliant", content, user_email, active_page="compliant")


def render_all_resources(compliant_summary, user_email=""):
    """Render combined view of all resources — compliant + non-compliant."""
    by_type = compliant_summary.get("by_type", {})
    by_region = compliant_summary.get("by_region", {})
    total_compliant = compliant_summary.get("total_compliant", 0)
    total_resources = compliant_summary.get("total_resources", 0)
    nc_total = total_resources - total_compliant
    pct = round(total_compliant / total_resources * 100, 1) if total_resources > 0 else 0
    pct_color = _rate_color(pct)

    content = f"""
    <h1>ALL RESOURCES // {total_resources} TRACKED</h1>
    <div style="margin-bottom:12px; display:flex; gap:8px; flex-wrap:wrap;">
      <a href="/compliant" class="btn btn-secondary" style="color:#22c55e; border-color:rgba(34,197,94,0.3);">COMPLIANT ({total_compliant})</a>
      <a href="/findings" class="btn btn-secondary" style="color:#ef4444;">NON-COMPLIANT ({nc_total})</a>
      <a href="/all" class="btn btn-primary">BOTH ({total_resources})</a>
      <span style="border-left:1px solid #2a2a3a; margin:0 4px;"></span>
      <a href="/findings?severity=HIGH" class="btn btn-secondary">HIGH</a>
      <a href="/findings?severity=MEDIUM" class="btn btn-secondary">MEDIUM</a>
      <a href="/findings?severity=LOW" class="btn btn-secondary">LOW</a>
    </div>

    <div class="cards">
      <div class="card">
        <div class="label">Compliance Rate</div>
        <div class="value" style="color:{pct_color};">{pct}%</div>
      </div>
      <div class="card">
        <div class="label">Compliant</div>
        <div class="value" style="color:#22c55e;">{total_compliant}</div>
      </div>
      <div class="card">
        <div class="label">Non-Compliant</div>
        <div class="value" style="color:#ef4444;">{nc_total}</div>
      </div>
      <div class="card">
        <div class="label">Total Tracked</div>
        <div class="value">{total_resources}</div>
      </div>
    </div>"""

    # By type — show compliant AND non-compliant
    if by_type:
        type_sorted = sorted(by_type.items(), key=lambda x: -x[1].get("total", 0))
        type_rows = ""
        for rtype, stats in type_sorted:
            icon = TYPE_ICONS.get(rtype, "")
            total = stats.get("total", 0)
            compl = stats.get("compliant", 0)
            nc = total - compl
            rate = round(compl / total * 100) if total > 0 else 0
            type_rows += f"""<tr>
              <td style="color:#6b7280;">{icon} {_e(rtype)}</td>
              <td>{total}</td>
              <td style="color:#22c55e; font-weight:600;">{compl}</td>
              <td style="color:#ef4444; font-weight:600;">{nc}</td>
              <td style="color:{_rate_color(rate)}; font-weight:600;">{rate}%</td>
            </tr>"""
        content += f"""
        <h2>BY RESOURCE TYPE</h2>
        <table>
          <thead><tr><th>Type</th><th>Total</th><th style="color:#22c55e;">Compliant</th><th style="color:#ef4444;">Non-Compliant</th><th>Rate</th></tr></thead>
          <tbody>{type_rows}</tbody>
        </table>"""

    # By region
    if by_region:
        region_sorted = sorted(by_region.items(), key=lambda x: -x[1].get("total", 0))
        region_rows = ""
        for region, stats in region_sorted:
            total = stats.get("total", 0)
            compl = stats.get("compliant", 0)
            nc = total - compl
            rate = round(compl / total * 100) if total > 0 else 0
            region_rows += f"""<tr>
              <td style="color:#e2e8f0;">{_e(region)}</td>
              <td>{total}</td>
              <td style="color:#22c55e; font-weight:600;">{compl}</td>
              <td style="color:#ef4444; font-weight:600;">{nc}</td>
              <td style="color:{_rate_color(rate)}; font-weight:600;">{rate}%</td>
            </tr>"""
        content += f"""
        <h2>BY REGION</h2>
        <table>
          <thead><tr><th>Region</th><th>Total</th><th style="color:#22c55e;">Compliant</th><th style="color:#ef4444;">Non-Compliant</th><th>Rate</th></tr></thead>
          <tbody>{region_rows}</tbody>
        </table>"""

    return _layout("All Resources", content, user_email)


def _finding_row(f, show_contact=True):
    """Render a single finding as a table row."""
    sev = f.get("severity", "")
    rtype = f.get("resource_type", "")
    icon = TYPE_ICONS.get(rtype, "")
    cost = f.get("estimated_monthly_cost")
    if cost is not None and cost > 0:
        cost_str = f"${float(cost):.2f}/mo"
    elif cost is None:
        cost_str = '<span class="hint" style="color:#4b5563;font-size:10px;" data-tip="Scanner has no pricing model for this resource type.">no model</span>'
    else:
        cost_str = "-"
    arn = f.get("resource_arn", "")
    name = f.get("resource_name", "") or arn.split(":")[-1][:40]
    is_protected = f.get("protected", False)
    region = f.get("region", "")
    issue = f.get("issue", "")[:80]
    tags = f.get("tags", {})
    managed_by = tags.get(TAG_KEYS["managed_by"], "").lower()
    managed = managed_by in VALID_MANAGED_BY and managed_by != "manual"
    contact = _get_contact(f) or "-"
    age_days = f.get("running_duration_days")
    age_str = f"{int(age_days)}d" if age_days else "-"
    state = (f.get("state") or "").lower()

    # State color coding
    if state in ("running", "available", "active", "in-use", "in_use"):
        state_color = "#22c55e"  # green
    elif state in ("starting", "pending", "modifying", "creating", "updating"):
        state_color = "#f59e0b"  # amber
    elif state in ("stopped", "terminated", "deleted", "shutting-down", "deleting", "failed"):
        state_color = "#ef4444"  # red
    else:
        state_color = "#4b5563"  # gray (unknown/empty)
    state_display = state or "-"

    # IaC-managed: blue highlight; suspected click-ops: red highlight
    _IAC_LABELS = {
        "terraform": "TF-MANAGED",
        "cloudformation": "CFN-MANAGED",
        "sam": "CFN-MANAGED",
        "pulumi": "PULUMI-MANAGED",
        "cdk": "CDK-MANAGED",
        "serverless": "SLS-MANAGED",
        "copilot": "COPILOT-MANAGED",
    }
    if managed:
        iac_label = _IAC_LABELS.get(managed_by, "IAC-MANAGED")
        managed_badge = f" {_badge(iac_label, 'blue')}"
        row_border = "border-left:3px solid #3b82f6;"
    else:
        managed_badge = f" {_badge('CLICK-OPS', 'red', 'badge-sm')}"
        row_border = "border-left:3px solid #ef4444;"

    # Enforcement status badges
    hk_status = f.get("hk_status", "")
    approval = f.get("approval_status", "")
    enforcement_badge = ""
    if is_protected:
        enforcement_badge = f" {_badge('PROTECTED', 'blue')}"
    elif hk_status == "torn_down":
        enforcement_badge = f" {_badge('DESTROYED', 'gray')}"
    elif hk_status == "enforcing":
        enforcement_badge = f" {_badge('DESTROYING', 'red')}"
    elif hk_status == "warned" and approval == "approved":
        enforcement_badge = f" {_badge('DESTROY APPROVED', 'red')}"
    elif hk_status == "warned" and approval == "pending":
        enforcement_badge = f" {_badge('PENDING DESTROY', 'amber')}"
    elif hk_status == "warned":
        enforcement_badge = f" {_badge('WARNED', 'amber')}"

    # For non-compliant resources with no enforcement status, explain why
    if not enforcement_badge and not is_protected:
        if sev != "HIGH":
            short_reason = f"{sev} severity — only HIGH targeted"
        elif state in INACTIVE_STATES:
            short_reason = f"{state} — inactive resources not targeted"
        else:
            short_reason = "Evaluated on next scan (nightly 03:00 UTC)"
        enforcement_badge = _badge("NOT TARGETED", "gray", "badge-sm", title=short_reason)

    repo_url = tags.get("Repository", "")
    repo_link = ""
    if repo_url and repo_url.startswith("https://"):
        repo_name = repo_url.split("/")[-1] if "/" in repo_url else repo_url
        repo_link = f' <a href="{_e(repo_url)}" target="_blank" rel="noopener" title="{_e(repo_url)}" style="color:#6b7280; font-size:9px; text-decoration:none;">({_e(repo_name)})</a>'

    arn_encoded = html.escape(arn, quote=True)
    console = _console_url(arn, region)
    name_link = (
        f'<a href="{_e(console)}" target="_blank" rel="noopener" style="color:#e2e8f0; text-decoration:none;" title="Open in AWS Console">{_e(name)}</a>'
        if console
        else _e(name)
    )

    contact_td = f'<td style="color:#6b7280; font-size:11px;">{_e(contact[:20])}</td>' if show_contact else ""

    # Path to compliance for TF-managed resources
    path = _compliance_path(f, managed)
    path_html = (
        f' <span style="font-size:9px; color:#3b82f6;" title="{_e(path)}">&#x2192; {_e(path[:20])}</span>'
        if path
        else ""
    )

    # Actions: CURE button for TF-managed; DETAIL for everything else
    cure_url = f"/cure?arn={html.escape(arn, quote=True)}"
    is_tf = managed_by == "terraform"
    cure_btn = (
        f'<a href="{cure_url}" class="btn btn-primary" style="font-size:10px; margin-right:4px;">CURE</a>'
        if is_tf
        else f'<a href="{cure_url}" class="btn btn-secondary" style="font-size:10px; margin-right:4px;">DETAIL</a>'
    )
    fp_form = f"""<form method="post" action="/override" style="display:inline;">
          <input type="hidden" name="resource_arn" value="{arn_encoded}">
          <input type="hidden" name="resource_type" value="{_e(rtype)}">
          <input type="hidden" name="resource_name" value="{_e(name)}">
          <input type="hidden" name="override_type" value="false_positive">
          <input type="hidden" name="reason" value="">
          <button type="submit" class="btn btn-secondary" style="font-size:10px;"
            title="Mark as false positive — exempt from compliance checks"
            onclick="var r=prompt('Reason for marking as false positive:'); if(!r)return false; this.form.reason.value=r;">
            Exempt</button>
        </form>"""

    return f"""<tr style="{row_border}">
      <td>{_badge(_e(sev), _SEV_BADGE.get(sev, "gray"))}</td>
      <td style="color:#6b7280; font-size:12px;">{icon} {_e(rtype)}</td>
      <td class="truncate" title="{arn_encoded}">{name_link}{managed_badge}{repo_link}</td>
      <td style="white-space:nowrap;">{enforcement_badge}</td>
      <td style="color:#4b5563; font-size:11px;">{_e(region[-10:] if region else "")}</td>
      <td style="color:{state_color}; font-size:11px; font-weight:500;">{_e(state_display)}</td>
      {contact_td}
      <td class="clamp2" style="color:#4b5563; font-size:11px;" title="{_e(issue)}">{_e(issue)}{path_html}</td>
      <td class="cost cost-bad">{cost_str}</td>
      <td style="color:#4b5563; font-size:11px;">{age_str}</td>
      <td style="white-space:nowrap;">{cure_btn}{fp_form}</td>
    </tr>"""


def _render_pivots(findings):
    """Render aggregated pivot tables for findings."""
    by_type = {}
    by_region = {}
    by_category = {}
    by_age = {"<7d": 0, "7-30d": 0, "30-90d": 0, "90d+": 0, "unknown": 0}

    for f in findings:
        # By type
        rtype = f.get("resource_type", "unknown")
        by_type.setdefault(rtype, {"count": 0, "cost": 0.0})
        by_type[rtype]["count"] += 1
        by_type[rtype]["cost"] += _safe_float(f.get("estimated_monthly_cost"))

        # By region
        region = f.get("region", "unknown")
        by_region.setdefault(region, {"count": 0, "cost": 0.0})
        by_region[region]["count"] += 1
        by_region[region]["cost"] += _safe_float(f.get("estimated_monthly_cost"))

        # By category (problem type)
        cat = f.get("category", "unknown")
        by_category.setdefault(cat, {"count": 0, "cost": 0.0})
        by_category[cat]["count"] += 1
        by_category[cat]["cost"] += _safe_float(f.get("estimated_monthly_cost"))

        # By age
        age = f.get("running_duration_days")
        if age is None:
            by_age["unknown"] += 1
        elif age < 7:
            by_age["<7d"] += 1
        elif age < 30:
            by_age["7-30d"] += 1
        elif age < 90:
            by_age["30-90d"] += 1
        else:
            by_age["90d+"] += 1

    def _pivot_table(title, data, key_label="Category"):
        sorted_data = sorted(data.items(), key=lambda x: -x[1]["count"] if isinstance(x[1], dict) else -x[1])
        rows = ""
        for k, v in sorted_data:
            if isinstance(v, dict):
                rows += f'<tr><td style="color:#e2e8f0;">{_e(k)}</td><td>{v["count"]}</td><td class="cost cost-bad">${v["cost"]:.2f}/mo</td></tr>'
            else:
                rows += f'<tr><td style="color:#e2e8f0;">{_e(k)}</td><td>{v}</td><td>-</td></tr>'
        return f"""<div class="card" style="overflow:auto;">
          <div class="label">{title}</div>
          <table style="margin-top:8px; border:none; box-shadow:none; table-layout:fixed; width:100%;">
            <thead><tr><th style="width:55%;">{key_label}</th><th style="width:20%;">Count</th><th style="width:25%;">Cost</th></tr></thead>
            <tbody>{rows}</tbody>
          </table></div>"""

    age_data = {k: {"count": v, "cost": 0.0} for k, v in by_age.items() if v > 0}

    return f"""<h2>ANALYSIS</h2>
    <div class="cards" style="grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));">
      {_pivot_table("By Resource Type", by_type, "Type")}
      {_pivot_table("By Region", by_region, "Region")}
      {_pivot_table("By Problem", by_category, "Category")}
      {_pivot_table("By Age", age_data, "Age Bucket")}
    </div>"""


def _render_info_section(info_findings):
    """Build a collapsed details section for INFO (expiring-soon) findings."""
    if not info_findings:
        return ""
    info_rows = ""
    for f in info_findings:
        tags = f.get("tags", {})
        rtype = f.get("resource_type", "")
        icon = TYPE_ICONS.get(rtype, "")
        name = f.get("resource_name") or f.get("resource_arn", "").split(":")[-1][:40]
        region = f.get("region", "")
        issue = f.get("issue", "")[:80]
        repo_url = tags.get("Repository", "")
        repo_link = ""
        if repo_url and repo_url.startswith("https://"):
            repo_name = repo_url.split("/")[-1]
            repo_link = f'<a href="{_e(repo_url)}" style="color:#3b82f6; font-size:11px;">{_e(repo_name)}</a>'
        info_rows += f"""<tr>
          <td style="color:#6b7280;">{icon} {_e(rtype)}</td>
          <td>{_e(name)}</td>
          <td style="color:#9ca3af;">{_e(region)}</td>
          <td style="color:#f59e0b; font-size:12px;">{_e(issue)}</td>
          <td>{repo_link or "-"}</td>
        </tr>"""
    return f"""
    <details style="margin-top:24px;">
      <summary style="cursor:pointer; padding:10px 14px; background:#111118; border:1px solid rgba(245,158,11,0.2);
                       border-radius:6px; color:#f59e0b; font-weight:600; font-size:13px;
                       list-style:none; display:flex; align-items:center; gap:8px;">
        <span style="font-size:10px; transition:transform 0.2s;">&#x25B6;</span>
        EXPIRING SOON &mdash; {len(info_findings)} resources approaching expiration (informational only)
      </summary>
      <div style="margin-top:8px; padding:8px 12px; background:rgba(245,158,11,0.03);
                  border:1px solid rgba(245,158,11,0.1); border-radius:4px;">
        <p style="color:#6b7280; font-size:11px; margin:0 0 8px;">
          These resources are compliant but have an ExpiresAt date within 14 days. Extend or plan for teardown.
          <a href="/compliant" style="color:#22c55e;">View compliant resources &rarr;</a>
        </p>
        <table>
          <thead><tr><th>Type</th><th>Resource</th><th>Region</th><th>Status</th><th>Repo</th></tr></thead>
          <tbody>{info_rows}</tbody>
        </table>
      </div>
    </details>
    <style>details[open] > summary span:first-child {{ transform: rotate(90deg); }}</style>"""


def render_findings(
    findings, user_email="", severity_filter=None, group_by="", contact_filter="", page=1, info_findings=None
):
    """Render the findings list page, optionally grouped by contact."""
    # Filter by contact if specified
    if contact_filter:
        findings = [
            f
            for f in findings
            if _get_contact(f) == contact_filter or (contact_filter == "untagged" and not _get_contact(f))
        ]

    # Severity filter tabs — preserve grouping/contact in links
    active_all = "active" if not severity_filter else ""
    active_h = "active" if severity_filter == "HIGH" else ""
    active_m = "active" if severity_filter == "MEDIUM" else ""
    active_l = "active" if severity_filter == "LOW" else ""
    is_grouped = group_by == "contact"
    is_pivots = group_by == "pivot"
    active_flat = "active" if not is_grouped and not is_pivots else ""
    active_grp = "active" if is_grouped else ""
    active_piv = "active" if is_pivots else ""

    # Build filter query suffixes
    sev_suffix = f"&severity={_e(urllib.parse.quote(str(severity_filter), safe=''))}" if severity_filter else ""
    grp_suffix = f"&group={_e(urllib.parse.quote(str(group_by), safe=''))}" if group_by else ""

    contact_label = f" {_badge(_e(contact_filter), 'blue')}" if contact_filter else ""

    header = f"""
    <h1>FINDINGS // {len(findings)} NON-COMPLIANT{contact_label}</h1>
    <div style="margin-bottom:12px; display:flex; gap:8px; flex-wrap:wrap;">
      <a href="/compliant" class="btn btn-secondary" style="color:#22c55e; border-color:rgba(34,197,94,0.3);">COMPLIANT</a>
      <a href="/findings" class="btn {"btn-primary" if active_all and not contact_filter else "btn-secondary"}" style="color:#ef4444;">NON-COMPLIANT</a>
      <a href="/all" class="btn btn-secondary">BOTH</a>
      <span style="border-left:1px solid #2a2a3a; margin:0 4px;"></span>
      <a href="/findings?severity=HIGH{grp_suffix}" class="btn {"btn-danger" if active_h else "btn-secondary"}">HIGH</a>
      <a href="/findings?severity=MEDIUM{grp_suffix}" class="btn {"btn-warn" if active_m else "btn-secondary"}">MEDIUM</a>
      <a href="/findings?severity=LOW{grp_suffix}" class="btn btn-secondary" style="{"color:#22c55e; border-color:rgba(34,197,94,0.3);" if active_l else ""}">LOW</a>
      <span style="border-left:1px solid #2a2a3a; margin:0 4px;"></span>
      <a href="/findings{sev_suffix}" class="btn {"btn-primary" if active_flat else "btn-secondary"}">TABLE</a>
      <a href="/findings?group=contact{sev_suffix}" class="btn {"btn-primary" if active_grp else "btn-secondary"}">BY CONTACT</a>
      <a href="/findings?group=pivot{sev_suffix}" class="btn {"btn-primary" if active_piv else "btn-secondary"}">ANALYSIS</a>
    </div>"""

    if contact_filter:
        clear_link = f"/findings{sev_suffix}"
        header += f'<div style="margin-bottom:12px;"><a href="{clear_link}" class="btn btn-secondary" style="font-size:11px;">CLEAR CONTACT FILTER &#x2715;</a></div>'

    # Build info section (expiring-soon) — needed before early returns
    info_section = _render_info_section(info_findings)

    if not findings:
        return _layout(
            "Findings", header + '<div class="empty">No findings match your filter.</div>' + info_section, user_email
        )

    # Pivot / analysis view
    if is_pivots:
        return _layout("Analysis", header + _render_pivots(findings), user_email)

    # Grouped by contact view — shows summary cards AND itemized table per group
    if is_grouped and not contact_filter:
        groups = {}
        for f in findings:
            contact = _get_contact(f) or "untagged"
            groups.setdefault(contact, []).append(f)

        def group_sort_key(item):
            high_count = sum(1 for i in item[1] if i.get("severity") == "HIGH")
            total_cost = sum(_safe_float(i.get("estimated_monthly_cost")) for i in item[1])
            return (-high_count, -total_cost)

        content = header
        content += (
            f'<div style="margin-bottom:16px; color:#6b7280; font-size:12px;">{len(groups)} contacts identified</div>'
        )

        for contact, items in sorted(groups.items(), key=group_sort_key):
            high_count = sum(1 for i in items if i.get("severity") == "HIGH")
            med_count = sum(1 for i in items if i.get("severity") == "MEDIUM")
            total_cost = sum(_safe_float(i.get("estimated_monthly_cost")) for i in items)
            contact_encoded = html.escape(contact, quote=True)
            contact_color = "#ef4444" if high_count > 10 else ("#f59e0b" if high_count > 0 else "#22c55e")

            # Summary card with expand link
            content += f"""
            <div style="background:#111118; border:1px solid #1e1e2e; border-left:3px solid {contact_color};
                        border-radius:4px; padding:12px 16px; margin-bottom:2px;
                        display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;">
              <div>
                <span style="color:#e2e8f0; font-weight:600;">{_e(contact)}</span>
                <span style="color:#4b5563; margin-left:12px; font-size:12px;">{len(items)} findings</span>
              </div>
              <div style="display:flex; gap:12px; align-items:center;">
                {_badge(f"{high_count} HIGH", "red")}
                {_badge(f"{med_count} MED", "amber")}
                <span class="cost cost-bad">${total_cost:.2f}/mo</span>
                <a href="/findings?contact={contact_encoded}{sev_suffix}"
                   class="btn btn-secondary" style="font-size:10px;">VIEW ALL</a>
              </div>
            </div>"""

        return _layout("Findings", content, user_email)

    # Flat table view (default) — also used for contact drill-down
    # Split findings into actionable vs protected
    actionable = [f for f in findings if not f.get("protected", False)]
    protected = [f for f in findings if f.get("protected", False)]

    page_size = 100
    total = len(actionable)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_findings = actionable[start:end]

    rows = [_finding_row(f, show_contact=True) for f in page_findings]
    contact_th = "<th style='width:7%;'>Contact</th>"

    # Pagination controls
    pagination = ""
    if total_pages > 1:
        # Build base URL preserving current filters
        base_params = []
        if severity_filter:
            base_params.append(f"severity={_e(urllib.parse.quote(str(severity_filter), safe=''))}")
        if group_by:
            base_params.append(f"group={_e(urllib.parse.quote(str(group_by), safe=''))}")
        if contact_filter:
            base_params.append(f"contact={html.escape(contact_filter, quote=True)}")

        def _page_url(p):
            params = [*base_params, f"page={p}"]
            return f"/findings?{'&'.join(params)}"

        nav_items = []
        if page > 1:
            nav_items.append(
                f'<a href="{_page_url(page - 1)}" class="btn btn-secondary" style="font-size:11px;">&larr; Prev</a>'
            )
        else:
            nav_items.append('<span class="btn btn-secondary" style="font-size:11px; opacity:0.3;">&larr; Prev</span>')

        # Page number buttons (show nearby pages)
        for p in range(1, total_pages + 1):
            if p == 1 or p == total_pages or abs(p - page) <= 2:
                if p == page:
                    nav_items.append(f'<span class="btn btn-primary" style="font-size:11px;">{p}</span>')
                else:
                    nav_items.append(
                        f'<a href="{_page_url(p)}" class="btn btn-secondary" style="font-size:11px;">{p}</a>'
                    )
            elif (p == 2 and page > 4) or (p == total_pages - 1 and page < total_pages - 3):
                nav_items.append('<span style="color:#4b5563; font-size:11px; padding:0 4px;">&hellip;</span>')

        if page < total_pages:
            nav_items.append(
                f'<a href="{_page_url(page + 1)}" class="btn btn-secondary" style="font-size:11px;">Next &rarr;</a>'
            )
        else:
            nav_items.append('<span class="btn btn-secondary" style="font-size:11px; opacity:0.3;">Next &rarr;</span>')

        pagination = f"""
        <div style="display:flex; gap:4px; align-items:center; margin-top:12px; flex-wrap:wrap;">
          {"".join(nav_items)}
          <span style="color:#4b5563; font-size:11px; margin-left:8px;">
            {start + 1}&ndash;{end} of {total}
          </span>
        </div>"""

    legend = f"""<div style="margin-bottom:8px; display:flex; gap:16px; font-size:11px; color:#6b7280; flex-wrap:wrap;">
      <span>{_badge("TF-MANAGED", "blue")} Tracked in IaC (TF/CFN/Pulumi/CDK)</span>
      <span>{_badge("CLICK-OPS", "red")} Suspected manual — no IaC tool tag</span>
      <span>{_badge("PENDING DESTROY", "amber")} Hunterkiller warned — awaiting approval</span>
      <span>{_badge("NOT TARGETED", "gray")} Not flagged for enforcement (hover for reason)</span>
      <span><span class="btn btn-primary" style="font-size:10px; padding:2px 6px; cursor:default;">CURE</span> IaC remediation plan</span>
      <span><span class="btn btn-secondary" style="font-size:10px; padding:2px 6px; cursor:default;">DETAIL</span> Inspect (click-ops)</span>
      <span><span class="btn btn-secondary" style="font-size:10px; padding:2px 6px; cursor:default;">Exempt</span> Mark as false positive</span>
    </div>"""

    table = f"""
    {legend}
    <table>
      <thead>
        <tr><th style="width:4%;">Sev</th><th style="width:8%;">Type</th><th style="width:18%;">Name</th><th style="width:10%;">Enforcement</th><th style="width:6%;">Region</th><th style="width:5%;">State</th>{contact_th}<th style="width:22%;">Issue</th><th style="width:5%;">Cost</th><th style="width:3%;">Age</th><th style="width:10%;"></th></tr>
      </thead>
      <tbody>{"".join(rows)}</tbody>
    </table>{pagination}"""

    # Protected findings — collapsible section, starts collapsed
    protected_section = ""
    if protected:
        protected_cost = sum(_safe_float(f.get("estimated_monthly_cost")) for f in protected)
        protected_rows = [_finding_row(f, show_contact=True) for f in protected]
        protected_section = f"""
    <details style="margin-top:24px;">
      <summary style="cursor:pointer; padding:10px 14px; background:#111118; border:1px solid rgba(59,130,246,0.2);
                       border-radius:6px; color:#3b82f6; font-weight:600; font-size:13px;
                       list-style:none; display:flex; align-items:center; gap:8px;">
        <span style="font-size:10px; transition:transform 0.2s;">&#x25B6;</span>
        &#x1F6E1; PROTECTED RESOURCES &mdash; {len(protected)} findings (exempt from teardown, ${protected_cost:.2f}/mo)
      </summary>
      <div style="margin-top:8px; padding:8px 12px; background:rgba(59,130,246,0.03);
                  border:1px solid rgba(59,130,246,0.1); border-radius:4px;">
        <p style="color:#6b7280; font-size:11px; margin:0 0 8px;">
          These resources have compliance findings but are protected from hunterkiller teardown.
          Findings are shown for visibility — fix tags where possible, but these resources will never be destroyed automatically.
          <a href="/protected" style="color:#3b82f6;">View protected resources &rarr;</a>
        </p>
        <table>
          <thead>
            <tr><th style="width:4%;">Sev</th><th style="width:8%;">Type</th><th style="width:18%;">Name</th><th style="width:10%;">Enforcement</th><th style="width:6%;">Region</th><th style="width:5%;">State</th>{contact_th}<th style="width:22%;">Issue</th><th style="width:5%;">Cost</th><th style="width:3%;">Age</th><th style="width:10%;"></th></tr>
          </thead>
          <tbody>{"".join(protected_rows)}</tbody>
        </table>
      </div>
    </details>
    <style>details[open] > summary span:first-child {{ transform: rotate(90deg); }}</style>"""

    return _layout("Findings", header + table + protected_section + info_section, user_email)


def render_pending(pending_items, user_email="", is_admin=False, executor_armed=False, page=1):
    """Render the pending destroys page with escape hatch controls and approval gate."""
    content = f"<h1>PENDING DESTROYS // {len(pending_items)} TARGETS</h1>"

    if not executor_armed:
        content += '<div class="card" style="border-left:4px solid #6b7280; margin-bottom:16px; padding:10px 14px;">'
        content += "<strong>Executor not armed</strong> — Teardown approvals and destruction are disabled. "
        content += "Set <code>executor_armed=true</code> in Terraform to enable."
        content += "</div>"

    if not pending_items:
        content += '<div class="empty">No resources are pending destruction. All clear.</div>'
        return _layout("Pending", content, user_email)

    # Bulk action toolbar (admin only)
    if is_admin and pending_items:
        warned_count = len(pending_items)
        pending_approval = sum(1 for i in pending_items if i.get("approval_status") != "approved")

        approve_disabled = "" if executor_armed else " disabled"
        approve_title = "" if executor_armed else ' title="Executor not armed"'
        approve_d_style = "" if executor_armed else " opacity:0.5; cursor:not-allowed;"

        destroy_can = executor_armed
        destroy_disabled = "" if destroy_can else " disabled"
        destroy_title = "" if executor_armed else ' title="Executor not armed"'
        destroy_d_style = "" if destroy_can else " opacity:0.5; cursor:not-allowed;"

        content += f"""
        <div class="card" style="border-left:4px solid #6b7280; margin-bottom:16px; padding:10px 14px; display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
          <span style="font-weight:700; margin-right:8px;">Bulk Actions</span>
          <form method="post" action="/abort-all" style="display:inline;">
            <button type="submit" class="btn btn-primary" style="font-size:11px;"
              onclick="return confirm('ABORT ALL {warned_count} pending destroys?')">Abort All ({warned_count})</button>
          </form>
          <form method="post" action="/extend-all" style="display:inline;">
            <input type="hidden" name="hours" value="24">
            <button type="submit" class="btn btn-warn" style="font-size:11px;"
              onclick="return confirm('Extend grace period by 24h for all {warned_count} warned resources?')">+24h All ({warned_count})</button>
          </form>
          <form method="post" action="/approve-all" style="display:inline;">
            <button type="submit" class="btn btn-warn" style="font-size:11px;{approve_d_style}"{approve_disabled}{approve_title}
              onclick="return confirm('APPROVE ALL {pending_approval} pending resources for teardown?')">Approve All ({pending_approval})</button>
          </form>
          <form method="post" action="/destroy-all" style="display:inline;">
            <button type="submit" class="btn btn-danger" style="font-size:11px;{destroy_d_style}"{destroy_disabled}{destroy_title}
              onclick="return confirm('DESTROY ALL {warned_count} resources NOW? This will auto-approve and destroy. Cannot be undone!')">Destroy All ({warned_count})</button>
          </form>
        </div>"""

    page_items, pagination_html, _total = paginate(pending_items, page, page_size=50)

    now = datetime.now(UTC)
    rows = []
    for item in page_items:
        arn = item.get("resource_arn", "")
        name = item.get("resource_name", "") or arn.split(":")[-1][:40]
        rtype = item.get("resource_type", "")
        icon = TYPE_ICONS.get(rtype, "")
        cost = item.get("estimated_monthly_cost")
        try:
            cost = float(cost) if cost is not None else None
        except (ValueError, TypeError):
            cost = None
        if cost is not None and cost > 0:
            cost_str = f"${cost:.2f}/mo"
        elif cost is None:
            cost_str = '<span class="hint" style="color:#4b5563;font-size:10px;" data-tip="No pricing model">-</span>'
        else:
            cost_str = "-"
        contact_email = item.get("contact_email", "")
        contact_source = item.get("contact_source", "")
        deadline = item.get("grace_deadline", "")
        warned_at = item.get("warned_at", "")
        approval_status = item.get("approval_status", "pending")
        approved_by = item.get("approved_by", "")

        # Time remaining
        try:
            deadline_dt = datetime.fromisoformat(deadline)
            remaining = deadline_dt - now
            if remaining.total_seconds() > 0:
                hours = int(remaining.total_seconds() // 3600)
                mins = int((remaining.total_seconds() % 3600) // 60)
                time_str = f"{hours}h {mins}m remaining"
                time_color = "#f59e0b" if hours < 6 else "#22c55e"
            else:
                time_str = "OVERDUE"
                time_color = "#ef4444"
        except (ValueError, TypeError):
            time_str = "Unknown"
            time_color = "#6b7280"

        arn_encoded = html.escape(arn, quote=True)

        disabled = "" if is_admin else " disabled"
        disabled_title = "" if is_admin else ' title="Admin access required"'
        disabled_style = "" if is_admin else " opacity:0.5; cursor:not-allowed;"

        # Approval status display
        if approval_status == "approved":
            approval_badge = '<span style="color:#22c55e; font-weight:700;">APPROVED</span>'
            if approved_by:
                approval_badge += (
                    f'<br><span style="font-size:10px; color:#4b5563;">{_e(approved_by.split("@")[0])}</span>'
                )
        else:
            approval_badge = '<span style="color:#f59e0b;">Pending</span>'

        # Approve / Revoke button
        if approval_status == "approved":
            approve_btn = f"""
                <form method="post" action="/revoke" style="display:inline; margin-right:4px;">
                  <input type="hidden" name="resource_arn" value="{arn_encoded}">
                  <button type="submit" class="btn btn-secondary" style="font-size:11px;{disabled_style}"{disabled}{disabled_title}
                    onclick="return confirm({_e(_js_json(f"Revoke teardown approval for {name}?"))})">Revoke</button>
                </form>"""
        else:
            # Approve is disabled when executor not armed
            approve_disabled = disabled if executor_armed else " disabled"
            approve_title = disabled_title if executor_armed else ' title="Executor not armed"'
            approve_d_style = disabled_style if executor_armed else " opacity:0.5; cursor:not-allowed;"
            approve_btn = f"""
                <form method="post" action="/approve" style="display:inline; margin-right:4px;">
                  <input type="hidden" name="resource_arn" value="{arn_encoded}">
                  <button type="submit" class="btn btn-warn" style="font-size:11px;{approve_d_style}"{approve_disabled}{approve_title}
                    onclick="return confirm({_e(_js_json(f"Approve teardown of {name}? The executor will proceed when armed."))})">Approve</button>
                </form>"""

        # Destroy Now: requires both approval AND executor armed
        destroy_can = is_admin and executor_armed and approval_status == "approved"
        destroy_disabled = "" if destroy_can else " disabled"
        destroy_title = ""
        if not is_admin:
            destroy_title = ' title="Admin access required"'
        elif not executor_armed:
            destroy_title = ' title="Executor not armed"'
        elif approval_status != "approved":
            destroy_title = ' title="Approval required before destruction"'
        destroy_d_style = "" if destroy_can else " opacity:0.5; cursor:not-allowed;"

        actions = f"""
            <form method="post" action="/abort" style="display:inline; margin-right:4px;">
              <input type="hidden" name="resource_arn" value="{arn_encoded}">
              <button type="submit" class="btn btn-primary" style="font-size:11px;{disabled_style}"{disabled}{disabled_title}
                onclick="return confirm({_e(_js_json(f"ABORT destruction of {name}?"))})">Abort</button>
            </form>
            <form method="post" action="/extend" style="display:inline; margin-right:4px;">
              <input type="hidden" name="resource_arn" value="{arn_encoded}">
              <input type="hidden" name="hours" value="24">
              <button type="submit" class="btn btn-warn" style="font-size:11px;{disabled_style}"{disabled}{disabled_title}
                onclick="return confirm('Extend grace period by 24 hours?')">+24h</button>
            </form>
            {approve_btn}
            <form method="post" action="/destroy" style="display:inline;">
              <input type="hidden" name="resource_arn" value="{arn_encoded}">
              <button type="submit" class="btn btn-danger" style="font-size:11px;{destroy_d_style}"{destroy_disabled}{destroy_title}
                onclick="return confirm({_e(_js_json(f"DESTROY {name} NOW? This cannot be undone!"))})">Destroy Now</button>
            </form>"""

        rows.append(f"""<tr>
          <td>{icon} {_e(rtype)}</td>
          <td class="truncate" title="{arn_encoded}">{_e(name)}</td>
          <td class="cost cost-bad">{cost_str}</td>
          <td style="font-size:12px;">{_notified_display(contact_email, contact_source)}</td>
          <td>{approval_badge}</td>
          <td style="color:{time_color}; font-weight:700;">{time_str}</td>
          <td style="font-size:11px; color:#4b5563;">{_e(warned_at[:16] if warned_at else "")}</td>
          <td>{actions}</td>
        </tr>""")

    table = f"""
    <table>
      <thead>
        <tr><th>Type</th><th>Name</th><th>Cost</th><th>Notified</th><th>Approval</th><th>Deadline</th><th>Warned</th><th>Actions{_admin_only("Destroy actions restricted to admins") if is_admin else ""}</th></tr>
      </thead>
      <tbody>{"".join(rows)}</tbody>
    </table>{pagination_html}"""

    content += table
    return _layout("Pending", content, user_email)


def _notified_display(contact_email, contact_source):
    """Render a human-readable notification status for the pending view."""
    if not contact_source:
        return '<span style="color:#6b7280;">Unknown</span>'

    if contact_source == "admin_fallback":
        return (
            '<span style="color:#f59e0b;">Admins only</span>'
            '<br><span style="font-size:10px;color:#4b5563;">No resource contact found</span>'
        )

    if contact_source == "no_contact":
        return (
            '<span style="color:#ef4444;">No one</span>'
            '<br><span style="font-size:10px;color:#4b5563;">No contact or admins configured</span>'
        )

    # Tagged contact (tag:Contact, tag:OwnerEmail, tag:Owner) or IAM lookup (iam:username)
    email_display = _e(contact_email.split(",")[0]) if contact_email else "?"
    if contact_source.startswith("tag:"):
        tag_name = contact_source.split(":", 1)[1]
        return (
            f'<span style="color:#3b82f6;">{email_display}</span>'
            f'<br><span style="font-size:10px;color:#4b5563;">via {_e(tag_name)} tag + admins</span>'
        )

    if contact_source.startswith("iam:"):
        iam_user = contact_source.split(":", 1)[1]
        return (
            f'<span style="color:#3b82f6;">{email_display}</span>'
            f'<br><span style="font-size:10px;color:#4b5563;">via IAM user {_e(iam_user)} + admins</span>'
        )

    return f'<span style="color:#94a3b8;">{_e(contact_email or "Unknown")}</span>'


def render_overrides(overrides, user_email="", is_admin=False, page=1):
    """Render the overrides management page."""
    content = f"<h1>OVERRIDES // {len(overrides)} ACTIVE</h1>"

    if is_admin:
        content += f"""
        <h2>Add Override{_admin_only("Override management restricted to admins")}</h2>
        <div class="card" style="margin-bottom:24px;">
          <form method="post" action="/override">
            <div class="form-group">
              <label>Resource ARN</label>
              <input type="text" name="resource_arn" required placeholder="arn:aws:...">
            </div>
            <div class="form-group">
              <label>Type</label>
              <select name="override_type">
                <option value="false_positive">False Positive</option>
                <option value="exempt">Exempt</option>
              </select>
            </div>
            <div class="form-group">
              <label>Reason</label>
              <textarea name="reason" required placeholder="Explain why this is a false positive or exempt..."></textarea>
            </div>
            <div class="form-group">
              <label>Resource Type (optional)</label>
              <input type="text" name="resource_type" placeholder="e.g., secretsmanager:secret">
            </div>
            <div class="form-group">
              <label>Resource Name (optional)</label>
              <input type="text" name="resource_name" placeholder="Human-readable name">
            </div>
            <button type="submit" class="btn btn-primary">Add Override</button>
          </form>
        </div>"""

    if not overrides:
        content += '<div class="empty">No overrides configured.</div>'
        return _layout("Overrides", content, user_email)

    page_items, pagination_html, _total = paginate(overrides, page, page_size=50)

    rows = []
    for o in page_items:
        arn = o.get("resource_arn", "")
        otype = o.get("override_type", "")
        reason = o.get("reason", "")
        created_by = o.get("created_by", "")
        created_at = o.get("created_at", "")[:16]
        expires = o.get("expires_at", "")[:16] if o.get("expires_at") else "Never"
        rtype = o.get("resource_type", "")

        arn_encoded = html.escape(arn, quote=True)
        override_badge_color = "blue" if otype == "false_positive" else "purple"

        delete_btn = ""
        if is_admin:
            delete_btn = f"""
            <form method="post" action="/override/delete" style="display:inline;">
              <input type="hidden" name="resource_arn" value="{arn_encoded}">
              <button type="submit" class="btn btn-danger" style="font-size:11px;"
                onclick="return confirm('Remove this override?')">Remove</button>
            </form>"""

        rows.append(f"""<tr>
          <td>{_badge(_e(otype), override_badge_color)}</td>
          <td>{_e(rtype)}</td>
          <td class="truncate mono" title="{arn_encoded}">{_e(arn[-60:])}</td>
          <td>{_e(reason[:80])}</td>
          <td>{_e(created_by)}</td>
          <td>{_e(created_at)}</td>
          <td>{_e(expires)}</td>
          <td>{delete_btn}</td>
        </tr>""")

    table = f"""
    <table>
      <thead>
        <tr><th>Type</th><th>Resource</th><th>ARN</th><th>Reason</th><th>Created By</th><th>Created</th><th>Expires</th><th>Actions{_admin_only("Override removal restricted to admins") if is_admin else ""}</th></tr>
      </thead>
      <tbody>{"".join(rows)}</tbody>
    </table>{pagination_html}"""

    content += table
    return _layout("Overrides", content, user_email)

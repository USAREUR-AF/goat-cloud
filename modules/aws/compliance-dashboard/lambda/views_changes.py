"""
Compliance delta view — shows what changed since a prior scan (>= 24h ago).
"""

import html as html_mod

from views_base import (
    SEV_COLORS,
    TYPE_ICONS,
    _e,
    _layout,
)


def render_changes(delta, today_date, yesterday_date, user_email=""):
    """Render the changes page comparing current report vs a prior report."""
    if delta is None:
        content = "<h1>COMPLIANCE CHANGES</h1>"
        content += '<div class="empty">No prior report available for comparison. '
        content += "Changes will appear after two consecutive nightly scans.</div>"
        return _layout("Changes", content, user_email, active_page="changes")

    s = delta["summary"]
    span_label = _time_span_label(today_date, yesterday_date)

    # Header
    header = f"""
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
      <div>
        <h1 style="margin-bottom:4px;">{_e(span_label)} CHANGES</h1>
        <span style="color:#4b5563; font-size:12px;">
          Comparing {_e(today_date)} vs {_e(yesterday_date)}
        </span>
      </div>
      <div style="text-align:right;">
        <div style="font-size:10px; color:#6b7280; text-transform:uppercase; letter-spacing:0.1em;">Burn Delta</div>
        <div style="font-size:18px; font-weight:700; color:{_delta_color(s["burn_delta"])};">
          {_format_delta(s["burn_delta"])}/mo
        </div>
      </div>
    </div>"""

    # Summary cards
    cards = f"""
    <div class="cards">
      <div class="card" style="border-color:rgba(239,68,68,0.3);">
        <div class="label">New Findings</div>
        <div class="value" style="color:#ef4444;">+{s["new_count"]}</div>
        <div style="font-size:11px; color:#6b7280;">+${s["new_burn"]:.0f}/mo</div>
      </div>
      <div class="card" style="border-color:rgba(34,197,94,0.3);">
        <div class="label">Resolved</div>
        <div class="value" style="color:#22c55e;">{s["resolved_count"]}</div>
        <div style="font-size:11px; color:#6b7280;">${s["resolved_burn"]:.0f}/mo saved</div>
      </div>
      <div class="card" style="border-color:rgba(245,158,11,0.3);">
        <div class="label">Persistent</div>
        <div class="value" style="color:#f59e0b;">{s["persistent_count"]}</div>
        <div style="font-size:11px; color:#6b7280;">${s["today_burn"]:.0f}/mo total</div>
      </div>
      <div class="card">
        <div class="label">Total Today</div>
        <div class="value">{s["today_total"]}</div>
        <div style="font-size:11px; color:#6b7280;">was {s["yesterday_total"]}</div>
      </div>
      <div class="card">
        <div class="label">Resources Discovered</div>
        <div class="value">{s["today_discovered"]}</div>
        <div style="font-size:11px; color:#6b7280;">was {s["yesterday_discovered"]}</div>
      </div>
    </div>"""

    # New findings table
    new_section = _render_findings_table(
        "NEW NON-COMPLIANT RESOURCES",
        delta["new_findings"],
        badge_text="+NEW",
        badge_color="#ef4444",
        empty_msg="No new non-compliant resources. All clear.",
    )

    # Resolved findings table
    resolved_section = _render_findings_table(
        "RESOLVED",
        delta["resolved_findings"],
        badge_text="RESOLVED",
        badge_color="#22c55e",
        empty_msg=f"No findings resolved in the last {span_label.lower().replace('-', ' ')}.",
        resolved=True,
    )

    # Severity changes
    sev_section = ""
    if delta["severity_changes"]:
        sev_section = _render_severity_changes(delta["severity_changes"])

    # Compliant changes summary
    compliant_section = ""
    if s["new_compliant_count"] or s["lost_compliant_count"]:
        compliant_section = f"""
        <h2 style="margin-top:24px;">COMPLIANT RESOURCE CHANGES</h2>
        <div class="cards">
          <div class="card" style="border-color:rgba(34,197,94,0.3);">
            <div class="label">Newly Compliant</div>
            <div class="value" style="color:#22c55e;">+{s["new_compliant_count"]}</div>
          </div>
          <div class="card" style="border-color:rgba(239,68,68,0.3);">
            <div class="label">Lost Compliance</div>
            <div class="value" style="color:#ef4444;">-{s["lost_compliant_count"]}</div>
          </div>
        </div>"""

    content = header + cards + new_section + resolved_section + sev_section + compliant_section
    return _layout("Changes", content, user_email, active_page="changes")


def _render_findings_table(
    title, findings, badge_text="", badge_color="#6b7280", empty_msg="No items.", resolved=False
):
    """Render a findings table section.

    When resolved=True, rows are muted (greyed-out severity, no cost alarm
    colouring, "Details" instead of "CURE") to visually distinguish resolved
    items from active non-compliant findings.
    """
    section = f'<h2 style="margin-top:24px;">{title} ({len(findings)})</h2>'

    if not findings:
        section += f'<div class="empty">{empty_msg}</div>'
        return section

    rows = []
    for f in findings:
        arn = f.get("resource_arn", "")
        name = f.get("resource_name", "") or arn.split(":")[-1][:40]
        rtype = f.get("resource_type", "")
        icon = TYPE_ICONS.get(rtype, "")
        severity = f.get("severity", "LOW")
        cost = f.get("estimated_monthly_cost")
        if cost is None:
            cost_str = '<span style="color:#4b5563;font-size:10px;">-</span>'
        elif cost > 0:
            cost_str = f"${float(cost):.2f}/mo"
        else:
            cost_str = "-"
        issue = f.get("issue", "")
        region = f.get("region", "")
        arn_encoded = html_mod.escape(arn, quote=True)

        if resolved:
            sev_color = "#4b5563"
            cost_style = "color:#4b5563;"
            row_style = "opacity:0.7;"
            resolution = f.get("resolution", "compliant")
            if resolution == "deleted":
                status_col = '<span style="color:#6b7280; font-size:10px; font-weight:600;">DELETED</span>'
            else:
                status_col = '<span style="color:#22c55e; font-size:10px; font-weight:600;">COMPLIANT</span>'
            detail_btn = ""
            if arn:
                detail_btn = (
                    f'<a href="/cure?arn={arn_encoded}" class="btn btn-secondary" '
                    f'style="font-size:10px; padding:2px 6px; text-decoration:none;">Details</a>'
                )
        else:
            sev_color = SEV_COLORS.get(severity, "#6b7280")
            cost_style = ""
            row_style = ""
            status_col = ""
            detail_btn = ""
            if arn:
                detail_btn = (
                    f'<a href="/cure?arn={arn_encoded}" class="btn btn-primary" '
                    f'style="font-size:10px; padding:2px 6px; text-decoration:none;">CURE</a>'
                )

        rows.append(f"""<tr style="{row_style}">
          <td><span style="color:{sev_color}; font-weight:700;">{_e(severity)}</span></td>
          <td>{icon} {_e(rtype)}</td>
          <td class="truncate" title="{arn_encoded}">{_e(name)}</td>
          <td style="font-size:11px; color:#6b7280;">{_e(region)}</td>
          <td class="cost" style="{cost_style}">{cost_str}</td>
          <td style="font-size:11px; max-width:200px; overflow:hidden; text-overflow:ellipsis;"
              title="{html_mod.escape(issue, quote=True)}">{_e(issue[:60])}</td>
          {f"<td>{status_col}</td>" if resolved else ""}
          <td>{detail_btn}</td>
        </tr>""")

    status_header = "<th>Status</th>" if resolved else ""
    section += f"""
    <table>
      <thead>
        <tr><th>Sev</th><th>Type</th><th>Name</th><th>Region</th><th>Cost</th><th>Issue</th>{status_header}<th></th></tr>
      </thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""
    return section


def _render_severity_changes(changes):
    """Render severity change table."""
    section = f'<h2 style="margin-top:24px;">SEVERITY CHANGES ({len(changes)})</h2>'
    rows = []
    for f in changes:
        arn = f.get("resource_arn", "")
        name = f.get("resource_name", "") or arn.split(":")[-1][:40]
        rtype = f.get("resource_type", "")
        old_sev = f.get("previous_severity", "?")
        new_sev = f.get("severity", "?")
        old_color = SEV_COLORS.get(old_sev, "#6b7280")
        new_color = SEV_COLORS.get(new_sev, "#6b7280")
        icon = TYPE_ICONS.get(rtype, "")

        rows.append(f"""<tr>
          <td>{icon} {_e(rtype)}</td>
          <td class="truncate">{_e(name)}</td>
          <td><span style="color:{old_color}; font-weight:700;">{_e(old_sev)}</span>
              &rarr;
              <span style="color:{new_color}; font-weight:700;">{_e(new_sev)}</span></td>
        </tr>""")

    section += f"""
    <table>
      <thead><tr><th>Type</th><th>Name</th><th>Change</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""
    return section


def _time_span_label(today_date, yesterday_date):
    """Compute a human-readable label for the comparison window."""
    from data_reports import _parse_report_date

    t = _parse_report_date(today_date)
    y = _parse_report_date(yesterday_date)
    if t and y:
        hours = (t - y).total_seconds() / 3600
        if hours < 48:
            return f"{round(hours)}-HOUR"
        days = round(hours / 24)
        return f"{days}-DAY"
    return "24-HOUR"


def _delta_color(delta):
    """Color for a burn rate delta value."""
    if delta > 0:
        return "#ef4444"
    if delta < 0:
        return "#22c55e"
    return "#6b7280"


def _format_delta(delta):
    """Format a delta value with +/- sign."""
    if delta > 0:
        return f"+${delta:.0f}"
    if delta < 0:
        return f"-${abs(delta):.0f}"
    return "$0"

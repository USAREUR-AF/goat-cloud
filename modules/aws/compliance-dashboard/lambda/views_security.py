"""
Security Posture page — single-pane view of environment security status.

Five sections:
  1. Internet Exposure Map
  2. Auth Coverage Gaps
  3. Monitoring Coverage
  4. Container Images
  5. Vulnerability Intelligence
"""

from auth import is_admin
from data import get_security_posture
from views_base import NAV_CSS, _admin_only, _badge, _e, _ecr_scan_url, render_nav

# ── Status colors ────────────────────────────────────────────────────────────

_GAP_COLORS = {
    "critical": "#ef4444",
    "warning": "#f59e0b",
    "ok": "#22c55e",
    "info": "#3b82f6",
    "identity": "#a855f7",
}

_STALENESS_LABELS = {
    "fresh": "Current",
    "aging": "Update Available",
    "stale": "Stale",
    "unknown": "Unknown",
}

_STALENESS_BADGE = {
    "fresh": "green",
    "aging": "amber",
    "stale": "red",
    "unknown": "gray",
}

_GAP_BADGE = {
    "critical": "red",
    "warning": "amber",
    "ok": "green",
    "info": "blue",
    "identity": "purple",
}


def _check(ok):
    """Green check, red X, or gray N/A for None."""
    if ok is None:
        return '<span style="color:#4b5563; font-size:11px;">N/A</span>'
    if ok:
        return '<span style="color:#22c55e; font-weight:700;">&#10003;</span>'
    return '<span style="color:#ef4444; font-weight:700;">&#10007;</span>'


def _sec_badge(text, color_name):
    """Security badge — bordered variant of the shared _badge."""
    return _badge(_e(text), color_name, border=True)


# ── Section renderers ────────────────────────────────────────────────────────


def _render_summary_cards(posture):
    """Top-level summary cards."""
    exposure = posture["exposure_map"]
    auth_gaps = posture["auth_gaps"]
    monitoring = posture["monitoring"]
    ecr = posture["ecr_findings"]

    exposed = len(exposure)
    unauthed = sum(1 for e in exposure if not e["has_auth"])
    gap_count = sum(1 for g in auth_gaps if g["gap_level"] in ("critical", "warning"))
    gap_critical = sum(1 for g in auth_gaps if g["gap_level"] == "critical")
    foundation = sum(1 for x in [monitoring["cloudtrail"], monitoring["guardduty"], monitoring["vpc_flow_logs"]] if x)
    ecr_critical = sum(f.get("critical", 0) for f in ecr.values() if isinstance(f, dict))
    stale_count = sum(1 for f in ecr.values() if isinstance(f, dict) and f.get("drift", {}).get("staleness") == "stale")

    unauthed_color = "#ef4444" if unauthed > 0 else "#22c55e"
    gap_color = "#ef4444" if gap_count > 0 else "#22c55e"
    foundation_color = "#22c55e" if foundation == 3 else ("#f59e0b" if foundation >= 2 else "#ef4444")
    ecr_color = "#ef4444" if ecr_critical > 0 else "#22c55e"
    stale_color = "#ef4444" if stale_count > 0 else "#22c55e"

    return f"""
    <div class="cards">
      <div class="card">
        <div class="label">Exposed Services</div>
        <div class="value" style="color:#e2e8f0;">{exposed}</div>
        <div style="font-size:10px; color:{unauthed_color}; margin-top:4px;">
          {unauthed} without auth</div>
      </div>
      <div class="card">
        <div class="label">Auth Gaps</div>
        <div class="value" style="color:{gap_color};">{gap_count}</div>
        <div style="font-size:10px; color:#4b5563; margin-top:4px;">
          {gap_critical} critical, {gap_count - gap_critical} open registration</div>
      </div>
      <div class="card">
        <div class="label">Foundation</div>
        <div class="value" style="color:{foundation_color};">{foundation}/3</div>
        <div style="font-size:10px; color:#4b5563; margin-top:4px;">
          CloudTrail + GuardDuty + Flow Logs</div>
      </div>
      <div class="card">
        <div class="label">ECR Critical</div>
        <div class="value" style="color:{ecr_color};">{ecr_critical}</div>
        <div style="font-size:10px; color:#4b5563; margin-top:4px;">critical findings</div>
      </div>
      <div class="card">
        <div class="label">Images Stale</div>
        <div class="value" style="color:{stale_color};">{stale_count}</div>
        <div style="font-size:10px; color:#4b5563; margin-top:4px;">deployed &gt;30d behind latest</div>
      </div>
    </div>"""


def _render_exposure_section(exposure):
    """Section 1: Internet Exposure Map."""
    if not exposure:
        return """<h2>1. Internet Exposure Map</h2>
        <div class="empty-box">No exposed services found in TF state.</div>"""

    rows = ""
    for e in exposure:
        has_auth = e["has_auth"]
        dot_color = "#22c55e" if has_auth else "#ef4444"
        dot = (
            f'<span style="display:inline-block; width:10px; height:10px; border-radius:50%; '
            f'background:{dot_color}; box-shadow:0 0 6px {dot_color};"></span>'
        )

        hostname = e["hostname"]
        service = e["service"]
        auth_layer = e["auth_layer"]
        auth_color = "#22c55e" if has_auth else "#ef4444"

        # CF proxy status
        if e["cf_proxied"] is True:
            cf_cell = _sec_badge("Proxied", "orange")
        elif e["cf_proxied"] is False:
            cf_cell = _sec_badge("DNS Only", "gray")
        else:
            cf_cell = '<span style="color:#374151;">Unknown</span>'

        waf_cell = _check(e["waf"])

        rows += (
            f"<tr>"
            f'<td style="text-align:center;">{dot}</td>'
            f'<td><a href="https://{_e(hostname)}" target="_blank" rel="noopener" '
            f'style="color:#3b82f6; text-decoration:none;">{_e(hostname)}</a></td>'
            f'<td style="color:#e2e8f0; font-weight:600;">{_e(service)}</td>'
            f'<td><span style="color:#6b7280;">{_e(e["protocol"])}</span></td>'
            f'<td><span style="color:{auth_color};">{_e(auth_layer)}</span></td>'
            f'<td style="text-align:center;">{cf_cell}</td>'
            f'<td style="text-align:center;">{waf_cell}</td>'
            f"</tr>"
        )

    return f"""<h2>1. Internet Exposure Map</h2>
    <div style="color:#4b5563; font-size:11px; margin-bottom:12px;">
      Every service reachable from the internet, how it's exposed, and what sits in front of it.
    </div>
    <table>
      <thead><tr>
        <th style="text-align:center; width:40px;"></th>
        <th>Hostname</th>
        <th>Service</th>
        <th>Protocol</th>
        <th>Auth Layer</th>
        <th style="text-align:center;">CF Proxy</th>
        <th style="text-align:center;">WAF</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <div style="font-size:10px; color:#4b5563; margin-top:8px;">
      <span style="color:#22c55e;">&#9679;</span> Authenticated &nbsp;
      <span style="color:#ef4444;">&#9679;</span> No authentication
    </div>"""


def _render_auth_section(auth_gaps):
    """Section 2: Auth Coverage Gaps."""
    if not auth_gaps:
        return """<h2>2. Auth Coverage Gaps</h2>
        <div class="empty-box">No services to analyze.</div>"""

    rows = ""
    for g in auth_gaps:
        level = g["gap_level"]
        level_color = _GAP_COLORS.get(level, "#6b7280")
        level_label = level.upper()
        status_badge = _sec_badge(level_label, _GAP_BADGE.get(level, "gray"))

        rows += (
            f"<tr>"
            f'<td style="color:#e2e8f0; font-weight:600;">{_e(g["service"])}</td>'
            f'<td><a href="https://{_e(g["hostname"])}" target="_blank" rel="noopener" '
            f'style="color:#3b82f6; text-decoration:none; font-size:12px;">'
            f"{_e(g['hostname'])}</a></td>"
            f'<td><span style="color:#a855f7;">{_e(g["identity_provider"])}</span></td>'
            f'<td><span style="color:#6b7280;">{_e(g["auth_layer"])}</span></td>'
            f'<td><span style="color:{level_color};">{_e(g["registration"])}</span></td>'
            f'<td style="text-align:center;">{status_badge}</td>'
            f"</tr>"
        )

    return f"""<h2>2. Auth Coverage Gaps</h2>
    <div style="color:#4b5563; font-size:11px; margin-bottom:12px;">
      Which services have proper authentication and which don't.
      "Warning" means authentication exists but registration is open (any SSO account can sign in).
      Purple "IDENTITY" services provide authentication to other services.
    </div>
    <table>
      <thead><tr>
        <th>Service</th>
        <th>Hostname</th>
        <th>Identity Provider</th>
        <th>Auth Layer</th>
        <th>Registration</th>
        <th style="text-align:center;">Status</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""


def _render_monitoring_section(monitoring):
    """Section 3: Monitoring Coverage."""
    # Foundation checks
    checks = [
        ("CloudTrail", monitoring["cloudtrail"], "Audit logging for all API calls"),
        ("GuardDuty", monitoring["guardduty"], "Threat detection and alerting"),
        ("VPC Flow Logs", monitoring["vpc_flow_logs"], "Network traffic logging"),
    ]
    cards = ""
    for name, enabled, desc in checks:
        color = "#22c55e" if enabled else "#ef4444"
        icon = "&#10003;" if enabled else "&#10007;"
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        border = f"border-color: rgba({r},{g},{b}, 0.3);"
        cards += (
            f'<div class="card" style="{border}">'
            f'<div style="font-size:20px; color:{color}; margin-bottom:4px;">{icon}</div>'
            f'<div class="label">{_e(name)}</div>'
            f'<div style="font-size:11px; color:#4b5563; margin-top:4px;">{_e(desc)}</div>'
            f"</div>"
        )

    # Split services: ALB-routed vs internal/managed (no ALB endpoint)
    alb_services = [s for s in monitoring["services"] if s.get("hc_reason") == "alb"]
    internal_services = [s for s in monitoring["services"] if s.get("hc_reason") != "alb"]

    alb_rows = ""
    for svc in alb_services:
        alb_rows += (
            f"<tr>"
            f'<td style="color:#e2e8f0; font-weight:600;">{_e(svc["service"])}</td>'
            f'<td><span style="color:#6b7280;">{_e(svc["type"])}</span></td>'
            f'<td style="text-align:center;">{_check(svc["has_alarm"])}</td>'
            f'<td style="text-align:center;">{_check(svc["has_health_check"])}</td>'
            f"</tr>"
        )

    internal_rows = ""
    for svc in internal_services:
        reason = svc.get("hc_reason", "")
        if reason == "managed":
            hc_note = "Managed runtime"
        elif reason == "no_alb":
            hc_note = "No ALB endpoint"
        else:
            hc_note = "N/A"
        internal_rows += (
            f"<tr>"
            f'<td style="color:#e2e8f0; font-weight:600;">{_e(svc["service"])}</td>'
            f'<td><span style="color:#6b7280;">{_e(svc["type"])}</span></td>'
            f'<td style="text-align:center;">{_check(svc["has_alarm"])}</td>'
            f'<td><span style="color:#4b5563; font-size:11px;">{hc_note}</span></td>'
            f"</tr>"
        )

    internal_html = ""
    if internal_services:
        internal_html = f"""
    <div style="font-size:12px; color:#94a3b8; font-weight:600; text-transform:uppercase;
                letter-spacing:0.08em; margin: 20px 0 8px 0;">
      Internal / Managed Services <span style="color:#4b5563; font-weight:400;">
      (no ALB health check)</span>
    </div>
    <table>
      <thead><tr>
        <th>Service</th>
        <th>Type</th>
        <th style="text-align:center;">CW Alarm</th>
        <th style="text-align:center;">Health</th>
      </tr></thead>
      <tbody>{internal_rows}</tbody>
    </table>"""

    return f"""<h2>3. Monitoring Coverage</h2>
    <div style="color:#4b5563; font-size:11px; margin-bottom:12px;">
      Foundation security services and per-service alarm coverage.
    </div>

    <div style="font-size:12px; color:#94a3b8; font-weight:600; text-transform:uppercase;
                letter-spacing:0.08em; margin-bottom:8px;">Foundation</div>
    <div class="cards" style="grid-template-columns: repeat(3, 1fr); margin-bottom:20px;">
      {cards}
    </div>

    <div style="font-size:12px; color:#94a3b8; font-weight:600; text-transform:uppercase;
                letter-spacing:0.08em; margin-bottom:8px;">
      ALB-Routed Services <span style="color:#4b5563; font-weight:400;">
      ({monitoring["alarm_count"]} total alarms)</span>
    </div>
    <table>
      <thead><tr>
        <th>Service</th>
        <th>Type</th>
        <th style="text-align:center;">CW Alarm</th>
        <th style="text-align:center;">Health Check</th>
      </tr></thead>
      <tbody>{alb_rows}</tbody>
    </table>
    {internal_html}"""


def _render_images_section(ecr_findings, container_inventory=None, admin=False):
    """Section 4: container images, deployed digest and drift."""
    inventory = container_inventory or {}
    all_names = sorted(inventory.keys())

    if not all_names:
        return """<h2>4. Container Images</h2>
        <div class="empty-box">No container inventory found.</div>"""

    rows = ""
    for name in all_names:
        inv = inventory.get(name, {})

        # Base image, from image-versions upstream
        base = inv.get("base", "")

        # Deployed digest
        digest = inv.get("ecr_digest")
        if digest:
            short = digest[:19] + "..."
            digest_cell = f'<span style="color:#4b5563; font-size:10px; font-family:monospace;">{_e(short)}</span>'
        elif inv.get("ecr_repo"):
            digest_cell = '<span style="color:#374151; font-size:10px;">Not pinned</span>'
        else:
            digest_cell = '<span style="color:#374151; font-size:10px;">N/A</span>'

        # Drift badge
        ecr = ecr_findings.get(name, {})
        drift = ecr.get("drift", {})
        if drift.get("has_update"):
            s = drift.get("staleness", "unknown")
            s_badge_color = _STALENESS_BADGE.get(s, "gray")
            s_label = _STALENESS_LABELS.get(s, s)
            age = drift.get("deployed_age_days")
            age_text = f" ({age}d)" if age is not None else ""
            drift_cell = _sec_badge(f"{s_label}{age_text}", s_badge_color)
        elif drift.get("staleness") == "fresh":
            drift_cell = _sec_badge("Current", "green")
        elif digest:
            drift_cell = '<span style="color:#4b5563; font-size:10px;">No ECR data</span>'
        else:
            drift_cell = ""

        # ECR scan summary — link to Console if admin + digest available
        ecr_repo = ecr.get("repo", "")
        image_digest = ecr.get("deployed_digest") or ecr.get("latest_digest", "")
        image_scan_url = _ecr_scan_url(ecr_repo, image_digest) if admin else ""

        if ecr.get("error"):
            scan_cell = f'<span style="color:#4b5563;">{_e(ecr["error"])}</span>'
        elif ecr.get("scan_date"):
            scan_date = ecr["scan_date"][:10] if len(ecr.get("scan_date", "")) >= 10 else ecr.get("scan_date", "")
            crit = ecr.get("critical", 0)
            high = ecr.get("high", 0)
            crit_color = "#ef4444" if crit > 0 else "#22c55e"
            high_color = "#ef4444" if high > 0 else "#f59e0b"
            scan_inner = (
                f'<span style="color:#4b5563; font-size:11px;">{_e(scan_date)}</span> '
                f'<span style="color:{crit_color}; font-weight:600;">{crit}C</span> '
                f'<span style="color:{high_color}; font-weight:600;">{high}H</span>'
            )
            if image_scan_url:
                scan_cell = (
                    f'<a href="{_e(image_scan_url)}" target="_blank" rel="noopener" '
                    f'style="text-decoration:none;" title="View scan details in ECR">'
                    f'{scan_inner} <span style="color:#4b5563; font-size:10px;">&#x2197;</span></a>'
                )
            else:
                scan_cell = scan_inner
        else:
            scan_cell = '<span style="color:#374151;">No scan</span>'

        notes = inv.get("notes", "")

        rows += (
            f"<tr>"
            f'<td style="color:#e2e8f0; font-weight:600;">{_e(name)}</td>'
            f'<td><span style="color:#6b7280; font-size:12px;">{_e(base)}</span></td>'
            f"<td>{digest_cell}</td>"
            f'<td style="text-align:center;">{drift_cell}</td>'
            f"<td>{scan_cell}</td>"
            f'<td><span style="color:#4b5563; font-size:12px;">{_e(notes)}</span></td>'
            f"</tr>"
        )

    return f"""<h2>4. Container Images</h2>
    <div style="color:#4b5563; font-size:11px; margin-bottom:12px;">
      Deployed digest from
      <span style="font-family:monospace;">image-versions.json</span>, and version drift.
      Digests are pinned at deploy time; "Update Available" means a newer image exists in ECR.
    </div>
    <table>
      <thead><tr>
        <th>Container</th>
        <th>Base Image</th>
        <th>Deployed</th>
        <th style="text-align:center;">Drift</th>
        <th>ECR Scan{_admin_only("Scan links visible to admins only") if admin else ""}</th>
        <th>Notes</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""


def _render_vuln_section(ecr_findings, admin=False):
    """Section 5: Vulnerability Intelligence with deployed-vs-latest comparison."""
    if not ecr_findings:
        return """<h2>5. Vulnerability Intelligence</h2>
    <div class="empty-box">
      ECR scan data unavailable. Findings are fetched live from ECR scan-on-push results.
      In local dev mode, this section is empty.
    </div>"""

    rows = ""
    for name, findings in sorted(ecr_findings.items()):
        if findings.get("error"):
            rows += (
                f"<tr>"
                f'<td style="color:#e2e8f0; font-weight:600;">{_e(name)}</td>'
                f'<td colspan="7" style="color:#4b5563;">{_e(findings["error"])}</td>'
                f"</tr>"
            )
            continue

        scan_date = findings.get("scan_date", "")[:10] if findings.get("scan_date") else "-"
        tags = ", ".join(findings.get("tags", [])[:3]) or "-"
        crit = findings.get("critical", 0)
        high = findings.get("high", 0)
        medium = findings.get("medium", 0)
        low = findings.get("low", 0)
        total = findings.get("total", 0)

        crit_color = "#ef4444" if crit > 0 else "#22c55e"
        high_color = "#ef4444" if high > 0 else "#f59e0b"

        # Label: "LATEST" if there's a deployed row coming, otherwise just the name
        drift = findings.get("drift", {})
        has_deployed_row = drift.get("has_update") and findings.get("deployed_findings")

        # Build ECR Console link for the latest image scan (admin only)
        repo_name = findings.get("repo", "")
        latest_digest = findings.get("latest_digest", "")
        scan_url = _ecr_scan_url(repo_name, latest_digest) if admin else ""
        if scan_url:
            name_html = (
                f'<a href="{_e(scan_url)}" target="_blank" rel="noopener" '
                f'style="color:#e2e8f0; text-decoration:none;" '
                f'title="View scan findings in ECR Console">{_e(name)}</a>'
                f' <a href="{_e(scan_url)}" target="_blank" rel="noopener" '
                f'style="color:#4b5563; font-size:10px; text-decoration:none;"'
                f">&#x2197;</a>"
            )
        else:
            name_html = _e(name)

        row_label = f"{name_html} {_sec_badge('LATEST', 'blue')}" if has_deployed_row else name_html

        rows += (
            f"<tr>"
            f'<td style="color:#e2e8f0; font-weight:600;">{row_label}</td>'
            f'<td><span style="color:#6b7280; font-size:12px;">{_e(tags)}</span></td>'
            f'<td><span style="color:#4b5563;">{_e(scan_date)}</span></td>'
            f'<td style="text-align:center;"><span style="color:{crit_color}; font-weight:700;">'
            f"{crit}</span></td>"
            f'<td style="text-align:center;"><span style="color:{high_color}; font-weight:700;">'
            f"{high}</span></td>"
            f'<td style="text-align:center;"><span style="color:#f59e0b;">{medium}</span></td>'
            f'<td style="text-align:center;"><span style="color:#22c55e;">{low}</span></td>'
            f'<td style="text-align:center;"><span style="color:#e2e8f0; font-weight:600;">'
            f"{total}</span></td>"
            f"</tr>"
        )

        # Sub-row for DEPLOYED image when it differs from latest
        if has_deployed_row:
            dep = findings["deployed_findings"]
            dep_scan = dep.get("scan_date", "")[:10] if dep.get("scan_date") else "-"
            dep_crit = dep.get("critical", 0)
            dep_high = dep.get("high", 0)
            dep_medium = dep.get("medium", 0)
            dep_low = dep.get("low", 0)
            dep_total = dep.get("total", 0)
            s = drift.get("staleness", "unknown")
            s_badge_color = _STALENESS_BADGE.get(s, "gray")

            # Link DEPLOYED badge to its own scan results (admin only)
            deployed_digest = findings.get("deployed_digest", "")
            dep_scan_url = _ecr_scan_url(repo_name, deployed_digest) if admin else ""
            if dep_scan_url:
                dep_badge = (
                    f'<a href="{_e(dep_scan_url)}" target="_blank" rel="noopener" '
                    f'style="text-decoration:none;">{_sec_badge("DEPLOYED", s_badge_color)}</a>'
                )
            else:
                dep_badge = _sec_badge("DEPLOYED", s_badge_color)

            rows += (
                f'<tr style="background:#0a0a12;">'
                f'<td style="padding-left:24px; color:#6b7280; font-size:11px;">'
                f"  {dep_badge}</td>"
                f'<td><span style="color:#4b5563; font-size:11px;">pinned digest</span></td>'
                f'<td><span style="color:#4b5563; font-size:11px;">{_e(dep_scan)}</span></td>'
                f'<td style="text-align:center;"><span style="color:'
                f'{("#ef4444" if dep_crit > 0 else "#22c55e")}; font-size:11px;">'
                f"{dep_crit}</span></td>"
                f'<td style="text-align:center;"><span style="color:'
                f'{("#ef4444" if dep_high > 0 else "#f59e0b")}; font-size:11px;">'
                f"{dep_high}</span></td>"
                f'<td style="text-align:center;"><span style="color:#f59e0b; font-size:11px;">'
                f"{dep_medium}</span></td>"
                f'<td style="text-align:center;"><span style="color:#22c55e; font-size:11px;">'
                f"{dep_low}</span></td>"
                f'<td style="text-align:center;"><span style="color:#6b7280; font-size:11px;">'
                f"{dep_total}</span></td>"
                f"</tr>"
            )

    return f"""<h2>5. Vulnerability Intelligence</h2>
    <div style="color:#4b5563; font-size:11px; margin-bottom:12px;">
      ECR scan findings per container image. When the deployed digest differs from the
      latest available, both rows are shown for comparison. Scanning covers OS packages;
      application-level dependencies (npm, pip) require separate tooling.
    </div>
    <table>
      <thead><tr>
        <th>Container</th>
        <th>Tag</th>
        <th>Last Scan</th>
        <th style="text-align:center;">Critical</th>
        <th style="text-align:center;">High</th>
        <th style="text-align:center;">Medium</th>
        <th style="text-align:center;">Low</th>
        <th style="text-align:center;">Total</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <div style="font-size:10px; color:#4b5563; margin-top:8px;">
      Containers with CRITICAL findings unfixed for 30+ days should be prioritized for update.
      {f"Detailed scan results linked to ECR Console. {_admin_only('Scan detail links visible to admins only')}" if admin else "Detailed scan results require admin access."}
    </div>"""


# ── Main render function ─────────────────────────────────────────────────────


def render_security(user_email=""):
    """Render the full security posture page."""
    posture = get_security_posture()

    summary_html = _render_summary_cards(posture)
    exposure_html = _render_exposure_section(posture["exposure_map"])
    auth_html = _render_auth_section(posture["auth_gaps"])
    monitoring_html = _render_monitoring_section(posture["monitoring"])
    admin = is_admin(user_email)
    images_html = _render_images_section(posture["ecr_findings"], posture.get("container_inventory"), admin=admin)
    vuln_html = _render_vuln_section(posture["ecr_findings"], admin=admin)

    nav_html = render_nav("security", user_email, meta=f"{len(posture['exposure_map'])} exposed services")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Security Posture // __BRAND__</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    background: #050508; color: #e2e8f0; line-height: 1.6;
    min-height: 100vh;
  }}
  ::selection {{ background: rgba(239,68,68,0.3); }}

  {NAV_CSS}

  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}

  h1 {{ font-size: 20px; font-weight: 700; margin-bottom: 4px; color: #f1f5f9;
        letter-spacing: 0.02em; }}
  h2 {{ font-size: 14px; font-weight: 700; color: #94a3b8; margin: 32px 0 12px 0;
        text-transform: uppercase; letter-spacing: 0.08em;
        padding-top: 16px; border-top: 1px solid #1e1e2e; }}
  h2:first-of-type {{ margin-top: 0; border-top: 0; padding-top: 0; }}

  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 12px; margin-bottom: 20px; }}
  .card {{ background: #111118; border: 1px solid #1e1e2e; border-radius: 8px;
           padding: 16px 20px; transition: border-color 0.15s; }}
  .card:hover {{ border-color: #2a2a3a; }}
  .card .label {{ font-size: 10px; color: #6b7280; text-transform: uppercase;
                  letter-spacing: 0.12em; }}
  .card .value {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}

  table {{ width: 100%; border-collapse: collapse; background: #111118;
           border: 1px solid #1e1e2e; border-radius: 8px; overflow: hidden; }}
  th {{ background: #0d0d14; text-align: left; padding: 10px 12px; font-size: 10px;
       color: #6b7280; text-transform: uppercase; letter-spacing: 0.1em;
       border-bottom: 1px solid #1e1e2e; }}
  td {{ padding: 10px 12px; border-top: 1px solid #141420; font-size: 13px; color: #cbd5e1; }}
  tr:hover td {{ background: #141420; }}

  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 10px; font-weight: 700; letter-spacing: 0.06em; }}

  .empty-box {{ background: #111118; border: 1px solid #1e1e2e; border-radius: 8px;
                padding: 24px; text-align: center; color: #374151; font-size: 13px; }}
</style>
</head>
<body>
<div class="scanline" style="position:fixed; top:0; left:0; right:0; height:2px;
     background:linear-gradient(90deg, transparent, #ef4444, transparent);
     animation:scan 3s ease-in-out infinite; opacity:0.3; pointer-events:none; z-index:999;"></div>
<style>@keyframes scan {{ 0%,100% {{ transform: translateX(-100%); }} 50% {{ transform: translateX(100%); }} }}</style>
{nav_html}
<div class="container">
  <h1>Security Posture</h1>
  <div style="color:#4b5563; font-size:12px; margin-bottom:16px;">
    Single-pane view of the environment's security status. Not an exhaustive security tool
    &mdash; surfaces what matters most for operational awareness.
  </div>

  {summary_html}
  {exposure_html}
  {auth_html}
  {monitoring_html}
  {images_html}
  {vuln_html}

  <div style="margin-top:32px; text-align:center; font-size:10px; color:#374151; padding-bottom:16px;">
    <a href="/services" style="color:#3b82f6; text-decoration:none;">Services</a> &middot;
    <a href="/architecture/custom" style="color:#3b82f6; text-decoration:none;">Architecture</a> &middot;
    <a href="/" style="color:#3b82f6; text-decoration:none;">Dashboard</a>
  </div>
</div>
</body>
</html>"""

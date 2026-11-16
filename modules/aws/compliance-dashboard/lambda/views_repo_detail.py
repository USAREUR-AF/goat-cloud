"""
Per-repo detail page — landing page for /repos/<slug>/.

Shows repo metadata, resource summary, compliance rate, and links
to the repo's tree, network, architecture, and interactions views.
"""

import contextlib
import urllib.parse
from datetime import UTC, datetime

from data_repo import get_repo_info, get_repo_resources
from views_base import _badge, _e, _layout, _rate_color, _safe_url

# IaC type badge colors (same as views_repos.py)
_IAC_COLORS = {
    "terraform": "purple",
    "serverless": "orange",
    "pulumi": "blue",
    "cdk": "green",
    "cloudformation": "gray",
    "manual": "red",
}


def render_repo_detail(slug, user_email=""):
    """Render the per-repo detail landing page."""
    info = get_repo_info(slug)
    resources = get_repo_resources(slug)

    if not info:
        content = f"""
        <div style="text-align:center; padding:48px;">
          <h1 style="color:#ef4444; font-size:18px;">Repository Not Found</h1>
          <p style="color:#6b7280; margin-top:12px;">
            No IaC registry entry found for <code>{_e(slug)}</code>.
          </p>
          <a href="/repos" style="color:#3b82f6; text-decoration:none; margin-top:16px; display:inline-block;">
            &larr; Back to Repositories
          </a>
        </div>"""
        return _layout(f"Repo: {slug}", content, user_email, active_page="repos")

    repo_url = _safe_url(info.get("repo_url", ""))
    iac_type = info.get("iac_type", "unknown")
    last_deploy = info.get("last_deploy", "—")
    resource_count = len(resources)

    # Compliance stats
    compliant_count = sum(1 for r in resources if r["compliant"])
    nc_count = resource_count - compliant_count
    pct = round(100 * compliant_count / resource_count) if resource_count else 100
    pct_color = _rate_color(pct)

    iac_badge = _badge(_e(iac_type), color=_IAC_COLORS.get(iac_type, "gray"))

    # Lifecycle / ephemeral detection
    lifecycle_tags = {r["tags"].get("Lifecycle", "") for r in resources if r.get("tags")}
    is_ephemeral = "temporary" in lifecycle_tags
    expires_at_values = []
    for r in resources:
        eat = (r.get("tags") or {}).get("ExpiresAt", "")
        if eat:
            with contextlib.suppress(ValueError, TypeError):
                expires_at_values.append(datetime.fromisoformat(eat.replace("Z", "+00:00")))
    nearest_expiry = min(expires_at_values) if expires_at_values else None
    now = datetime.now(UTC)

    # Group resources by service type for summary
    svc_counts = {}
    for r in resources:
        rtype = r["resource_type"]
        svc = rtype.split(":")[0] if ":" in rtype else rtype
        svc_counts[svc] = svc_counts.get(svc, 0) + 1

    # Sub-page navigation
    base = f"/repos/{urllib.parse.quote(slug, safe='')}"

    content = f"""
    <div style="margin-bottom:16px;">
      <a href="/repos" style="color:#6b7280; text-decoration:none; font-size:12px;">
        &larr; All Repositories
      </a>
    </div>

    <div style="display:flex; align-items:center; gap:12px; margin-bottom:8px;">
      <h1 style="font-size:20px; font-weight:700; letter-spacing:1px; color:#f1f5f9; margin:0;">
        {_e(slug.upper())}
      </h1>
      {iac_badge}
    </div>

    {"<p style='color:#6b7280; font-size:12px; margin-bottom:20px;'><a href='" + _e(repo_url) + "' target='_blank' style='color:#60a5fa; text-decoration:none;'>" + _e(repo_url) + "</a></p>" if repo_url else ""}
"""

    # Lifecycle banner for ephemeral repos
    if is_ephemeral:
        if nearest_expiry:
            if nearest_expiry > now:
                delta = nearest_expiry - now
                hrs = int(delta.total_seconds() // 3600)
                mins = int((delta.total_seconds() % 3600) // 60)
                time_str = f"{hrs}h {mins}m remaining"
                banner_color = "#f59e0b"  # amber
                border_color = "rgba(245,158,11,0.3)"
            else:
                days_ago = (now - nearest_expiry).days
                time_str = f"expired {days_ago}d ago — pending teardown"
                banner_color = "#ef4444"  # red
                border_color = "rgba(239,68,68,0.3)"
            expiry_display = nearest_expiry.strftime("%Y-%m-%d %H:%M UTC")
        else:
            time_str = "no ExpiresAt set"
            expiry_display = "—"
            banner_color = "#f59e0b"
            border_color = "rgba(245,158,11,0.3)"

        content += f"""
    <div class="lifecycle-banner" style="background:rgba(245,158,11,0.06); border:1px solid {border_color};
         border-radius:8px; padding:12px 16px; margin-bottom:20px; display:flex; align-items:center; gap:12px;">
      <span style="font-size:18px; line-height:1;">&#9202;</span>
      <div>
        <div style="font-size:13px; font-weight:700; color:{banner_color}; letter-spacing:0.5px;">
          EPHEMERAL &mdash; {_e(time_str)}
        </div>
        <div style="font-size:11px; color:#94a3b8; margin-top:2px;">
          Lifecycle=temporary &middot; ExpiresAt: {_e(expiry_display)} &middot;
          Resources auto-torn-down after expiry in dev/staging
        </div>
      </div>
    </div>
"""

    content += f"""
    <!-- Stats -->
    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:10px; margin-bottom:24px;">
      <div class="stat-card">
        <div class="stat-label">Resources</div>
        <div class="stat-value">{resource_count}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Compliant</div>
        <div class="stat-value" style="color:#22c55e;">{compliant_count}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Non-Compliant</div>
        <div class="stat-value" style="color:{"#ef4444" if nc_count else "#22c55e"};">{nc_count}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Compliance</div>
        <div class="stat-value" style="color:{pct_color};">{pct}%</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Last Deploy</div>
        <div class="stat-value" style="font-size:14px; color:#94a3b8;">{_e(last_deploy[:19] if len(last_deploy) > 19 else last_deploy)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Lifecycle</div>
        <div class="stat-value" style="font-size:14px; color:{"#f59e0b" if is_ephemeral else "#22c55e"};">{"temporary" if is_ephemeral else "persistent"}</div>
      </div>
    </div>

    <!-- View Navigation -->
    <div style="display:flex; gap:8px; margin-bottom:24px; flex-wrap:wrap;">
      <a href="{base}/architecture" class="view-btn">
        <span class="view-icon">&#9634;</span> Architecture
      </a>
      <a href="{base}/tree" class="view-btn">
        <span class="view-icon">&#9660;</span> Tree
      </a>
      <a href="{base}/network" class="view-btn">
        <span class="view-icon">&#9678;</span> Network
      </a>
    </div>

    <!-- Service Type Summary -->
    <h2 style="font-size:13px; font-weight:700; color:#94a3b8; letter-spacing:1px; margin-bottom:10px;">
      RESOURCE BREAKDOWN
    </h2>
    <div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:24px;">
"""

    for svc, count in sorted(svc_counts.items(), key=lambda x: -x[1]):
        content += f"""
      <div style="background:#1e293b; border:1px solid #334155; border-radius:6px; padding:8px 14px;
                   display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:#94a3b8;">{_e(svc)}</span>
        <span style="font-size:14px; font-weight:700; color:#f1f5f9;">{count}</span>
      </div>"""

    content += """
    </div>

    <!-- Resource Table -->
    <h2 style="font-size:13px; font-weight:700; color:#94a3b8; letter-spacing:1px; margin-bottom:10px;">
      ALL RESOURCES
    </h2>
    <table class="res-table">
      <thead>
        <tr>
          <th>Status</th>
          <th>Type</th>
          <th>Name</th>
          <th>Region</th>
          <th>Lifecycle</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
"""

    for r in sorted(resources, key=lambda x: (x["compliant"], x["resource_type"], x["name"])):
        dot_color = "#22c55e" if r["compliant"] else "#ef4444"
        dot_title = "Compliant" if r["compliant"] else "Non-compliant"
        arn = r["arn"]
        console = _safe_url(r.get("console_url", ""))

        actions = ""
        if console:
            actions += f'<a href="{_e(console)}" target="_blank" style="color:#3b82f6; text-decoration:none; font-size:11px;">AWS</a>'
        if not r["compliant"] and arn:
            cure_url = f"/cure?arn={urllib.parse.quote(arn, safe='')}"
            if actions:
                actions += " &middot; "
            actions += f'<a href="{_e(cure_url)}" style="color:#f59e0b; text-decoration:none; font-size:11px;">CURE</a>'

        # Lifecycle cell
        r_lifecycle = (r.get("tags") or {}).get("Lifecycle", "")
        r_expires = (r.get("tags") or {}).get("ExpiresAt", "")
        if r_lifecycle == "temporary" and r_expires:
            try:
                exp_dt = datetime.fromisoformat(r_expires.replace("Z", "+00:00"))
                if exp_dt > now:
                    hrs_left = int((exp_dt - now).total_seconds() // 3600)
                    lc_html = f'<span style="color:#f59e0b; font-size:11px;" title="ExpiresAt: {_e(r_expires)}">{hrs_left}h left</span>'
                else:
                    lc_html = '<span style="color:#ef4444; font-size:11px;">expired</span>'
            except (ValueError, TypeError):
                lc_html = '<span style="color:#f59e0b; font-size:11px;">temporary</span>'
        elif r_lifecycle:
            lc_color = "#22c55e" if r_lifecycle == "persistent" else "#94a3b8"
            lc_html = f'<span style="color:{lc_color}; font-size:11px;">{_e(r_lifecycle)}</span>'
        else:
            lc_html = '<span style="color:#4b5563; font-size:11px;">&mdash;</span>'

        content += f"""
        <tr>
          <td><span style="display:inline-block; width:8px; height:8px; border-radius:50%;
                           background:{dot_color}; box-shadow:0 0 4px {dot_color};"
                     title="{dot_title}"></span></td>
          <td style="color:#60a5fa; font-size:12px;">{_e(r["resource_type"])}</td>
          <td style="color:#e2e8f0; font-size:12px; max-width:300px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
            {_e(r["name"]) or "<span style='color:#4b5563;'>—</span>"}
          </td>
          <td style="color:#6b7280; font-size:11px;">{_e(r["region"])}</td>
          <td>{lc_html}</td>
          <td>{actions}</td>
        </tr>"""

    content += """
      </tbody>
    </table>

    <style>
      .stat-card {
        background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 14px;
      }
      .stat-label {
        font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #94a3b8; margin-bottom: 4px;
      }
      .stat-value {
        font-size: 24px; font-weight: 700; color: #f1f5f9;
      }
      .view-btn {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 8px 16px; border-radius: 6px;
        background: rgba(59,130,246,0.06); border: 1px solid rgba(59,130,246,0.2);
        color: #3b82f6; text-decoration: none; font-size: 13px; font-weight: 600;
        transition: all 0.15s;
      }
      .view-btn:hover {
        background: rgba(59,130,246,0.12); border-color: rgba(59,130,246,0.4);
        transform: translateY(-1px);
      }
      .view-icon { font-size: 14px; }
      .res-table {
        width: 100%; border-collapse: collapse; font-size: 13px;
      }
      .res-table th {
        text-align: left; padding: 8px 12px; background: #1e293b; color: #94a3b8;
        font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px;
        border-bottom: 1px solid #334155;
      }
      .res-table td {
        padding: 6px 12px; border-bottom: 1px solid #1e293b;
      }
      .res-table tr:hover { background: #1e293b; }
      code {
        background: #334155; padding: 2px 6px; border-radius: 4px; font-size: 12px;
      }
    </style>
"""

    return _layout(f"Repo: {slug}", content, user_email, active_page="repos")

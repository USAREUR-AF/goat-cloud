"""
Services Table page — auto-generated service catalog from MODULE_META + live TF state.

Shows all deployed services with compliance status, zone filtering,
and summary statistics.  Linked from the main navigation bar.

Three sections:
  1. Hosted Services — self-hosted apps and serverless functions we deploy
  2. AWS Platform Services — underlying AWS utility services (CloudTrail, WAF, etc.)
  3. Unmanaged Resources — discovered in AWS but not in Terraform state
"""

import urllib.parse

from arch_shared import get_module_meta
from auth import is_admin
from data import get_compliance_report, get_tf_module_tree, get_unmanaged_resources
from views_base import (
    _ZONE_BADGE,
    NAV_CSS,
    _admin_only,
    _badge,
    _e,
    _rate_color,
    _safe_url,
    render_nav,
)

# ── Zone display config ────────────────────────────────────────────────────

_ZONE_COLORS = {
    "public": "#22c55e",
    "apps": "#3b82f6",
    "identity": "#a855f7",
    "serverless": "#f97316",
    "security": "#ef4444",
    "infra": "#6b7280",
}

_ZONE_LABELS = {
    "public": "Public",
    "apps": "Apps",
    "identity": "Identity",
    "serverless": "Serverless",
    "security": "Security",
    "infra": "Infra",
}

# Canonical icon key -> type label for the "Type" column
_ICON_TYPE_LABELS = {
    "ecs": "ECS",
    "ec2": "EC2",
    "lambda": "Lambda",
    "redis": "ElastiCache",
    "alb": "ALB",
    "vpn": "VPN",
    "vpc": "VPC",
    "waf": "WAF",
    "trail": "CloudTrail",
    "shield": "ACM/Shield",
    "monitoring": "CloudWatch",
    "guardduty": "GuardDuty",
    "cloudflare": "Cloudflare",
}

# Filter tabs (label, zone value — empty string means "all")
_ZONE_FILTERS = [
    ("All", ""),
    ("Public", "public"),
    ("Apps", "apps"),
    ("Identity", "identity"),
    ("Serverless", "serverless"),
]


def _build_service_list(tree_lookup):
    """Build sorted service lists from MODULE_META, split by tier."""
    hosted = []
    platform = []
    total_resources = 0
    total_compliant = 0

    zone_order = ["public", "apps", "identity", "serverless", "security", "infra"]

    for module_addr, meta in sorted(
        get_module_meta().items(),
        key=lambda kv: (
            zone_order.index(kv[1]["zone"]) if kv[1]["zone"] in zone_order else 99,
            kv[1]["name"].lower(),
        ),
    ):
        tree_entry = tree_lookup.get(module_addr)
        if tree_entry:
            stats = tree_entry["stats"]
            res_total = stats["total"]
            res_compliant = stats["compliant"]
            res_nc = stats["non_compliant"]
            pct = round(100 * res_compliant / res_total) if res_total else 100
        else:
            res_total = 0
            res_compliant = 0
            res_nc = 0
            pct = 100

        total_resources += res_total
        total_compliant += res_compliant

        svc = {
            "module": module_addr,
            "name": meta["name"],
            "zone": meta["zone"],
            "icon": meta["icon"],
            "host": meta.get("host", ""),
            "auth": meta.get("auth", ""),
            "notes": meta.get("notes", ""),
            "desc": meta.get("desc", ""),
            "console": meta.get("console", ""),
            "resources": res_total,
            "compliant": res_compliant,
            "non_compliant": res_nc,
            "pct": pct,
        }

        if meta.get("tier") == "platform":
            platform.append(svc)
        else:
            hosted.append(svc)

    return hosted, platform, total_resources, total_compliant


def _render_status_dot(non_compliant):
    """Render green or red status dot."""
    if non_compliant == 0:
        return (
            '<span style="display:inline-block; width:10px; height:10px; border-radius:50%; '
            'background:#22c55e; box-shadow:0 0 6px #22c55e;"></span>'
        )
    return (
        '<span style="display:inline-block; width:10px; height:10px; border-radius:50%; '
        'background:#ef4444; box-shadow:0 0 6px #ef4444;"></span>'
    )


def _render_hosted_rows(services):
    """Render table rows for hosted services."""
    current_zone = None
    rows = ""
    for svc in services:
        zone = svc["zone"]
        zone_color = _ZONE_COLORS.get(zone, "#6b7280")
        zone_label = _ZONE_LABELS.get(zone, zone.title())
        type_label = _ICON_TYPE_LABELS.get(svc["icon"], svc["icon"].upper())
        pct_color = _rate_color(svc["pct"])

        # Zone group header
        if zone != current_zone:
            current_zone = zone
            rows += (
                f'<tr class="zone-header" data-zone="{_e(zone)}" data-tier="hosted">'
                f'<td colspan="9" style="background:#0d0d14; padding:10px 12px; font-size:11px; '
                f"font-weight:700; color:{zone_color}; text-transform:uppercase; "
                f'letter-spacing:0.1em; border-top:2px solid {zone_color};">'
                f"{_e(zone_label)}</td></tr>"
            )

        dot = _render_status_dot(svc["non_compliant"])

        # Service name linked to detail page
        detail_path = f"/detail/{urllib.parse.quote(svc['module'], safe='')}"
        name_cell = (
            f'<a href="{detail_path}" style="color:#e2e8f0; text-decoration:none; '
            f'font-weight:600;">{_e(svc["name"])}</a>'
            f'<div style="font-size:10px; color:#4b5563; margin-top:2px;">{_e(svc["desc"])}</div>'
        )

        zone_badge = _badge(_e(zone_label), _ZONE_BADGE.get(zone, "gray"))

        type_badge = f'<span style="color:#6b7280; font-size:12px;">{_e(type_label)}</span>'

        auth_cell = (
            f'<span style="color:#a855f7; font-size:12px;">{_e(svc["auth"])}</span>'
            if svc["auth"]
            else '<span style="color:#374151; font-size:12px;">-</span>'
        )

        notes_cell = (
            f'<span style="color:#6b7280; font-size:12px;">{_e(svc["notes"])}</span>'
            if svc["notes"]
            else '<span style="color:#374151; font-size:12px;">-</span>'
        )

        if svc["host"]:
            url_cell = (
                f'<a href="https://{_e(svc["host"])}" target="_blank" rel="noopener" '
                f'style="color:#3b82f6; text-decoration:none; font-size:12px;">'
                f"{_e(svc['host'])}</a>"
            )
        else:
            url_cell = '<span style="color:#374151; font-size:12px;">-</span>'

        res_cell = (
            f'<span style="color:#e2e8f0; font-weight:600;">{_e(str(svc["resources"]))}</span>'
            if svc["resources"]
            else '<span style="color:#374151;">0</span>'
        )

        pct_cell = f'<span style="color:{pct_color}; font-weight:700;">{svc["pct"]}%</span>'

        rows += (
            f'<tr class="svc-row" data-zone="{_e(zone)}" data-tier="hosted">'
            f'<td style="text-align:center;">{dot}</td>'
            f"<td>{name_cell}</td>"
            f"<td>{zone_badge}</td>"
            f"<td>{type_badge}</td>"
            f"<td>{auth_cell}</td>"
            f"<td>{notes_cell}</td>"
            f"<td>{url_cell}</td>"
            f'<td style="text-align:center;">{res_cell}</td>'
            f'<td style="text-align:center;">{pct_cell}</td>'
            f"</tr>"
        )

    return rows


def _render_platform_rows(services):
    """Render table rows for AWS platform services."""
    current_zone = None
    rows = ""
    for svc in services:
        zone = svc["zone"]
        zone_color = _ZONE_COLORS.get(zone, "#6b7280")
        zone_label = _ZONE_LABELS.get(zone, zone.title())
        type_label = _ICON_TYPE_LABELS.get(svc["icon"], svc["icon"].upper())
        pct_color = _rate_color(svc["pct"])

        # Zone group header
        if zone != current_zone:
            current_zone = zone
            rows += (
                f'<tr class="zone-header" data-zone="{_e(zone)}" data-tier="platform">'
                f'<td colspan="6" style="background:#0d0d14; padding:10px 12px; font-size:11px; '
                f"font-weight:700; color:{zone_color}; text-transform:uppercase; "
                f'letter-spacing:0.1em; border-top:2px solid {zone_color};">'
                f"{_e(zone_label)}</td></tr>"
            )

        dot = _render_status_dot(svc["non_compliant"])

        detail_path = f"/detail/{urllib.parse.quote(svc['module'], safe='')}"
        name_cell = (
            f'<a href="{detail_path}" style="color:#e2e8f0; text-decoration:none; '
            f'font-weight:600;">{_e(svc["name"])}</a>'
            f'<div style="font-size:10px; color:#4b5563; margin-top:2px;">{_e(svc["desc"])}</div>'
        )

        type_badge = f'<span style="color:#6b7280; font-size:12px;">{_e(type_label)}</span>'

        if _safe_url(svc["console"]):
            console_cell = (
                f'<a href="{_e(_safe_url(svc["console"]))}" target="_blank" rel="noopener" '
                f'style="color:#f97316; text-decoration:none; font-size:12px;">'
                f"Open in Console &rarr;</a>"
            )
        else:
            console_cell = '<span style="color:#374151; font-size:12px;">-</span>'

        res_cell = (
            f'<span style="color:#e2e8f0; font-weight:600;">{_e(str(svc["resources"]))}</span>'
            if svc["resources"]
            else '<span style="color:#374151;">0</span>'
        )

        pct_cell = f'<span style="color:{pct_color}; font-weight:700;">{svc["pct"]}%</span>'

        rows += (
            f'<tr class="svc-row" data-zone="{_e(zone)}" data-tier="platform">'
            f'<td style="text-align:center;">{dot}</td>'
            f"<td>{name_cell}</td>"
            f"<td>{type_badge}</td>"
            f"<td>{console_cell}</td>"
            f'<td style="text-align:center;">{res_cell}</td>'
            f'<td style="text-align:center;">{pct_cell}</td>'
            f"</tr>"
        )

    return rows


def _render_unmanaged_section(report):
    """Render the unmanaged resources section with grouped tables."""
    unmanaged = get_unmanaged_resources(report)

    if unmanaged["total"] == 0:
        return (
            '<div style="background:#111118; border:1px solid #1e1e2e; border-radius:8px; '
            'padding:24px; text-align:center; color:#374151; font-size:13px;">'
            "No unmanaged resources detected in the latest scan.</div>"
        )

    html = (
        f'<div style="color:#4b5563; font-size:11px; margin-bottom:12px;">'
        f"{unmanaged['total']} resource{'s' if unmanaged['total'] != 1 else ''} "
        f"discovered in AWS but not managed by Terraform."
        f"</div>"
    )

    for group in unmanaged["groups"]:
        count = len(group["resources"])
        collapsed = count >= 5

        group_id = group["key"].replace(":", "-").replace(" ", "-")
        html += (
            f'<div class="unm-group" style="margin-bottom:16px;">'
            f'<div class="unm-group-header" data-target="unm-{_e(group_id)}" '
            f'style="display:flex; align-items:center; gap:8px; padding:8px 12px; '
            f"background:#111118; border:1px solid #1e1e2e; border-radius:6px 6px "
            f'{"6px 6px" if collapsed else "0 0"}; cursor:pointer; user-select:none;">'
            f'<span class="unm-arrow" style="color:#f59e0b; font-size:10px; '
            f'transition:transform 0.15s;">{"&#9654;" if collapsed else "&#9660;"}</span>'
            f'<span style="color:#f59e0b; font-weight:700; font-size:12px; '
            f'text-transform:uppercase; letter-spacing:0.06em;">{_e(group["label"])}</span>'
            f'<span style="color:#6b7280; font-size:11px;">'
            f"{count} resource{'s' if count != 1 else ''}</span>"
            f"</div>"
        )

        display = "none" if collapsed else ""
        html += (
            f'<div id="unm-{_e(group_id)}" style="display:{display};">'
            f'<table style="border-top:0; border-radius:0 0 6px 6px;">'
            f"<thead><tr>"
            f"<th>Name</th>"
            f"<th>Type</th>"
            f"<th>Region</th>"
            f"<th>Age</th>"
            f'<th style="text-align:center;">Click-Ops Likelihood</th>'
            f"</tr></thead><tbody>"
        )

        for res in group["resources"]:
            name = res["name"] or res["arn"].split(":")[-1]

            age = ""
            if res.get("running_duration_days") is not None:
                days = int(res["running_duration_days"])
                age = f"{days}d"

            conf = res.get("confidence", "MEDIUM")
            conf_badge_color = "red" if conf == "HIGH" else "amber"

            html += (
                f"<tr>"
                f'<td><span style="color:#e2e8f0; font-weight:600;">{_e(name)}</span></td>'
                f'<td><span style="color:#6b7280; font-size:12px;">{_e(res["resource_type"])}</span></td>'
                f'<td><span style="color:#6b7280; font-size:12px;">{_e(res["region"])}</span></td>'
                f'<td><span style="color:#6b7280; font-size:12px;">{_e(age)}</span></td>'
                f'<td style="text-align:center;">'
                f"{_badge(_e(conf), conf_badge_color)}</td>"
                f"</tr>"
            )

        html += "</tbody></table></div></div>"

    return html


def render_services(user_email=""):
    """Render the full services table page."""
    report = get_compliance_report()
    tree = get_tf_module_tree(report)
    tree_lookup = {mod["module"]: mod for mod in tree}

    hosted, platform, total_resources, total_compliant = _build_service_list(tree_lookup)
    user_is_admin = is_admin(user_email)
    unmanaged = get_unmanaged_resources(report) if user_is_admin else {"groups": [], "total": 0, "total_cost": 0}

    total_services = len(hosted) + len(platform)
    overall_pct = round(100 * total_compliant / total_resources) if total_resources else 100

    unmanaged_card = ""
    if user_is_admin and unmanaged["total"] > 0:
        unmanaged_card = (
            f'<div class="card" style="border-color:rgba(245,158,11,0.3);">'
            f'<div class="label">Unmanaged</div>'
            f'<div class="value" style="color:#f59e0b;">{unmanaged["total"]}</div>'
            f"</div>"
        )

    # ── Summary cards ───────────────────────────────────────────────────────
    summary_html = f"""
    <div class="cards">
      <div class="card">
        <div class="label">Hosted Services</div>
        <div class="value" style="color:#e2e8f0;">{len(hosted)}</div>
      </div>
      <div class="card">
        <div class="label">Platform Services</div>
        <div class="value" style="color:#6b7280;">{len(platform)}</div>
      </div>
      {unmanaged_card}
      <div class="card">
        <div class="label">Total Resources</div>
        <div class="value" style="color:#e2e8f0;">{total_resources}</div>
      </div>
      <div class="card">
        <div class="label">Overall Compliance</div>
        <div class="value" style="color:{_rate_color(overall_pct)};">{overall_pct}%</div>
      </div>
    </div>"""

    # ── Zone filter buttons (hosted only) ───────────────────────────────────
    filter_buttons = []
    for label, zone_val in _ZONE_FILTERS:
        if zone_val:
            zone_color = _ZONE_COLORS.get(zone_val, "#6b7280")
            filter_buttons.append(
                f'<button class="zone-btn" data-zone="{zone_val}" style="--zone-color:{zone_color};">{label}</button>'
            )
        else:
            filter_buttons.append('<button class="zone-btn active" data-zone="">All</button>')
    filter_html = '<div class="zone-filters">' + "\n      ".join(filter_buttons) + "</div>"

    hosted_rows = _render_hosted_rows(hosted)
    platform_rows = _render_platform_rows(platform)
    unmanaged_html = _render_unmanaged_section(report) if user_is_admin else ""

    nav_html = render_nav("services", user_email, meta=f"{total_services} services")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Services // __BRAND__</title>
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
  h2 {{ font-size: 14px; font-weight: 700; color: #94a3b8; margin: 28px 0 12px 0;
        text-transform: uppercase; letter-spacing: 0.08em; }}
  h2:first-of-type {{ margin-top: 0; }}

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
  tr.zone-header:hover td {{ background: #0d0d14; }}

  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 10px; font-weight: 700; letter-spacing: 0.06em; }}

  /* ── Zone filter buttons ── */
  .zone-filters {{ display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }}
  .zone-btn {{
    padding: 6px 14px; border-radius: 4px; font-size: 12px; font-weight: 600;
    cursor: pointer; border: 1px solid rgba(255,255,255,0.06); background: #111118;
    color: #6b7280; font-family: inherit; letter-spacing: 0.03em;
    transition: all 0.15s;
  }}
  .zone-btn:hover {{
    color: #e2e8f0; border-color: #374151; background: rgba(255,255,255,0.03);
  }}
  .zone-btn.active {{
    color: #ef4444; border-color: rgba(239,68,68,0.3);
    background: rgba(239,68,68,0.08);
  }}
  .zone-btn[data-zone]:not([data-zone=""]).active {{
    color: var(--zone-color); border-color: var(--zone-color);
    background: rgba(255,255,255,0.05);
  }}

  .section-divider {{
    border: 0; border-top: 1px solid #1e1e2e; margin: 32px 0 8px 0;
  }}
</style>
</head>
<body>
<div class="scanline" style="position:fixed; top:0; left:0; right:0; height:2px;
     background:linear-gradient(90deg, transparent, #ef4444, transparent);
     animation:scan 3s ease-in-out infinite; opacity:0.3; pointer-events:none; z-index:999;"></div>
<style>@keyframes scan {{ 0%,100% {{ transform: translateX(-100%); }} 50% {{ transform: translateX(100%); }} }}</style>
{nav_html}
<div class="container">
  <h1>Service Catalog</h1>
  <div style="color:#4b5563; font-size:12px; margin-bottom:16px;">
    All deployed services from Terraform module metadata and live state.
  </div>

  {summary_html}

  <h2>Hosted Services</h2>
  {filter_html}

  <table>
    <thead>
      <tr>
        <th style="text-align:center; width:40px;">Status</th>
        <th>Service</th>
        <th>Zone</th>
        <th>Type</th>
        <th>Auth</th>
        <th>Notes</th>
        <th>URL</th>
        <th style="text-align:center;">Resources</th>
        <th style="text-align:center;">Compliance</th>
      </tr>
    </thead>
    <tbody>
      {hosted_rows}
    </tbody>
  </table>

  <hr class="section-divider">

  <h2>AWS Platform Services</h2>
  <div style="color:#4b5563; font-size:11px; margin-bottom:12px;">
    Underlying AWS services managed via Terraform for security, networking, and operations.
  </div>

  <table>
    <thead>
      <tr>
        <th style="text-align:center; width:40px;">Status</th>
        <th>Service</th>
        <th>Type</th>
        <th>Console</th>
        <th style="text-align:center;">Resources</th>
        <th style="text-align:center;">Compliance</th>
      </tr>
    </thead>
    <tbody>
      {platform_rows}
    </tbody>
  </table>

  {
        ""
        if not user_is_admin
        else f'''<hr class="section-divider">

  <h2 style="color:#f59e0b;">Unmanaged Resources{_admin_only("Unmanaged resources visible to admins only")}</h2>
  {unmanaged_html}'''
    }

  <div style="margin-top:24px; text-align:center; font-size:10px; color:#374151; padding-bottom:16px;">
    <a href="/architecture/custom" style="color:#3b82f6; text-decoration:none;">Architecture</a> &middot;
    <a href="/tree" style="color:#3b82f6; text-decoration:none;">TF Tree</a> &middot;
    <a href="/network" style="color:#3b82f6; text-decoration:none;">Network</a> &middot;
    <a href="/" style="color:#3b82f6; text-decoration:none;">Dashboard</a>
  </div>
</div>
<script>
(function() {{
  // Zone filter for hosted services
  var btns = document.querySelectorAll('.zone-btn');
  btns.forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      btns.forEach(function(b) {{ b.classList.remove('active'); }});
      btn.classList.add('active');
      var zone = btn.getAttribute('data-zone');
      var rows = document.querySelectorAll('[data-tier="hosted"]');
      rows.forEach(function(row) {{
        if (!zone || row.getAttribute('data-zone') === zone) {{
          row.style.display = '';
        }} else {{
          row.style.display = 'none';
        }}
      }});
    }});
  }});

  // Collapsible unmanaged resource groups
  document.querySelectorAll('.unm-group-header').forEach(function(header) {{
    header.addEventListener('click', function() {{
      var targetId = header.getAttribute('data-target');
      var target = document.getElementById(targetId);
      var arrow = header.querySelector('.unm-arrow');
      if (target.style.display === 'none') {{
        target.style.display = '';
        arrow.innerHTML = '&#9660;';
        header.style.borderRadius = '6px 6px 0 0';
      }} else {{
        target.style.display = 'none';
        arrow.innerHTML = '&#9654;';
        header.style.borderRadius = '6px';
      }}
    }});
  }});
}})();
</script>
</body>
</html>"""

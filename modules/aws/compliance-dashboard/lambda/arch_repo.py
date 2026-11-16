"""
Per-repo architecture views.

Three views following the canonical architecture hierarchy:
  /repos/<slug>/architecture                       — Landing page (two cards)
  /repos/<slug>/architecture/custom                — Containment diagram (service cards)
  /repos/<slug>/architecture/custom/<service_type> — Per-service-type detail (resources)

Reuses _render_zone_box() and _render_service_card() from arch_custom.py
so the visual output matches the platform repository's diagram.
Individual resources are shown on the drill-down detail page.
"""

import urllib.parse

from arch_custom import (
    ZONE_CARD_CSS,
    _esc,
    _render_zone_box,
)
from data_repo import (
    _SERVICE_TYPE_MAP,
    _service_from_type,
    get_repo_info,
    get_repo_resources,
    get_repo_tree,
)
from views_base import NAV_CSS, _e, _rate_color, _safe_url, render_nav

# Zone visual config (service-type zones, not network zones)
_ZONE_CONFIG = {
    "compute": {
        "label": "Compute",
        "color": "#f97316",
        "bg": "rgba(249,115,22,0.06)",
        "border": "rgba(249,115,22,0.35)",
    },
    "api": {
        "label": "API Layer",
        "color": "#3b82f6",
        "bg": "rgba(59,130,246,0.06)",
        "border": "rgba(59,130,246,0.35)",
    },
    "storage": {
        "label": "Storage",
        "color": "#22c55e",
        "bg": "rgba(34,197,94,0.06)",
        "border": "rgba(34,197,94,0.35)",
    },
    "security": {
        "label": "Security & IAM",
        "color": "#ef4444",
        "bg": "rgba(239,68,68,0.06)",
        "border": "rgba(239,68,68,0.35)",
    },
    "monitoring": {
        "label": "Monitoring",
        "color": "#a855f7",
        "bg": "rgba(168,85,247,0.06)",
        "border": "rgba(168,85,247,0.35)",
    },
    "messaging": {
        "label": "Messaging",
        "color": "#06b6d4",
        "bg": "rgba(6,182,212,0.06)",
        "border": "rgba(6,182,212,0.35)",
    },
    "other": {
        "label": "Other Resources",
        "color": "#6b7280",
        "bg": "rgba(107,114,128,0.06)",
        "border": "rgba(107,114,128,0.35)",
    },
}

# Inferred connections between service types
_INFERRED_CONNECTIONS = [
    ("apigateway", "lambda", "HTTP invoke"),
    ("lambda", "dynamodb", "Read/Write"),
    ("lambda", "s3", "Read/Write"),
    ("lambda", "sqs", "Send/Receive"),
    ("lambda", "sns", "Publish"),
    ("events", "lambda", "Trigger"),
    ("apigateway", "s3", "Static hosting"),
]

# IaC type badge colors
_IAC_BADGE_COLORS = {
    "terraform": ("#7c3aed", "rgba(124,58,237,0.15)"),
    "pulumi": ("#3b82f6", "rgba(59,130,246,0.15)"),
    "cloudformation": ("#6b7280", "rgba(107,114,128,0.15)"),
    "serverless": ("#f97316", "rgba(249,115,22,0.15)"),
    "cdk": ("#22c55e", "rgba(34,197,94,0.15)"),
}

# Experimental banner shown on all per-repo architecture views
_EXPERIMENTAL_BANNER = """
  <div style="background:rgba(245,158,11,0.06); border:1px solid rgba(245,158,11,0.2);
              border-radius:8px; padding:10px 16px; margin-bottom:16px;
              display:flex; align-items:flex-start; gap:10px; font-size:12px; color:#d97706;">
    <span style="font-size:14px; flex-shrink:0; margin-top:1px;">&#9888;</span>
    <span style="color:#94a3b8; line-height:1.5;">
      <strong style="color:#d97706;">Experimental.</strong>
      Generated from live compliance data &mdash; resource counts and compliance
      status are accurate. However, service groupings and relationships are
      inferred automatically and may not reflect the actual architecture.
    </span>
  </div>"""

# Per-repo page CSS (breadcrumb, header, nav-links — not in ZONE_CARD_CSS)
_REPO_PAGE_CSS = """
  .breadcrumb {
    font-size: 12px; color: #6b7280; margin-bottom: 16px;
  }
  .breadcrumb a { color: #3b82f6; text-decoration: none; }
  .breadcrumb a:hover { text-decoration: underline; }
  .repo-header {
    display: flex; align-items: center; gap: 12px; margin-bottom: 6px;
  }
  .repo-header h1 {
    font-size: 18px; font-weight: 700; letter-spacing: 1px; color: #f1f5f9;
  }
  .nav-links {
    display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap;
  }
  .nav-link {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: 600;
    text-decoration: none; border: 1px solid rgba(255,255,255,0.08);
    color: #6b7280; transition: all 0.15s;
  }
  .nav-link:hover { color: #e2e8f0; border-color: #374151; background: rgba(255,255,255,0.03); }
  .nav-link.active { color: #3b82f6; border-color: rgba(59,130,246,0.3); background: rgba(59,130,246,0.06); }
"""

# Shared page style block
_PAGE_CSS = f"""
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    background: #050508; color: #e2e8f0; line-height: 1.5; min-height: 100vh;
  }}
  ::selection {{ background: rgba(59,130,246,0.3); }}
  {NAV_CSS}
  {ZONE_CARD_CSS}
  {_REPO_PAGE_CSS}
"""


def _iac_badge_html(iac_type):
    iac_color, iac_bg = _IAC_BADGE_COLORS.get(iac_type, ("#6b7280", "rgba(107,114,128,0.15)"))
    return (
        f'<span style="display:inline-block; padding:3px 10px; border-radius:4px; '
        f"font-size:11px; font-weight:600; letter-spacing:0.04em; "
        f'color:{iac_color}; background:{iac_bg}; border:1px solid {iac_color}44;">'
        f"{_e(iac_type)}</span>"
    )


def _get_service_types_present(resources):
    """Return sorted list of (service_key, meta, count) tuples present in the repo."""
    svc_counts = {}
    for r in resources:
        svc = _service_from_type(r["resource_type"])
        svc_counts[svc] = svc_counts.get(svc, 0) + 1
    result = []
    for svc, count in sorted(svc_counts.items(), key=lambda x: -x[1]):
        meta = _SERVICE_TYPE_MAP.get(svc, {"label": svc.upper(), "icon": "ecs", "zone": "other"})
        result.append((svc, meta, count))
    return result


# ── Landing Page ──


def render_repo_architecture_landing(slug, user_email=""):
    """Render the per-repo architecture landing page with two cards."""
    info = get_repo_info(slug)
    resources = get_repo_resources(slug)

    if not info:
        return _render_error(slug, "Repository not found in IaC registry.", user_email)

    iac_type = info.get("iac_type", "unknown")
    base = f"/repos/{urllib.parse.quote(slug, safe='')}"

    # Discover service types for the service index
    svc_types = _get_service_types_present(resources)

    # Service type link chips
    svc_links = ""
    for svc_key, meta, count in svc_types:
        zone = meta.get("zone", "other")
        zone_cfg = _ZONE_CONFIG.get(zone, _ZONE_CONFIG["other"])
        svc_links += (
            f'<a href="{base}/architecture/custom/{urllib.parse.quote(svc_key, safe="")}" '
            f'class="card-link" style="font-size:11px;padding:5px 12px;'
            f'color:{zone_cfg["color"]};border-color:{zone_cfg["border"]};">'
            f"{_e(meta['label'])} ({count})</a>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(slug)} Architecture // __BRAND__</title>
<style>
  {_PAGE_CSS}
  .card-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  @media (max-width: 700px) {{ .card-grid {{ grid-template-columns: 1fr; }} }}
  .card {{
    background: #0a0a10; border: 1px solid #1e1e2e; border-radius: 10px;
    padding: 24px; transition: all 0.2s; display: flex; flex-direction: column;
  }}
  .card:hover {{
    border-color: #2a2a3a; background: #0e0e16;
    transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.3);
  }}
  .card-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }}
  .card-letter {{
    width: 32px; height: 32px; border-radius: 8px; display: flex;
    align-items: center; justify-content: center; font-weight: 700;
    font-size: 16px; flex-shrink: 0;
  }}
  .card-title {{ font-size: 15px; font-weight: 600; }}
  .card-tech {{ font-size: 10px; color: #4b5563; margin-top: 2px; }}
  .card-desc {{
    font-size: 12px; color: #94a3b8; line-height: 1.6; flex: 1;
    margin-bottom: 16px;
  }}
  .card-link {{
    display: inline-flex; align-items: center; gap: 6px;
    color: #3b82f6; text-decoration: none; font-size: 13px; font-weight: 600;
    padding: 8px 16px; border: 1px solid rgba(59,130,246,0.2); border-radius: 6px;
    background: rgba(59,130,246,0.06); transition: all 0.15s; align-self: flex-start;
  }}
  .card-link:hover {{
    background: rgba(59,130,246,0.12); border-color: rgba(59,130,246,0.4);
  }}
  .footer {{
    margin-top: 32px; text-align: center; font-size: 10px; color: #374151;
    padding-bottom: 16px;
  }}
  .footer a {{ color: #3b82f6; text-decoration: none; }}
  .footer a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
{render_nav("repos", user_email)}
<div class="arch-container">
  <div class="breadcrumb">
    <a href="/repos">Repositories</a> &rsaquo;
    <a href="{base}">{_e(slug)}</a> &rsaquo;
    Architecture
  </div>

  {_EXPERIMENTAL_BANNER}

  <div class="repo-header">
    <h1>{_e(slug.upper())}</h1>
    {_iac_badge_html(iac_type)}
  </div>
  <p style="color:#6b7280; font-size:13px; margin-bottom:24px;">
    Architecture visualizations for this repository's AWS resources.
  </p>

  <div class="card-grid">
    <!-- Card A: Custom Containment Diagram -->
    <div class="card">
      <div class="card-header">
        <div class="card-letter" style="background:rgba(34,197,94,0.12); color:#22c55e;">A</div>
        <div>
          <div class="card-title">Custom Diagram</div>
          <div class="card-tech">Containment diagram &mdash; services by zone</div>
        </div>
      </div>
      <div class="card-desc">
        Static containment diagram grouping AWS services by zone
        (Compute, API, Storage, Security). Each service card shows
        resource count, compliance status, and cost. Click any service
        for the full resource breakdown.
      </div>
      <a href="{base}/architecture/custom" class="card-link">View Custom Diagram &rarr;</a>
    </div>

    <!-- Card B: Service Index + Interactions -->
    <div class="card">
      <div class="card-header">
        <div class="card-letter" style="background:rgba(168,85,247,0.12); color:#a855f7;">B</div>
        <div>
          <div class="card-title">Service Index</div>
          <div class="card-tech">Per-service detail &rarr; interactions</div>
        </div>
      </div>
      <div class="card-desc">
        Per-service deep dive with resource counts and compliance status.
        The interactive D3 graph shows inferred service-to-service connections
        with typed edges (HTTP, data, event).
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;">
        {svc_links}
      </div>
      <a href="{base}/architecture/interactions" class="card-link" style="margin-top:auto;">
        All Services &mdash; Interactive D3 Graph &rarr;</a>
    </div>
  </div>

  <div class="footer">
    Auto-generated from compliance report &middot;
    <a href="{base}/tree">Tree</a> &middot;
    <a href="{base}/network">Network</a> &middot;
    <a href="{base}">Detail</a>
  </div>
</div>
</body>
</html>"""


# ── Containment Diagram (uses canonical rendering) ──


def render_repo_architecture(slug, user_email=""):
    """Render the per-repo containment diagram using canonical zone/card rendering.

    Transforms get_repo_tree() data into (mod_addr, meta, stats) tuples
    and delegates to arch_custom._render_zone_box() for the actual HTML.
    """
    info = get_repo_info(slug)
    if not info:
        return _render_error(slug, "Repository not found in IaC registry.", user_email)

    tree = get_repo_tree(slug)
    if not tree:
        return _render_error(slug, "No resources found for this repository.", user_email)

    iac_type = info.get("iac_type", "unknown")
    base = f"/repos/{urllib.parse.quote(slug, safe='')}"

    # Build a lookup: service label -> service key
    resources = get_repo_resources(slug)
    svc_key_from_label = {}
    for r in resources:
        svc = _service_from_type(r["resource_type"])
        meta = _SERVICE_TYPE_MAP.get(svc, {"label": svc.upper(), "icon": "ecs", "zone": "other"})
        svc_key_from_label[meta["label"]] = svc

    # Transform tree entries into (mod_addr, meta, stats) tuples grouped by zone
    modules_by_zone = {}
    for entry in tree:
        label = entry["module"]
        svc_key = svc_key_from_label.get(label, label.lower().replace(" ", "_"))
        arch = entry.get("arch", {})
        zone = arch.get("zone", "other")
        svc_meta = _SERVICE_TYPE_MAP.get(svc_key, {"label": label, "icon": "ecs", "zone": zone})

        # Build resource names preview for description
        res_names = [r.get("name") or r.get("id", "") for r in entry.get("resources", [])]
        shown = [n for n in res_names[:3] if n]
        desc = ", ".join(shown)
        if len(res_names) > 3:
            desc += f" +{len(res_names) - 3} more"

        meta = {
            "name": label,
            "icon": svc_meta.get("icon", "ecs"),
            "desc": desc,
        }
        modules_by_zone.setdefault(zone, []).append((svc_key, meta, entry["stats"]))

    # Aggregate stats
    total_resources = sum(e["stats"]["total"] for e in tree)
    total_compliant = sum(e["stats"]["compliant"] for e in tree)
    total_nc = total_resources - total_compliant
    overall_pct = round(100 * total_compliant / total_resources) if total_resources else 100
    pct_color = _rate_color(overall_pct)
    total_cost = sum(e["stats"].get("cost", 0) for e in tree)
    service_count = len(tree)

    def _fmt_cost(cost):
        if cost == 0:
            return "$0"
        if cost < 1:
            return f"${cost:.2f}"
        return f"${cost:,.0f}"

    # Link function for card click targets
    def card_link_fn(mod_addr):
        return f"{base}/architecture/custom/{urllib.parse.quote(mod_addr, safe='')}"

    # Inferred connections for the data flow legend
    all_svc_keys = set()
    for entries in modules_by_zone.values():
        for svc_key, *_ in entries:
            all_svc_keys.add(svc_key)

    active_flows = []
    for src, tgt, label in _INFERRED_CONNECTIONS:
        if src in all_svc_keys and tgt in all_svc_keys:
            src_meta = _SERVICE_TYPE_MAP.get(src, {"label": src.upper()})
            tgt_meta = _SERVICE_TYPE_MAP.get(tgt, {"label": tgt.upper()})
            active_flows.append((src_meta["label"], tgt_meta["label"], label))

    # Build page
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(slug)} Custom Diagram // __BRAND__</title>
<style>
  {_PAGE_CSS}
</style>
</head>
<body>
{render_nav("repos", user_email)}
<div class="arch-container">
  <div class="breadcrumb">
    <a href="/repos">Repositories</a> &rsaquo;
    <a href="{base}">{_e(slug)}</a> &rsaquo;
    <a href="{base}/architecture">Architecture</a> &rsaquo;
    Custom Diagram
  </div>

  {_EXPERIMENTAL_BANNER}

  <div class="repo-header">
    <h1>{_esc(slug.upper())}</h1>
    {_iac_badge_html(iac_type)}
  </div>

  <div class="nav-links">
    <a href="{base}" class="nav-link">Detail</a>
    <a href="{base}/architecture" class="nav-link">Architecture</a>
    <a href="{base}/architecture/custom" class="nav-link active">Custom Diagram</a>
    <a href="{base}/architecture/interactions" class="nav-link">Interactions</a>
  </div>

  <div class="stats-strip">
    <div class="stat-pill"><div><div class="stat-label">Services</div><div class="stat-value" style="color:#3b82f6;">{service_count}</div></div></div>
    <div class="stat-pill"><div><div class="stat-label">Resources</div><div class="stat-value" style="color:#e2e8f0;">{total_resources}</div></div></div>
    <div class="stat-pill"><div><div class="stat-label">Compliant</div><div class="stat-value" style="color:#22c55e;">{total_compliant}</div></div></div>
    <div class="stat-pill"><div><div class="stat-label">Non-Compliant</div><div class="stat-value" style="color:{"#ef4444" if total_nc else "#22c55e"};">{total_nc}</div></div></div>
    <div class="stat-pill"><div><div class="stat-label">Compliance</div><div class="stat-value" style="color:{pct_color};">{overall_pct}%</div></div></div>
    <div class="stat-pill"><div><div class="stat-label">Est. Monthly</div><div class="stat-value" style="color:#f59e0b;">{_fmt_cost(total_cost)}</div></div></div>
  </div>
"""

    # Render zones using the canonical zone/card rendering from arch_custom.py
    zone_order = ["api", "compute", "storage", "security", "monitoring", "messaging", "other"]
    for zone_key in zone_order:
        modules = modules_by_zone.get(zone_key)
        if modules:
            page += _render_zone_box(zone_key, modules, zone_config=_ZONE_CONFIG, card_link_fn=card_link_fn)

    # Data flow legend (inferred connections)
    if active_flows:
        flow_html = ""
        for src_label, tgt_label, label in active_flows:
            flow_html += (
                f'    <div class="legend-item" style="gap:4px;">'
                f'<span style="color:#e2e8f0;">{_esc(src_label)}</span>'
                f' <span style="color:#374151;">&rarr;</span> '
                f'<span style="color:#e2e8f0;">{_esc(tgt_label)}</span>'
                f' <span style="color:#4b5563;">({_esc(label)})</span>'
                f"</div>\n"
            )
        page += f"""
  <div class="legend" style="margin-bottom:8px;">
    <span class="legend-title">Inferred Data Flow</span>
{flow_html}  </div>"""

    # Compliance legend
    page += """
  <div class="legend">
    <span class="legend-title">Compliance</span>
    <div class="legend-item"><span class="legend-dot" style="background:#22c55e; box-shadow:0 0 4px #22c55e;"></span> Fully compliant</div>
    <div class="legend-item"><span class="legend-dot" style="background:#f59e0b; box-shadow:0 0 4px #f59e0b;"></span> Partially compliant</div>
    <div class="legend-item"><span class="legend-dot" style="background:#ef4444; box-shadow:0 0 4px #ef4444;"></span> Non-compliant</div>
    <div class="legend-item"><span class="legend-dot" style="background:#6b7280;"></span> No data</div>
  </div>

  <div style="margin-top:16px; text-align:center; font-size:10px; color:#374151;">
    Auto-generated from compliance report &middot;
    Click any service card for resource detail &middot;
    <a href="/repos" style="color:#3b82f6; text-decoration:none;">All repos</a>
  </div>
</div>
</body>
</html>"""

    return page


# ── Per-Service-Type Detail ──


def render_repo_architecture_service(slug, service_type, user_email=""):
    """Render a detail page for a single service type within a repo."""
    info = get_repo_info(slug)
    resources = get_repo_resources(slug)

    if not info:
        return _render_error(slug, "Repository not found in IaC registry.", user_email)

    # Filter to just this service type. EC2 instances are grouped on the overview by
    # Service tag (service_type "ec2:instance::<service-tag>"), so a click drills into
    # that functional group — all hosts sharing the Service tag (its regional fleet).
    if service_type.startswith("ec2:instance::"):
        target = service_type.split("::", 1)[1]
        svc_resources = [
            r
            for r in resources
            if r["resource_type"] == "ec2:instance"
            and ((r.get("tags") or {}).get("Service") or r.get("name") or "instance") == target
        ]
    else:
        svc_resources = [r for r in resources if _service_from_type(r["resource_type"]) == service_type]

    if not svc_resources:
        return _render_error(
            slug,
            f"No resources of type '{service_type}' found for this repository.",
            user_email,
        )

    iac_type = info.get("iac_type", "unknown")
    if service_type.startswith("ec2:instance::"):
        ec2_meta = _SERVICE_TYPE_MAP.get("ec2", {"label": "EC2", "icon": "ec2", "zone": "compute"})
        meta = {
            "label": service_type.split("::", 1)[1],
            "icon": ec2_meta.get("icon", "ec2"),
            "zone": ec2_meta.get("zone", "compute"),
        }
    else:
        meta = _SERVICE_TYPE_MAP.get(service_type, {"label": service_type.upper(), "icon": "ecs", "zone": "other"})
    zone = meta.get("zone", "other")
    zone_cfg = _ZONE_CONFIG.get(zone, _ZONE_CONFIG["other"])
    color = zone_cfg["color"]

    base = f"/repos/{urllib.parse.quote(slug, safe='')}"

    # Stats for this service type
    total = len(svc_resources)
    compliant = sum(1 for r in svc_resources if r["compliant"])
    nc = total - compliant
    pct = round(100 * compliant / total) if total else 100
    pct_color = _rate_color(pct)

    # Cost
    costs = [r.get("estimated_monthly_cost") or 0 for r in svc_resources]
    total_cost = round(sum(costs), 2)

    # Find inferred connections to other service types present in this repo
    all_svc_keys = {_service_from_type(r["resource_type"]) for r in resources}
    connections = []
    for src, tgt, label in _INFERRED_CONNECTIONS:
        if src == service_type and tgt in all_svc_keys:
            tgt_meta = _SERVICE_TYPE_MAP.get(tgt, {"label": tgt.upper()})
            connections.append(("&rarr;", tgt, tgt_meta["label"], label))
        elif tgt == service_type and src in all_svc_keys:
            src_meta = _SERVICE_TYPE_MAP.get(src, {"label": src.upper()})
            connections.append(("&larr;", src, src_meta["label"], label))

    conn_html = ""
    if connections:
        conn_html = """
  <div style="margin-top:24px;">
    <h2 style="font-size:13px; font-weight:700; color:#94a3b8; letter-spacing:1px; margin-bottom:10px;">
      INFERRED CONNECTIONS
    </h2>
    <div style="display:flex; flex-wrap:wrap; gap:8px;">"""
        for arrow, other_svc, other_label, label in connections:
            conn_html += (
                f'<a href="{base}/architecture/custom/{urllib.parse.quote(other_svc, safe="")}" '
                f'style="display:inline-flex; align-items:center; gap:6px; padding:8px 14px; '
                f"background:#0a0a10; border:1px solid #1e1e2e; border-radius:8px; "
                f'text-decoration:none; font-size:12px; transition:all 0.15s;">'
                f'<span style="color:#6b7280;">{arrow}</span>'
                f'<span style="color:#e2e8f0; font-weight:600;">{_e(other_label)}</span>'
                f'<span style="color:#4b5563; font-size:10px;">{_e(label)}</span>'
                f"</a>"
            )
        conn_html += """
    </div>
  </div>"""

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(meta["label"])} &mdash; {_e(slug)} // __BRAND__</title>
<style>
  {_PAGE_CSS}
  .res-table {{
    width: 100%; border-collapse: collapse; font-size: 13px;
  }}
  .res-table th {{
    text-align: left; padding: 8px 12px; background: #0d0d14; color: #94a3b8;
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px;
    border-bottom: 1px solid #1e1e2e;
  }}
  .res-table td {{
    padding: 6px 12px; border-bottom: 1px solid #111118;
  }}
  .res-table tr:hover {{ background: #0d0d14; }}
</style>
</head>
<body>
{render_nav("repos", user_email)}
<div class="arch-container">
  <div class="breadcrumb">
    <a href="/repos">Repositories</a> &rsaquo;
    <a href="{base}">{_e(slug)}</a> &rsaquo;
    <a href="{base}/architecture">Architecture</a> &rsaquo;
    <a href="{base}/architecture/custom">Custom Diagram</a> &rsaquo;
    {_e(meta["label"])}
  </div>

  {_EXPERIMENTAL_BANNER}

  <div class="repo-header">
    <h1 style="color:{color};">{_e(meta["label"])}</h1>
    {_iac_badge_html(iac_type)}
  </div>

  <div class="nav-links">
    <a href="{base}/architecture/custom" class="nav-link">&larr; Custom Diagram</a>
    <a href="{base}/architecture/interactions/{urllib.parse.quote(service_type, safe="")}" class="nav-link">Interactions &rarr;</a>
  </div>

  <div class="stats-strip">
    <div class="stat-pill"><div><div class="stat-label">Resources</div><div class="stat-value" style="color:#e2e8f0;">{total}</div></div></div>
    <div class="stat-pill"><div><div class="stat-label">Compliant</div><div class="stat-value" style="color:#22c55e;">{compliant}</div></div></div>
    <div class="stat-pill"><div><div class="stat-label">Non-Compliant</div><div class="stat-value" style="color:{"#ef4444" if nc else "#22c55e"};">{nc}</div></div></div>
    <div class="stat-pill"><div><div class="stat-label">Compliance</div><div class="stat-value" style="color:{pct_color};">{pct}%</div></div></div>
    <div class="stat-pill"><div><div class="stat-label">Est. Cost</div><div class="stat-value" style="color:#f59e0b;">${total_cost}</div></div></div>
  </div>

  <h2 style="font-size:13px; font-weight:700; color:#94a3b8; letter-spacing:1px; margin-bottom:10px;">
    RESOURCES
  </h2>
  <table class="res-table">
    <thead>
      <tr>
        <th>Status</th>
        <th>Name</th>
        <th>Type</th>
        <th>Region</th>
        <th>Cost</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
"""

    for r in sorted(svc_resources, key=lambda x: (x["compliant"], x["name"])):
        dot_color = "#22c55e" if r["compliant"] else "#ef4444"
        dot_title = "Compliant" if r["compliant"] else "Non-compliant"
        name = r["name"] or r["arn"].split(":")[-1].split("/")[-1]
        console = _safe_url(r.get("console_url", ""))
        cost = r.get("estimated_monthly_cost")
        cost_str = f"${cost:.2f}" if cost is not None else "\u2014"

        actions = ""
        if console:
            actions += (
                f'<a href="{_e(console)}" target="_blank" '
                f'style="color:#3b82f6; text-decoration:none; font-size:11px;">AWS</a>'
            )
        if not r["compliant"] and r["arn"]:
            cure_url = f"/cure?arn={urllib.parse.quote(r['arn'], safe='')}"
            if actions:
                actions += " &middot; "
            actions += f'<a href="{_e(cure_url)}" style="color:#f59e0b; text-decoration:none; font-size:11px;">CURE</a>'

        page += f"""
      <tr>
        <td><span style="display:inline-block; width:8px; height:8px; border-radius:50%;
                         background:{dot_color}; box-shadow:0 0 4px {dot_color};"
                   title="{dot_title}"></span></td>
        <td style="color:#e2e8f0; font-size:12px; max-width:300px; overflow:hidden;
                   text-overflow:ellipsis; white-space:nowrap;" title="{_e(r["arn"])}">{_e(name)}</td>
        <td style="color:#60a5fa; font-size:12px;">{_e(r["resource_type"])}</td>
        <td style="color:#6b7280; font-size:11px;">{_e(r["region"])}</td>
        <td style="color:#f59e0b; font-size:12px;">{cost_str}</td>
        <td>{actions}</td>
      </tr>"""

    page += f"""
    </tbody>
  </table>

  {conn_html}

  <div style="margin-top:24px; text-align:center; font-size:10px; color:#374141;">
    Auto-generated from compliance report &middot;
    <a href="{base}/architecture/custom" style="color:#3b82f6; text-decoration:none;">&larr; Custom Diagram</a>
  </div>
</div>
</body>
</html>"""

    return page


# ── Error page ──


def _render_error(slug, error, user_email=""):
    """Render an error page."""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Architecture Error // __BRAND__</title>
<style>
  body {{ background:#050508; color:#e2e8f0;
         font-family:'SFMono-Regular',Consolas,monospace;
         display:flex; align-items:center; justify-content:center; min-height:100vh; }}
  .err {{ background:#111118; border:1px solid #1e1e2e; border-radius:12px;
          padding:32px; max-width:500px; text-align:center; }}
  .err h1 {{ color:#ef4444; font-size:18px; margin-bottom:12px; }}
  .err p {{ color:#6b7280; font-size:13px; margin-bottom:16px; }}
  .err a {{ color:#3b82f6; text-decoration:none; font-size:13px; }}
</style></head><body>
<div class="err">
  <h1>Architecture Unavailable</h1>
  <p>{_e(error)}</p>
  <a href="/repos">&larr; Repositories</a>
</div>
</body></html>"""

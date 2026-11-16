"""
Service-Level Architecture Diagram — per-module containment view with cost overlay.

Renders a detailed architecture diagram for a single Terraform module, showing
its internal sub-module structure, per-resource costs, connections to other
services, and a cost insight panel explaining how infrastructure choices
relate to spend.

Accessed at ``/architecture/custom/<module_address>``.
"""

import html
import urllib.parse

from arch_shared import (
    COST_TIERS,
    EDGE_STYLES,
    ZONE_COLORS,
    get_connection_pair,
    get_connections_for,
    get_cost_tier,
    get_module_meta,
    get_submodule_display_name,
)
from cost_display import (
    HINT_CSS,
    cost_tier_color,
    fmt_cost,
    fmt_cost_est,
    sum_costs,
)
from data_reports import get_compliance_report
from data_terraform import get_tf_module_tree
from views_base import NAV_CSS, _console_url, render_nav_arch


def _e(text):
    """HTML-escape a string."""
    return html.escape(str(text)) if text else ""


# Map TF resource types → Cost Explorer service names for billed spend lookup.
# CE service names must match the keys in report["service_costs"].
_TF_TYPE_TO_CE_SERVICE = {
    "aws_lambda_function": "AWS Lambda",
    "aws_dynamodb_table": "Amazon DynamoDB",
    "aws_ecs_service": "Amazon Elastic Container Service",
    "aws_ecs_cluster": "Amazon Elastic Container Service",
    "aws_ecs_task_definition": "Amazon Elastic Container Service",
    "aws_db_instance": "Amazon Relational Database Service",
    "aws_rds_cluster": "Amazon Relational Database Service",
    "aws_s3_bucket": "Amazon Simple Storage Service",
    "aws_elasticache_cluster": "Amazon ElastiCache",
    "aws_elasticache_replication_group": "Amazon ElastiCache",
    "aws_lb": "Amazon Elastic Load Balancing",
    "aws_alb": "Amazon Elastic Load Balancing",
    "aws_cloudwatch_log_group": "AmazonCloudWatch",
    "aws_cloudwatch_metric_alarm": "AmazonCloudWatch",
    "aws_secretsmanager_secret": "AWS Secrets Manager",
    "aws_efs_file_system": "Amazon Elastic File System",
    "aws_ecr_repository": "Amazon EC2 Container Registry (ECR)",
    "aws_route53_record": "Amazon Route 53",
    "aws_route53_zone": "Amazon Route 53",
    "aws_acm_certificate": "AWS Certificate Manager",
    "aws_instance": "Amazon Elastic Compute Cloud - Compute",
    "aws_ebs_volume": "EC2 - Other",
    "aws_eip": "EC2 - Other",
    "aws_nat_gateway": "Amazon Virtual Private Cloud",
    "aws_vpc": "Amazon Virtual Private Cloud",
    "aws_kms_key": "AWS Key Management Service",
    "aws_apigatewayv2_api": "Amazon API Gateway",
    "aws_api_gateway_rest_api": "Amazon API Gateway",
}


def _compliance_dot_color(resources):
    """Return (css_color, pct_label) for a list of resources."""
    total = len(resources)
    nc = sum(1 for r in resources if not r.get("compliant", True))
    if total == 0:
        return "#6b7280", "N/A"
    pct = round(100 * (total - nc) / total)
    if nc == 0:
        return "#22c55e", f"{pct}%"
    if pct >= 70:
        return "#f59e0b", f"{pct}%"
    return "#ef4444", f"{pct}%"


# ─────────────────────────────────────────────────────────────────────────────
# Main render function
# ─────────────────────────────────────────────────────────────────────────────


def render_architecture_service(module_address, user_email=""):
    """Render a service-level architecture diagram for a single TF module.

    Shows internal sub-module structure, per-resource costs, service
    connections, and a cost insight panel.
    """
    report = get_compliance_report()
    tree = get_tf_module_tree(report)

    # Find the module in the tree
    module_data = None
    for mod in tree:
        if mod["module"] == module_address:
            module_data = mod
            break

    if not module_data:
        return _render_not_found(module_address, user_email)

    meta = get_module_meta().get(module_address, {})
    stats = module_data.get("stats", {})
    children = module_data.get("children", {})
    resources = module_data.get("resources", [])
    file_path = module_data.get("file_path", "")

    name = meta.get("name", module_address.replace("module.", ""))
    zone = meta.get("zone", "infra")

    total = stats.get("total", 0)
    compliant = stats.get("compliant", 0)
    nc = stats.get("non_compliant", 0)
    cost = stats.get("cost", 0)
    cost_unknown = stats.get("cost_unknown", 0)

    # Find connections involving this service
    short_name = module_address.replace("module.", "")
    inbound, outbound = get_connections_for(short_name)

    # Separate top-level resources (not in any child) from child resources
    child_arns = set()
    for child_resources in children.values():
        for r in child_resources:
            child_arns.add(id(r))
    top_level_resources = [r for r in resources if id(r) not in child_arns]

    zone_color = ZONE_COLORS.get(zone, "#6b7280")

    # Build the page
    page = _page_open(name, module_address, user_email, meta, stats, zone, zone_color, file_path)
    page += _render_summary_strip(total, compliant, nc, cost, cost_unknown, len(children))
    page += _render_connections(inbound, outbound, module_address)
    page += _render_context_map_link(short_name)
    service_costs = report.get("service_costs", {})

    # Proportional cost attribution: compute this module's share of each CE service bill
    # based on estimated cost ratios across all modules.
    ce_total_est = {}  # CE service → total estimated cost across ALL modules
    ce_module_est = {}  # CE service → estimated cost in THIS module only
    ce_module_count = {}  # CE service → count of modules using it
    for mod in tree:
        mod_ce_est = {}
        for r in mod.get("resources", []):
            ce = _TF_TYPE_TO_CE_SERVICE.get(r.get("type", ""))
            if ce:
                mod_ce_est[ce] = mod_ce_est.get(ce, 0) + (r.get("estimated_monthly_cost") or 0)
        for ce, est in mod_ce_est.items():
            ce_total_est[ce] = ce_total_est.get(ce, 0) + est
            ce_module_count[ce] = ce_module_count.get(ce, 0) + 1
        if mod["module"] == module_address:
            ce_module_est = mod_ce_est

    ce_proportions = {}
    for ce_svc, mod_est in ce_module_est.items():
        actual = service_costs.get(ce_svc, 0)
        if actual <= 0:
            continue
        total_est = ce_total_est.get(ce_svc, 0)
        if total_est > 0:
            ce_proportions[ce_svc] = (mod_est / total_est) * actual
        elif ce_module_count.get(ce_svc, 1) > 0:
            ce_proportions[ce_svc] = actual / ce_module_count[ce_svc]

    page += _render_internal_architecture(children, top_level_resources, zone_color, service_costs, ce_proportions)
    page += _render_cost_insight(module_address, meta, stats, children, resources, service_costs)
    page += _render_footer(module_address, name, file_path)
    page += _page_close()
    return page


# ─────────────────────────────────────────────────────────────────────────────
# HTML generation helpers
# ─────────────────────────────────────────────────────────────────────────────


def _page_open(name, module_address, user_email, meta, stats, zone, zone_color, file_path=""):
    """Opening HTML with styles and service header."""
    total = stats.get("total", 0)

    host = meta.get("host", "")
    auth = meta.get("auth", "")
    desc = meta.get("desc", "")
    svc_type = meta.get("type", "")

    zone_labels = {
        "public": "Public Subnets",
        "apps": "Applications",
        "identity": "Identity Stack",
        "serverless": "AWS Serverless",
        "security": "Security & Monitoring",
        "infra": "Infrastructure",
    }
    zone_label = zone_labels.get(zone, zone.title())

    # Visit link
    visit_html = ""
    if host and not host.startswith("*"):
        visit_html = (
            f'<a href="https://{_e(host)}" target="_blank" rel="noopener" '
            f'style="color:#3b82f6;text-decoration:none;font-size:12px;padding:4px 10px;'
            f'border:1px solid rgba(59,130,246,0.2);border-radius:4px;background:rgba(59,130,246,0.06);">'
            f"Visit &rarr;</a>"
        )

    nav = render_nav_arch(
        "custom",
        user_email,
        meta=f"{name} &middot; {total} resources",
        sub_breadcrumb=name,
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(name)} Architecture // __BRAND__</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    background: #050508; color: #e2e8f0; line-height: 1.5;
    min-height: 100vh;
  }}
  ::selection {{ background: rgba(59,130,246,0.3); }}

  {NAV_CSS}

  .arch-container {{
    max-width: 1200px; margin: 0 auto; padding: 24px 24px 64px;
  }}

  /* ── Summary strip ── */
  .stats-strip {{
    display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap;
  }}
  .stat-pill {{
    background: #0d0d14; border: 1px solid #1e1e2e; border-radius: 6px;
    padding: 8px 16px; display: flex; align-items: center; gap: 8px;
    font-size: 12px;
  }}
  .stat-pill .stat-label {{ color: #6b7280; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; }}
  .stat-pill .stat-value {{ font-weight: 700; font-size: 16px; }}

  /* ── Service header ── */
  .svc-header {{
    background: #0a0a10; border: 1px solid #1e1e2e; border-radius: 10px;
    padding: 20px 24px; margin-bottom: 16px;
  }}
  .svc-header-top {{
    display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  }}
  .svc-header h1 {{ font-size: 22px; font-weight: 700; }}
  .zone-badge {{
    font-size: 10px; padding: 2px 8px; border-radius: 3px;
    font-weight: 600; letter-spacing: 0.04em;
  }}
  .svc-meta {{
    display: flex; gap: 16px; margin-top: 10px; flex-wrap: wrap;
    font-size: 12px; color: #6b7280;
  }}
  .svc-meta span {{ display: flex; align-items: center; gap: 4px; }}

  /* ── Connections ── */
  .conn-section {{
    background: #0a0a10; border: 1px solid #1e1e2e; border-radius: 10px;
    padding: 16px 20px; margin-bottom: 16px;
  }}
  .conn-section h2 {{
    font-size: 11px; color: #6b7280; text-transform: uppercase;
    letter-spacing: 0.1em; margin-bottom: 12px;
  }}
  .conn-grid {{ display: flex; gap: 10px; flex-wrap: wrap; }}
  .conn-pill {{
    background: #0d0d14; border: 1px solid #1e1e2e; border-radius: 6px;
    padding: 8px 14px; font-size: 12px; display: flex; align-items: center;
    gap: 8px; text-decoration: none; color: #e2e8f0;
    transition: all 0.15s;
  }}
  .conn-pill:hover {{
    border-color: #2a2a3a; background: #0e0e16; transform: translateY(-1px);
  }}
  .conn-arrow {{ color: #4b5563; }}

  /* ── Sub-module boxes ── */
  .submod-section {{
    margin-bottom: 16px;
  }}
  .submod-section h2 {{
    font-size: 11px; color: #6b7280; text-transform: uppercase;
    letter-spacing: 0.1em; margin-bottom: 12px;
  }}
  .submod-grid {{
    display: flex; flex-direction: column; gap: 10px;
  }}
  .submod-box {{
    background: #0a0a10; border: 1px solid #1e1e2e; border-radius: 10px;
    border-left: 3px solid; overflow: hidden; transition: all 0.15s;
  }}
  .submod-box:hover {{ border-color: #2a2a3a; }}
  .submod-header {{
    padding: 12px 16px; display: flex; align-items: center; gap: 12px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    cursor: pointer;
  }}
  .submod-header:hover {{ background: rgba(255,255,255,0.02); }}
  .submod-name {{ font-size: 14px; font-weight: 600; }}
  .submod-meta {{ margin-left: auto; display: flex; gap: 10px; align-items: center; font-size: 11px; color: #6b7280; }}
  .submod-body {{ padding: 8px 16px; }}

  /* ── Resource rows ── */
  /* Default: dot | name | type | estimate | console | toggle */
  .res-row {{
    display: grid; grid-template-columns: 6px 1fr 120px 70px 36px 20px;
    gap: 10px; align-items: center; padding: 6px 0;
    border-bottom: 1px solid rgba(255,255,255,0.02);
    font-size: 12px;
  }}
  .res-row:last-child {{ border-bottom: none; }}
  .res-hdr div {{ color: #4b5563; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; }}
  .res-dot {{ width: 6px; height: 6px; border-radius: 50%; }}
  .res-name {{ color: #e2e8f0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .res-type {{ color: #4b5563; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .res-cost {{ color: #6b7280; font-size: 11px; text-align: right; }}
  .res-link {{
    color: #4b5563; text-decoration: none; font-size: 10px;
    padding: 2px 6px; border: 1px solid rgba(255,255,255,0.06);
    border-radius: 3px; transition: all 0.15s; text-align: center;
  }}
  .res-link:hover {{ color: #3b82f6; border-color: rgba(59,130,246,0.3); }}
  .res-service, .res-acct, .billing-spacer {{ display: none; }}
  .res-service {{ color: #22c55e; font-size: 11px; text-align: right; }}
  .res-acct {{ color: #f59e0b; font-size: 11px; text-align: right; opacity: 0.6; }}
  .cost-meta {{ display: none; }}
  .billing-toggle {{ text-align: right; cursor: pointer; color: #374131; font-size: 11px; transition: color 0.15s; }}
  .billing-toggle:hover {{ color: #22c55e; }}
  /* Expanded: dot | name | type | estimate | console | gap/toggle | prorated | account */
  .show-billing .res-row {{ grid-template-columns: 6px 1fr 120px 70px 36px 32px 60px 60px; }}
  .show-billing .res-service, .show-billing .res-acct, .show-billing .billing-spacer {{ display: block; }}
  .show-billing .cost-meta {{ display: inline; }}
  .hint-tip {{ max-width: 360px !important; }}
  .hint {{ border-bottom-color: #6b7280 !important; }}

  /* ── Cost insight ── */
  .insight-panel {{
    background: #0a0a10; border: 1px solid #1e1e2e; border-radius: 10px;
    padding: 20px 24px; margin-bottom: 16px;
  }}
  .insight-panel h2 {{
    font-size: 11px; color: #6b7280; text-transform: uppercase;
    letter-spacing: 0.1em; margin-bottom: 16px;
  }}
  .tier-row {{
    display: grid; grid-template-columns: 120px 1fr auto;
    gap: 10px; align-items: center; padding: 6px 0;
    border-bottom: 1px solid rgba(255,255,255,0.02);
    font-size: 12px;
  }}
  .tier-row:last-child {{ border-bottom: none; }}
  .tier-bar-bg {{
    height: 16px; background: #111118; border-radius: 3px; overflow: hidden;
  }}
  .tier-bar {{
    height: 100%; border-radius: 3px; transition: width 0.3s;
  }}

  /* ── Footer links ── */
  .footer-links {{
    display: flex; gap: 10px; flex-wrap: wrap; margin-top: 24px;
    font-size: 12px;
  }}
  .footer-links a {{
    color: #4b5563; text-decoration: none; padding: 6px 12px;
    border: 1px solid rgba(255,255,255,0.06); border-radius: 4px;
    transition: all 0.15s;
  }}
  .footer-links a:hover {{ color: #e2e8f0; border-color: #374151; }}

  @media (max-width: 700px) {{
    .svc-header-top {{ flex-direction: column; align-items: flex-start; }}
    .res-row {{ grid-template-columns: 6px 1fr 80px; }}
    .res-type, .res-service, .res-aws, .res-acct, .billing-toggle {{ display: none; }}
  }}

  {HINT_CSS}
</style>
</head>
<body>
{nav}
<div class="arch-container">

  <!-- Service header -->
  <div class="svc-header">
    <div class="svc-header-top">
      <h1>{_e(name)}</h1>
      <span class="zone-badge" style="background:{zone_color}15;color:{zone_color};border:1px solid {zone_color}33;">{_e(zone_label)}</span>
      {f'<span class="zone-badge" style="background:rgba(59,130,246,0.1);color:#60a5fa;border:1px solid rgba(59,130,246,0.2);">{_e(svc_type)}</span>' if svc_type else ""}
      {visit_html}
    </div>
    <div class="svc-meta">
      <span style="color:#4b5563;font-size:11px;font-family:monospace;">{_e(module_address)}</span>
      {f'<span>Host: <strong style="color:#e2e8f0;">{_e(host)}</strong></span>' if host and not host.startswith("*") else ""}
      {f'<span>Auth: <strong style="color:#a855f7;">{_e(auth)}</strong></span>' if auth else ""}
    </div>
    {f'<div style="margin-top:8px;font-size:12px;color:#94a3b8;">{_e(desc)}</div>' if desc else ""}
    {f'<div style="margin-top:6px;font-size:11px;color:#374151;">Source: {_e(file_path)}</div>' if file_path else ""}
  </div>
"""


def _render_summary_strip(total, compliant, nc, cost, cost_unknown, children_count):
    """Render the summary stat pills."""
    pct = round(100 * compliant / total) if total else 100
    pct_color = "#22c55e" if pct >= 80 else ("#f59e0b" if pct >= 50 else "#ef4444")
    cost_color = cost_tier_color(cost)

    # Cost display: distinguish known estimates from unknown
    if cost == 0 and cost_unknown > 0 and cost_unknown == total:
        cost_html = '<span class="hint" style="color:#4b5563;" data-tip="Scanner has no pricing model for any resource in this module. Check the burn page for actual charges.">no model</span>'
    else:
        cost_html = f'{fmt_cost(cost)}<span style="font-size:10px;color:#4b5563;">/mo</span>'
        if cost_unknown > 0:
            cost_html += (
                f' <span class="hint" style="color:#4b5563;font-size:9px;" '
                f'data-tip="{cost_unknown} resource(s) have no pricing model — actual cost may be higher">*</span>'
            )

    return f"""
  <div class="stats-strip">
    <div class="stat-pill">
      <div>
        <div class="stat-label">Resources</div>
        <div class="stat-value" style="color:#e2e8f0;">{total}</div>
      </div>
    </div>
    <div class="stat-pill">
      <div>
        <div class="stat-label">Compliant</div>
        <div class="stat-value" style="color:#22c55e;">{compliant}</div>
      </div>
    </div>
    <div class="stat-pill">
      <div>
        <div class="stat-label">Non-Compliant</div>
        <div class="stat-value" style="color:{"#ef4444" if nc else "#22c55e"};">{nc}</div>
      </div>
    </div>
    <div class="stat-pill">
      <div>
        <div class="stat-label">Compliance</div>
        <div class="stat-value" style="color:{pct_color};">{pct}%</div>
      </div>
    </div>
    <div class="stat-pill">
      <div>
        <div class="stat-label">Est. Monthly</div>
        <div class="stat-value" style="color:{cost_color};">{cost_html}</div>
      </div>
    </div>
    <div class="stat-pill">
      <div>
        <div class="stat-label">Sub-Modules</div>
        <div class="stat-value" style="color:#3b82f6;">{children_count}</div>
      </div>
    </div>
  </div>
"""


def _render_connections(inbound, outbound, module_address):
    """Render the connections section showing inbound/outbound service edges."""
    if not inbound and not outbound:
        return ""

    pills = []
    for conn in inbound:
        src, _ = get_connection_pair(conn)
        src_meta = get_module_meta().get(f"module.{src}", {})
        src_name = src_meta.get("name", src)
        src_link = f"/architecture/custom/module.{urllib.parse.quote(src, safe='')}"
        edge_type = conn.get("type", "http") if isinstance(conn, dict) else "http"
        edge_label = conn.get("label", "") if isinstance(conn, dict) else ""
        style = EDGE_STYLES.get(edge_type, EDGE_STYLES["http"])
        badge_html = (
            f'<span style="font-size:9px;padding:1px 5px;border-radius:3px;'
            f"background:{style['color']}15;color:{style['color']};"
            f'border:1px solid {style["color"]}33;">{_e(edge_label or style["label"])}</span>'
        )
        pills.append(
            f'<a class="conn-pill" href="{src_link}">'
            f'<span style="color:#22c55e;font-weight:600;">{_e(src_name)}</span>'
            f'<span class="conn-arrow">&rarr;</span>'
            f'<span style="color:#6b7280;">this service</span>'
            f"{badge_html}"
            f"</a>"
        )
    for conn in outbound:
        _, tgt = get_connection_pair(conn)
        tgt_meta = get_module_meta().get(f"module.{tgt}", {})
        tgt_name = tgt_meta.get("name", tgt)
        tgt_link = f"/architecture/custom/module.{urllib.parse.quote(tgt, safe='')}"
        edge_type = conn.get("type", "http") if isinstance(conn, dict) else "http"
        edge_label = conn.get("label", "") if isinstance(conn, dict) else ""
        style = EDGE_STYLES.get(edge_type, EDGE_STYLES["http"])
        badge_html = (
            f'<span style="font-size:9px;padding:1px 5px;border-radius:3px;'
            f"background:{style['color']}15;color:{style['color']};"
            f'border:1px solid {style["color"]}33;">{_e(edge_label or style["label"])}</span>'
        )
        pills.append(
            f'<a class="conn-pill" href="{tgt_link}">'
            f'<span style="color:#6b7280;">this service</span>'
            f'<span class="conn-arrow">&rarr;</span>'
            f'<span style="color:#f97316;font-weight:600;">{_e(tgt_name)}</span>'
            f"{badge_html}"
            f"</a>"
        )

    return f"""
  <div class="conn-section">
    <h2>Service Connections</h2>
    <div class="conn-grid">
      {"".join(pills)}
    </div>
  </div>
"""


def _render_context_map_link(focal_name):
    """Render a link card to the interactive context map for this service."""
    interactions_link = f"/architecture/interactions/module.{urllib.parse.quote(focal_name, safe='')}"

    # Edge type legend preview
    legend_items = ""
    for _etype, style in EDGE_STYLES.items():
        dash_attr = f'stroke-dasharray="{style["dash"]}"' if style.get("dash") else ""
        legend_items += (
            f'<span style="display:inline-flex;align-items:center;gap:4px;margin-right:12px;">'
            f'<svg width="20" height="6"><line x1="0" y1="3" x2="20" y2="3" '
            f'stroke="{style["color"]}" stroke-width="2" {dash_attr}/></svg>'
            f'<span style="color:#6b7280;font-size:10px;">{style["label"]}</span></span>'
        )

    return f"""
  <div class="conn-section">
    <h2>Service Context Map</h2>
    <a href="{interactions_link}" style="display:block;text-decoration:none;
       padding:20px 24px;margin:8px 0;border-radius:10px;
       background:rgba(59,130,246,0.04);border:1px solid rgba(59,130,246,0.15);
       transition:all 0.2s;"
       onmouseover="this.style.background='rgba(59,130,246,0.08)';this.style.borderColor='rgba(59,130,246,0.3)';"
       onmouseout="this.style.background='rgba(59,130,246,0.04)';this.style.borderColor='rgba(59,130,246,0.15)';">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
        <span style="color:#3b82f6;font-size:14px;font-weight:600;">
          Interactive Context Map &rarr;</span>
      </div>
      <div style="color:#94a3b8;font-size:11px;line-height:1.5;margin-bottom:10px;">
        D3 force-directed graph showing this service and its connections.
        Click nodes to expand infrastructure components, hover for details.
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:4px;">
        {legend_items}
      </div>
    </a>
  </div>
"""


def _render_resource_row(r, service_costs=None, billed_mode="show", ce_proportions=None):
    """Render a single resource row with estimated, service, and account cost columns.

    billed_mode: "show" = display billed amount (first resource for this CE
    service), "dedup" = already counted above, "none" = no CE service mapping.
    ce_proportions: dict of CE service name → this module's proportional share.
    """
    service_costs = service_costs or {}
    ce_proportions = ce_proportions or {}
    compliant = r.get("compliant", True)
    dot_color = "#22c55e" if compliant else "#ef4444"
    rtype = r.get("type", "")
    name = r.get("name", "")
    arn = r.get("arn", "")
    cost = r.get("estimated_monthly_cost")  # None = no model, 0 = free
    _, tier_color = get_cost_tier(rtype)

    # CE service and billed amount (needed for est-cost flag and both billed columns)
    ce_svc = _TF_TYPE_TO_CE_SERVICE.get(rtype)
    actual_billed = service_costs.get(ce_svc, 0) if ce_svc else 0

    # Console link — always render the cell for grid alignment
    console = _console_url(arn) if arn else ""
    if console:
        link_html = f'<a class="res-link" href="{_e(console)}" target="_blank" rel="noopener">AWS</a>'
    else:
        link_html = ""

    # Estimated cost display — flag "free" when billing contradicts it
    if cost is None:
        cost_html = fmt_cost_est(None)
    elif cost > 0:
        cost_html = fmt_cost(cost) + "/mo"
    elif cost == 0:
        if actual_billed > 0:
            cost_html = (
                '<span class="hint" style="color:#f59e0b;font-size:10px;" '
                'data-tip="Estimated $0 but the AWS service it belongs to has charges — '
                'this resource may use usage-based pricing not captured by the estimate">$0?</span>'
            )
        else:
            cost_html = '<span style="color:#22c55e;font-size:10px;">free</span>'
    else:
        cost_html = ""

    # Service column — proportional share of CE billing for this module
    if billed_mode == "show":
        prop_amount = ce_proportions.get(ce_svc, 0) if ce_svc else 0
        if prop_amount > 0:
            service_html = (
                f'<span class="hint" style="color:#22c55e;" '
                f'data-tip="Proportional share of {_e(ce_svc)} billing based on estimated cost ratio">'
                f"{fmt_cost(prop_amount)}</span>"
            )
        elif ce_svc and service_costs:
            service_html = '<span style="color:#374151;">$0</span>'
        else:
            service_html = '<span style="color:#374151;">&mdash;</span>'
    elif billed_mode == "dedup":
        service_html = '<span style="color:#374151;font-size:10px;">incl.</span>'
    else:
        service_html = '<span style="color:#374151;">&mdash;</span>'

    # Account column — full account-wide CE service billing
    if billed_mode == "show":
        if actual_billed > 0:
            account_html = (
                f'<span class="hint" style="color:#f59e0b;" '
                f'data-tip="Total account-wide billing for {_e(ce_svc)}">'
                f"{fmt_cost(actual_billed)}</span>"
            )
        elif ce_svc and service_costs:
            account_html = '<span style="color:#374151;">$0</span>'
        else:
            account_html = '<span style="color:#374151;">&mdash;</span>'
    elif billed_mode == "dedup":
        account_html = '<span style="color:#374151;font-size:10px;">incl.</span>'
    else:
        account_html = '<span style="color:#374151;">&mdash;</span>'

    return f"""
      <div class="res-row">
        <div class="res-dot" style="background:{dot_color};" title="{"Compliant" if compliant else "Non-compliant"}"></div>
        <div class="res-name" title="{_e(arn or name)}">{_e(name or rtype)}</div>
        <div class="res-type" style="color:{tier_color};">{_e(rtype.replace("aws_", ""))}</div>
        <div class="res-cost">{cost_html}</div>
        <div class="res-aws">{link_html}</div>
        <div class="billing-spacer"></div>
        <div class="res-service">{service_html}</div>
        <div class="res-acct">{account_html}</div>
      </div>"""


def _render_submodule_box(child_address, child_resources, zone_color, service_costs=None, ce_proportions=None):
    """Render a single sub-module as a collapsible box."""
    service_costs = service_costs or {}
    ce_proportions = ce_proportions or {}
    display_name = get_submodule_display_name(child_address)
    total = len(child_resources)
    cost, cost_unk = sum_costs(child_resources)
    dot_color, pct_label = _compliance_dot_color(child_resources)

    # Determine dominant cost tier for border color
    type_counts = {}
    for r in child_resources:
        rt = r.get("type", "")
        type_counts[rt] = type_counts.get(rt, 0) + 1
    dominant_type = max(type_counts, key=type_counts.get) if type_counts else ""
    _, tier_color = get_cost_tier(dominant_type)

    # Cost display with None awareness
    if cost == 0 and cost_unk > 0 and cost_unk == total:
        cost_str = '<span class="hint" style="color:#4b5563;font-size:10px;" data-tip="No pricing model for these resources">no model</span>'
        cost_color = "#4b5563"
    elif cost > 0:
        cost_str = (
            f'<span class="hint" data-tip="Infrastructure-based estimate from instance-type and storage pricing tables. '
            f'Forward-looking monthly rate, not actual billing.">{fmt_cost(cost)}/mo est.</span>'
        )
        if cost_unk > 0:
            cost_str += f' <span class="hint" style="color:#4b5563;font-size:9px;" data-tip="{cost_unk} resource(s) have no pricing model">*</span>'
        cost_color = cost_tier_color(cost)
    else:
        cost_str = (
            '<span class="hint" style="font-size:10px;" data-tip="All resources in this group have $0 estimated cost '
            '(config-only resources like IAM, security groups, certificates).">$0/mo est.</span>'
        )
        cost_color = cost_tier_color(0)

    # Billed spend: proportional (service) and account-wide totals
    billed_services = set()
    for r in child_resources:
        ce_svc = _TF_TYPE_TO_CE_SERVICE.get(r.get("type", ""))
        if ce_svc:
            billed_services.add(ce_svc)
    svc_total = sum(ce_proportions.get(svc, 0) for svc in billed_services)
    acct_total = sum(service_costs.get(svc, 0) for svc in billed_services)

    billed_str = ""
    if billed_services and service_costs:
        svc_label = fmt_cost(svc_total) if svc_total > 0 else "~$0"
        svc_tip = (
            "Prorated share of actual AWS billing this period. "
            "This module's estimated cost ratio x account-wide bill for: " + ", ".join(sorted(billed_services))
        )
        billed_str += (
            f'<span class="cost-meta hint" style="color:#22c55e;font-weight:600;font-size:10px;" '
            f'data-tip="{_e(svc_tip)}">{svc_label} prorated</span> '
        )
    if acct_total > 0:
        acct_tip = "Full account-wide billing across all modules for: " + ", ".join(sorted(billed_services))
        billed_str += (
            f'<span class="cost-meta hint" style="color:#f59e0b;font-weight:600;font-size:10px;" '
            f'data-tip="{_e(acct_tip)}">({fmt_cost(acct_total)} account)</span>'
        )
    if not billed_str:
        if service_costs:
            billed_str = '<span style="color:#374151;font-size:10px;">$0 billed</span>'
        else:
            billed_str = '<span style="color:#374151;font-size:10px;">&mdash;</span>'

    # Render resource rows with billed deduplication (show amount only once per CE service)
    seen_ce = set()
    resource_rows = ""
    for r in child_resources:
        ce_svc = _TF_TYPE_TO_CE_SERVICE.get(r.get("type", ""))
        if ce_svc and ce_svc not in seen_ce:
            seen_ce.add(ce_svc)
            billed_mode = "show"
        elif ce_svc:
            billed_mode = "dedup"
        else:
            billed_mode = "none"
        resource_rows += _render_resource_row(r, service_costs, billed_mode=billed_mode, ce_proportions=ce_proportions)

    # Column header row — order must match .res-row grid: dot, name, type, estimate, console, [prorated, account]
    col_hdr = """
      <div class="res-row res-hdr" style="border-bottom:1px solid rgba(255,255,255,0.06);padding-bottom:4px;margin-bottom:2px;">
        <div></div>
        <div>Name</div>
        <div>Type</div>
        <div style="text-align:right;">Estimate</div>
        <div>Console</div>
        <div class="billing-toggle" onclick="var s=document.getElementById('arch-section');s.classList.toggle('show-billing');var t=s.classList.contains('show-billing')?'◂':'▸';document.querySelectorAll('.billing-toggle').forEach(function(el){el.textContent=t;});" title="Toggle prorated and account-wide billing columns">▸</div>
        <div class="res-service hint" style="text-align:right;" data-tip="Prorated share of this billing period's actual AWS spend. Calculated as: (this module's estimated cost ÷ total estimate across all modules for this AWS service) x actual bill. This is back-of-the-napkin math, not precise attribution — AWS does not bill per-resource without cost allocation tags.">Prorated</div>
        <div class="res-acct hint" style="text-align:right;" data-tip="Total account-wide billing from AWS Cost Explorer for this AWS service. Shared across all modules — not specific to this module.">Account</div>
      </div>"""

    return f"""
    <details class="submod-box" style="border-left-color:{tier_color};" open>
      <summary class="submod-header">
        <div class="res-dot" style="background:{dot_color};width:8px;height:8px;" title="Compliance: {pct_label}"></div>
        <div class="submod-name">{_e(display_name)}</div>
        <div class="submod-meta">
          <span>{total} resources</span>
          <span style="color:{cost_color};font-weight:600;">{cost_str}</span>
          {billed_str}
        </div>
      </summary>
      <div class="submod-body">
        {col_hdr}
        {resource_rows}
      </div>
    </details>"""


def _render_internal_architecture(children, top_level_resources, zone_color, service_costs=None, ce_proportions=None):
    """Render the internal architecture section with sub-module boxes."""
    service_costs = service_costs or {}
    ce_proportions = ce_proportions or {}
    if not children and not top_level_resources:
        return """
  <div class="submod-section">
    <h2>Internal Architecture</h2>
    <div style="color:#4b5563;font-size:12px;padding:16px 0;">
      No resources found in this module.
    </div>
  </div>"""

    boxes = []

    # Render child modules (sorted by cost descending for visual impact)
    sorted_children = sorted(
        children.items(),
        key=lambda x: -sum_costs(x[1])[0],
    )
    for child_addr, child_resources in sorted_children:
        boxes.append(_render_submodule_box(child_addr, child_resources, zone_color, service_costs, ce_proportions))

    # Render top-level resources (those not in any child module)
    if top_level_resources:
        boxes.append(
            _render_submodule_box(
                "Direct Resources",
                top_level_resources,
                zone_color,
                service_costs,
                ce_proportions,
            )
        )

    return f"""
  <div class="submod-section" id="arch-section">
    <h2>Internal Architecture</h2>
    <div class="submod-grid">
      {"".join(boxes)}
    </div>
  </div>"""


def _render_cost_insight(module_address, meta, stats, children, resources, service_costs=None):
    """Render the cost insight panel with tier breakdown and context."""
    service_costs = service_costs or {}
    svc_type = meta.get("type", "")
    total_cost = stats.get("cost", 0)
    cost_unknown = stats.get("cost_unknown", 0)

    # Build tier breakdown (only count resources with known costs)
    tier_data = {}
    no_model_count = 0
    for r in resources:
        rt = r.get("type", "")
        tier_key, _tier_color = get_cost_tier(rt)
        cost = r.get("estimated_monthly_cost")
        if cost is None:
            no_model_count += 1
            continue
        if tier_key not in tier_data:
            tier_info = COST_TIERS.get(tier_key, {})
            tier_data[tier_key] = {
                "count": 0,
                "cost": 0,
                "color": tier_info.get("color", "#6b7280"),
                "label": tier_info.get("label", tier_key),
            }
        tier_data[tier_key]["count"] += 1
        tier_data[tier_key]["cost"] += cost

    # Sort by cost descending
    sorted_tiers = sorted(tier_data.items(), key=lambda x: -x[1]["cost"])
    max_cost = max((d["cost"] for _, d in sorted_tiers), default=1) or 1

    tier_rows = []
    for _tier_key, data in sorted_tiers:
        bar_pct = (data["cost"] / max_cost) * 100 if max_cost > 0 else 0
        cost_str = fmt_cost(data["cost"])
        tier_rows.append(f"""
      <div class="tier-row">
        <div style="display:flex;align-items:center;gap:6px;">
          <span style="width:8px;height:8px;border-radius:50%;background:{data["color"]};flex-shrink:0;"></span>
          <span style="color:{data["color"]};">{_e(data["label"])}</span>
        </div>
        <div class="tier-bar-bg">
          <div class="tier-bar" style="width:{bar_pct:.1f}%;background:{data["color"]}33;"></div>
        </div>
        <div style="color:#e2e8f0;font-weight:600;text-align:right;">
          {cost_str}<span style="color:#4b5563;font-size:10px;">/mo</span>
          <span style="color:#4b5563;font-size:10px;margin-left:4px;">({data["count"]})</span>
        </div>
      </div>""")

    # "No model" tier row if any resources lacked pricing
    if no_model_count > 0:
        tier_rows.append(f"""
      <div class="tier-row">
        <div style="display:flex;align-items:center;gap:6px;">
          <span style="width:8px;height:8px;border-radius:50%;background:#4b5563;flex-shrink:0;"></span>
          <span class="hint" style="color:#4b5563;" data-tip="These resources have no pricing model in the scanner. Cost depends on usage volume.">No Model</span>
        </div>
        <div class="tier-bar-bg">
          <div class="tier-bar" style="width:0%;background:#4b556333;"></div>
        </div>
        <div style="color:#4b5563;font-size:11px;text-align:right;">
          ? <span style="font-size:10px;">({no_model_count})</span>
        </div>
      </div>""")

    # Context-aware insight text
    insight_text = _get_insight_text(svc_type, total_cost, tier_data)

    # Total with unknown note
    unknown_note = ""
    if cost_unknown > 0:
        unknown_note = (
            f'<span class="hint" style="color:#4b5563;font-size:10px;margin-left:8px;" '
            f'data-tip="{cost_unknown} resource(s) have no pricing model — actual cost may be higher">'
            f"({cost_unknown} unpriced)</span>"
        )

    # Actual spend section — map this module's TF resource types to CE services
    actual_spend_html = ""
    if service_costs:
        ce_services_used = set()
        for r in resources:
            ce_svc = _TF_TYPE_TO_CE_SERVICE.get(r.get("type", ""))
            if ce_svc:
                ce_services_used.add(ce_svc)

        if ce_services_used:
            spend_rows = []
            for ce_svc in sorted(ce_services_used):
                billed = service_costs.get(ce_svc)
                if billed is not None and billed > 0:
                    spend_rows.append(
                        f'<div style="display:flex;justify-content:space-between;padding:4px 0;">'
                        f'<span style="color:#94a3b8;font-size:12px;">{_e(ce_svc)}</span>'
                        f'<span style="color:#22c55e;font-weight:600;font-size:12px;">${billed:,.2f}/mo</span>'
                        f"</div>"
                    )

            if spend_rows:
                actual_spend_html = (
                    '<div style="margin-top:16px;padding-top:12px;border-top:1px solid #1e1e2e;">'
                    '<div style="margin-bottom:8px;font-size:10px;color:#4b5563;text-transform:uppercase;letter-spacing:0.08em;">'
                    'Billed (Account-Wide) <span class="hint" data-tip="Actual AWS billing from Cost Explorer for '
                    "services used by this module. These are account-wide totals, not per-module — other modules "
                    'may also contribute to these costs.">&amp;#9432;</span>'
                    "</div>" + "".join(spend_rows) + "</div>"
                )

    return f"""
  <div class="insight-panel">
    <h2>Cost Insight</h2>
    <div style="margin-bottom:16px;font-size:13px;color:#94a3b8;">
      {insight_text}
    </div>
    <div style="margin-bottom:8px;font-size:10px;color:#4b5563;text-transform:uppercase;letter-spacing:0.08em;">
      Cost by Resource Tier
    </div>
    {"".join(tier_rows)}
    <div style="margin-top:12px;padding-top:12px;border-top:1px solid #1e1e2e;font-size:12px;">
      <span style="color:#6b7280;">Total estimated:</span>
      <span style="color:{cost_tier_color(total_cost)};font-weight:700;font-size:16px;margin-left:8px;">
        {fmt_cost(total_cost)}<span style="font-size:10px;color:#4b5563;">/mo</span>
      </span>
      {unknown_note}
    </div>
    {actual_spend_html}
    <div style="margin-top:8px;font-size:11px;color:#4b5563;">
      <a href="/burn" style="color:#3b82f6;text-decoration:none;">Burn page</a> has full billing breakdown.
    </div>
  </div>
"""


def _get_insight_text(svc_type, total_cost, tier_data):
    """Return context-aware insight text for the cost panel."""
    if svc_type == "Lambda":
        return (
            "This service runs on <strong style='color:#22c55e;'>AWS Lambda</strong>, "
            "which costs ~$0.20 per 1M invocations plus duration charges. "
            "At typical low-traffic scales, Lambda functions cost effectively nothing. "
            "This is orders of magnitude cheaper than running an equivalent always-on "
            "ECS Fargate service (~$30/mo) or EC2 instance (~$42/mo for t3.medium)."
        )
    if svc_type == "ECS Fargate":
        base = (
            "This service runs on <strong style='color:#3b82f6;'>ECS Fargate</strong> "
            "(~$30/mo for 0.25 vCPU / 512MB). Fargate eliminates EC2 instance management "
            "but costs more per unit of compute than reserved EC2."
        )
        if total_cost > 40:
            base += (
                " The majority of this service's cost comes from its backing data stores "
                "(RDS, ElastiCache) rather than the compute itself."
            )
        return base
    if svc_type == "EC2":
        return (
            "This service runs on <strong style='color:#f59e0b;'>EC2</strong> with "
            "always-on compute. Stopped instances still incur EBS storage charges. "
            "For intermittent workloads, consider Lambda (near-zero cost) or "
            "Fargate Spot for batch processing."
        )
    if svc_type == "ElastiCache":
        return (
            "This is an <strong style='color:#f59e0b;'>ElastiCache Redis</strong> cluster. "
            "Redis runs continuously regardless of utilization. For session storage at "
            "low scale, DynamoDB (on-demand, ~$0.50/mo) could be a cheaper alternative."
        )
    if svc_type in ("CloudTrail", "GuardDuty", "CloudWatch", "WAF"):
        return (
            f"<strong style='color:#ef4444;'>{svc_type}</strong> is an AWS security/observability "
            "service with event-volume pricing. Cost scales with account activity, not with "
            "resource count. These services have no equivalent serverless alternative — they "
            "are foundational to security posture."
        )
    # Generic fallback
    if total_cost < 1:
        return (
            "This service has <strong style='color:#22c55e;'>near-zero estimated cost</strong>. "
            "It uses free-tier or serverless resources that only incur charges with usage."
        )
    return (
        f"This service has an estimated cost of <strong>{fmt_cost(total_cost)}/mo</strong>. "
        "The cost breakdown below shows how spend distributes across resource tiers."
    )


def _render_footer(module_address, name, file_path):
    """Render footer with navigation links."""
    encoded = urllib.parse.quote(module_address, safe="")
    return f"""
  <div class="footer-links">
    <a href="/architecture/custom">&larr; All Services</a>
    <a href="/detail/{encoded}">Detail View</a>
    <a href="/tree">Tree View</a>
    <a href="/network">Network View</a>
    <a href="/burn">Burn Analysis</a>
  </div>
"""


def _page_close():
    """Closing HTML."""
    return """
  <div style="margin-top:24px;text-align:center;font-size:10px;color:#374151;">
    Generated from live Terraform state &middot; Cost estimates from compliance scanner &middot;
    <a href="/burn" style="color:#3b82f6;text-decoration:none;">Actual billing on burn page</a>
  </div>
</div>
<script>(function(){
  var tip=document.createElement('div');
  tip.className='hint-tip';
  document.body.appendChild(tip);
  document.addEventListener('mouseover',function(e){
    var h=(e.target instanceof Element?e.target:document.documentElement).closest('.hint');
    if(!h){tip.classList.remove('visible');return;}
    tip.textContent=h.getAttribute('data-tip');
    tip.classList.add('visible');
    var r=h.getBoundingClientRect();
    var above=r.top-tip.offsetHeight-4;
    tip.style.top=(above>0?above:(r.bottom+4))+'px';
    var left=r.left;
    if(left+360>window.innerWidth)left=window.innerWidth-370;
    if(left<4)left=4;
    tip.style.left=left+'px';
  });
  document.addEventListener('mouseout',function(e){
    var h=(e.target instanceof Element?e.target:document.documentElement).closest('.hint');
    if(h&&!h.contains(e.relatedTarget))tip.classList.remove('visible');
  });
})()</script>
</body>
</html>"""


def _render_not_found(module_address, user_email=""):
    """Render a not-found page for invalid module addresses."""
    nav = render_nav_arch("custom", user_email, sub_breadcrumb="Not Found")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Module Not Found // __BRAND__</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #050508; color: #e2e8f0;
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    min-height: 100vh;
  }}
  {NAV_CSS}
  .error-box {{
    background: #111118; border: 1px solid #1e1e2e; border-radius: 12px;
    padding: 32px; max-width: 600px; text-align: center;
    margin: 80px auto;
  }}
  .error-box h1 {{ color: #ef4444; font-size: 18px; margin-bottom: 12px; }}
  .error-box p {{ color: #6b7280; font-size: 13px; line-height: 1.6; margin-bottom: 16px; }}
  .error-box code {{
    background: rgba(239,68,68,0.1); color: #ef4444; padding: 2px 8px;
    border-radius: 4px; font-size: 12px; word-break: break-all;
  }}
  .error-box a {{ color: #3b82f6; text-decoration: none; font-size: 13px; }}
  .error-box a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
{nav}
<div class="error-box">
  <h1>Module Not Found</h1>
  <p><code>{_e(module_address)}</code> is not in the current Terraform state.</p>
  <p>This module may have been removed or renamed.</p>
  <a href="/architecture/custom">&larr; Back to Architecture</a>
</div>
</body>
</html>"""

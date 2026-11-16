"""
Module Detail page — universal detail view for any TF module.

Works for ALL modules (compliant and non-compliant). Shows module metadata,
resource breakdown, compliance status, and links to related views. For
non-compliant modules, shows findings inline. Linked from the custom
architecture view's service cards.
"""

import html

from arch_shared import get_module_meta
from compliance_schema import SEVERITY_ORDER
from data import (
    _MODULE_FILE_MAP,
    get_all_findings,
    get_compliance_report,
    get_tf_module_tree,
)
from data_core import REPOSITORY_URL
from views_base import (
    _SEV_BADGE,
    _ZONE_BADGE,
    NAV_CSS,
    TYPE_ICONS,
    _badge,
    _console_url,
    _e,
    _rate_color,
    render_nav,
)


def render_detail(module_address, user_email=""):
    """Render the module detail page for a given TF module address."""
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

    # Module metadata from arch_shared (display name, host, desc, etc.)
    meta = get_module_meta().get(module_address, {})
    name = meta.get("name", module_address.replace("module.", ""))
    desc = meta.get("desc", "")
    host = meta.get("host", "")
    auth = meta.get("auth", "")
    zone = meta.get("zone", "unknown")

    # Stats
    stats = module_data["stats"]
    total = stats["total"]
    compliant = stats["compliant"]
    non_compliant = stats["non_compliant"]
    pct = round(100 * compliant / total) if total else 100
    pct_color = _rate_color(pct)
    is_compliant = non_compliant == 0

    # File path
    file_path = module_data.get("file_path", "") or _MODULE_FILE_MAP.get(module_address, "")
    repo_url_base = REPOSITORY_URL
    repo_url = f"{repo_url_base}/blob/main/{file_path}" if file_path else ""

    # Resources
    resources = module_data.get("resources", [])

    # Findings for this module's non-compliant resources
    nc_arns = {r["arn"] for r in resources if not r["compliant"] and r.get("arn")}
    all_findings = get_all_findings(report) if report else []
    module_findings = [f for f in all_findings if f.get("resource_arn", "") in nc_arns]

    # Group resources by type for summary
    type_counts = {}
    for r in resources:
        rtype = r.get("type", "unknown")
        if rtype not in type_counts:
            type_counts[rtype] = {"total": 0, "compliant": 0, "non_compliant": 0}
        type_counts[rtype]["total"] += 1
        if r["compliant"]:
            type_counts[rtype]["compliant"] += 1
        else:
            type_counts[rtype]["non_compliant"] += 1

    # Zone display config
    zone_labels = {
        "public": "Public Subnets",
        "apps": "Applications",
        "identity": "Identity Stack",
        "serverless": "AWS Serverless",
        "security": "Security & Monitoring",
        "infra": "Infrastructure",
    }
    zone_label = zone_labels.get(zone, zone.title())

    # Build HTML
    nav_html = render_nav("architecture", user_email, meta=f"Module: {name}")

    # Status banner
    if is_compliant:
        status_html = f"""
    <div style="background:rgba(34,197,94,0.08); border:1px solid rgba(34,197,94,0.25);
                border-radius:8px; padding:16px 20px; margin-bottom:20px;
                display:flex; align-items:center; gap:12px;">
      <div style="width:12px; height:12px; border-radius:50%; background:#22c55e;
                  box-shadow:0 0 8px #22c55e; flex-shrink:0;"></div>
      <div>
        <div style="font-size:14px; font-weight:700; color:#22c55e;">ALL CLEAR</div>
        <div style="font-size:12px; color:#4b5563;">All {total} resources in this module are compliant.</div>
      </div>
    </div>"""
    else:
        status_html = f"""
    <div style="background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.25);
                border-radius:8px; padding:16px 20px; margin-bottom:20px;
                display:flex; align-items:center; gap:12px;">
      <div style="width:12px; height:12px; border-radius:50%; background:#ef4444;
                  box-shadow:0 0 8px #ef4444; flex-shrink:0;"></div>
      <div>
        <div style="font-size:14px; font-weight:700; color:#ef4444;">{non_compliant} NON-COMPLIANT RESOURCE{"S" if non_compliant != 1 else ""}</div>
        <div style="font-size:12px; color:#4b5563;">{compliant} of {total} resources passing all checks.</div>
      </div>
    </div>"""

    # Links bar
    links_parts = []
    if host:
        links_parts.append(
            f'<a href="https://{_e(host)}" target="_blank" rel="noopener" '
            f'class="btn btn-primary" style="font-size:11px;">Visit {_e(host)}</a>'
        )
    links_parts.append(
        '<a href="/architecture/custom" class="btn btn-secondary" style="font-size:11px;">Architecture</a>'
    )
    links_parts.append('<a href="/tree" class="btn btn-secondary" style="font-size:11px;">TF Tree</a>')
    if repo_url:
        links_parts.append(
            f'<a href="{_e(repo_url)}" target="_blank" rel="noopener" '
            f'class="btn btn-secondary" style="font-size:11px;">Source</a>'
        )
    if not is_compliant:
        links_parts.append(
            '<a href="/findings" class="btn btn-secondary" style="font-size:11px; color:#ef4444; '
            'border-color:rgba(239,68,68,0.3);">All Findings</a>'
        )
    links_html = "\n      ".join(links_parts)

    # Resource type breakdown table
    type_rows = ""
    for rtype in sorted(type_counts.keys()):
        tc = type_counts[rtype]
        icon = TYPE_ICONS.get(rtype, "")
        rate = round(100 * tc["compliant"] / tc["total"]) if tc["total"] else 100
        nc_cell = (
            f'<td style="color:#ef4444; font-weight:600;">{tc["non_compliant"]}</td>'
            if tc["non_compliant"] > 0
            else '<td style="color:#22c55e;">0</td>'
        )
        type_rows += f"""<tr>
          <td style="color:#6b7280;">{icon} {_e(rtype)}</td>
          <td>{tc["total"]}</td>
          <td style="color:#22c55e; font-weight:600;">{tc["compliant"]}</td>
          {nc_cell}
          <td style="color:{_rate_color(rate)}; font-weight:600;">{rate}%</td>
        </tr>"""

    # Resource list table
    resource_rows = ""
    for r in sorted(resources, key=lambda x: (x["compliant"], x.get("type", ""), x.get("name", ""))):
        arn = r.get("arn", "")
        rid = r.get("id", "")
        rtype = r.get("type", "")
        rname = r.get("name", "")
        icon = TYPE_ICONS.get(rtype, "")
        is_ok = r["compliant"]

        status_dot = (
            '<span style="color:#22c55e; font-weight:700;">PASS</span>'
            if is_ok
            else '<span style="color:#ef4444; font-weight:700;">FAIL</span>'
        )

        # Link to cure page if non-compliant and has ARN
        name_cell = _e(rname)
        if not is_ok and arn:
            arn_encoded = html.escape(arn, quote=True)
            name_cell = (
                f'<a href="/cure?arn={arn_encoded}" style="color:#e2e8f0; '
                f'text-decoration:none;" title="View remediation plan">{_e(rname)}</a>'
            )

        # Console link
        console = _console_url(arn) if arn else ""
        console_icon = (
            f' <a href="{_e(console)}" target="_blank" rel="noopener" '
            f'style="color:#4b5563; text-decoration:none; font-size:10px;" '
            f'title="Open in AWS Console">&#x2197;</a>'
            if console
            else ""
        )

        # ARN display (truncated)
        arn_display = _e(arn[-50:]) if arn else _e(rid[:30]) if rid else "-"
        arn_full = _e(arn or rid or "")

        resource_rows += f"""<tr style="border-left:3px solid {"#22c55e" if is_ok else "#ef4444"};">
          <td>{status_dot}</td>
          <td style="color:#6b7280; font-size:12px;">{icon} {_e(rtype)}</td>
          <td>{name_cell}{console_icon}</td>
          <td class="mono truncate" style="font-size:11px;" title="{arn_full}">{arn_display}</td>
        </tr>"""

    # Findings section (only if non-compliant)
    findings_html = ""
    if module_findings:
        module_findings.sort(key=lambda f: SEVERITY_ORDER.get(f.get("severity", ""), 3))

        finding_rows = ""
        for f in module_findings:
            sev = f.get("severity", "")
            arn = f.get("resource_arn", "")
            fname = f.get("resource_name", "") or arn.split(":")[-1][:40]
            issue = f.get("issue", "")
            category = f.get("category", "")
            cost = f.get("estimated_monthly_cost")
            if cost is not None and cost > 0:
                cost_str = f"${float(cost):.2f}/mo"
            elif cost is None:
                cost_str = '<span style="color:#4b5563;font-size:10px;" title="No pricing model">-</span>'
            else:
                cost_str = "-"

            arn_encoded = html.escape(arn, quote=True)
            cure_link = f"/cure?arn={arn_encoded}"

            finding_rows += f"""<tr>
              <td>{_badge(_e(sev), _SEV_BADGE.get(sev, "gray"))}</td>
              <td><a href="{cure_link}" style="color:#e2e8f0; text-decoration:none;">{_e(fname)}</a></td>
              <td style="color:#6b7280; font-size:12px;">{_e(category)}</td>
              <td style="color:#94a3b8; font-size:12px;">{_e(issue[:60])}</td>
              <td class="cost cost-bad">{cost_str}</td>
            </tr>"""

        findings_html = f"""
    <h2>FINDINGS</h2>
    <table>
      <thead>
        <tr><th>Severity</th><th>Resource</th><th>Category</th><th>Issue</th><th>Cost</th></tr>
      </thead>
      <tbody>{finding_rows}</tbody>
    </table>"""

    # Children modules
    children_html = ""
    children = module_data.get("children", {})
    if children:
        child_items = ""
        for child_name, child_resources in sorted(children.items()):
            child_total = len(child_resources)
            child_ok = sum(1 for r in child_resources if r["compliant"])
            child_nc = child_total - child_ok
            child_pct = round(100 * child_ok / child_total) if child_total else 100
            dot_color = "#22c55e" if child_nc == 0 else ("#f59e0b" if child_pct >= 70 else "#ef4444")
            child_items += f"""
            <div style="background:#111118; border:1px solid #1e1e2e; border-radius:6px;
                        padding:10px 14px; display:flex; justify-content:space-between;
                        align-items:center;">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="width:8px; height:8px; border-radius:50%; background:{dot_color};
                             box-shadow:0 0 4px {dot_color}; flex-shrink:0;"></span>
                <span class="mono" style="font-size:12px; color:#94a3b8;">{_e(child_name)}</span>
              </div>
              <div style="display:flex; gap:12px; align-items:center; font-size:11px;">
                <span style="color:#4b5563;">{child_total} resources</span>
                <span style="color:{_rate_color(child_pct)}; font-weight:600;">{child_pct}%</span>
              </div>
            </div>"""

        children_html = f"""
    <h2>CHILD MODULES</h2>
    <div style="display:flex; flex-direction:column; gap:4px; margin-bottom:20px;">
      {child_items}
    </div>"""

    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(name)} // __BRAND__</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    background: #050508; color: #e2e8f0; line-height: 1.6;
    min-height: 100vh;
  }}
  ::selection {{ background: rgba(59,130,246,0.3); }}

  {NAV_CSS}

  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}

  h1 {{ font-size: 20px; font-weight: 700; margin-bottom: 4px; color: #f1f5f9;
        letter-spacing: 0.02em; }}
  h2 {{ font-size: 14px; font-weight: 600; margin: 24px 0 10px; color: #94a3b8;
        text-transform: uppercase; letter-spacing: 0.08em; }}

  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 10px; margin-bottom: 20px; }}
  .card {{ background: #111118; border: 1px solid #1e1e2e; border-radius: 8px;
           padding: 14px 18px; transition: border-color 0.15s; }}
  .card:hover {{ border-color: #2a2a3a; }}
  .card .label {{ font-size: 10px; color: #6b7280; text-transform: uppercase;
                  letter-spacing: 0.12em; }}
  .card .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}

  table {{ width: 100%; border-collapse: collapse; background: #111118;
           border: 1px solid #1e1e2e; border-radius: 8px; overflow: hidden; }}
  th {{ background: #0d0d14; text-align: left; padding: 8px 12px; font-size: 10px;
       color: #6b7280; text-transform: uppercase; letter-spacing: 0.1em;
       border-bottom: 1px solid #1e1e2e; }}
  td {{ padding: 8px 12px; border-top: 1px solid #141420; font-size: 13px; color: #cbd5e1; }}
  tr:hover td {{ background: #141420; }}

  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 10px; font-weight: 700; letter-spacing: 0.06em; }}
  .btn {{ display: inline-block; padding: 6px 14px; border-radius: 4px; font-size: 12px;
          font-weight: 600; text-decoration: none; cursor: pointer; border: 1px solid transparent;
          letter-spacing: 0.03em; transition: all 0.15s; font-family: inherit; }}
  .btn-primary {{ background: rgba(59,130,246,0.15); color: #3b82f6; border-color: rgba(59,130,246,0.3); }}
  .btn-primary:hover {{ background: #3b82f6; color: #fff; }}
  .btn-secondary {{ background: #1e1e2e; color: #94a3b8; border-color: #2a2a3a; }}
  .btn-secondary:hover {{ background: #2a2a3a; color: #e2e8f0; }}

  .mono {{ font-family: inherit; font-size: 12px; color: #6b7280; }}
  .truncate {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .cost {{ font-weight: 600; }}
  .cost-bad {{ color: #ef4444; }}
</style>
</head>
<body>
{nav_html}
<div class="container">
  <div style="margin-bottom:16px;">
    <a href="/architecture/custom" class="btn btn-secondary" style="font-size:11px;">&larr; Architecture</a>
  </div>

  <div style="display:flex; align-items:center; gap:12px; margin-bottom:4px;">
    <h1>{_e(name)}</h1>
    {_badge(_e(zone_label), _ZONE_BADGE.get(zone, "gray"))}
  </div>

  <div style="color:#4b5563; font-size:12px; margin-bottom:4px;">
    <span class="mono">{_e(module_address)}</span>
    {f'&middot; <span style="color:#6b7280;">{_e(file_path)}</span>' if file_path else ""}
  </div>

  {f'<div style="color:#94a3b8; font-size:13px; margin-bottom:4px;">{_e(desc)}</div>' if desc else ""}
  {f'<div style="font-size:12px; margin-bottom:4px;"><span style="color:#4b5563;">Host:</span> <a href="https://{_e(host)}" target="_blank" rel="noopener" style="color:#3b82f6; text-decoration:none;">{_e(host)}</a></div>' if host else ""}
  {f'<div style="font-size:12px; margin-bottom:4px;"><span style="color:#4b5563;">Auth:</span> <span style="color:#a855f7;">{_e(auth)}</span></div>' if auth else ""}

  <div style="display:flex; gap:8px; margin:12px 0 20px; flex-wrap:wrap;">
    {links_html}
  </div>

  {status_html}

  <div class="cards">
    <div class="card">
      <div class="label">Resources</div>
      <div class="value" style="color:#e2e8f0;">{total}</div>
    </div>
    <div class="card">
      <div class="label">Compliant</div>
      <div class="value" style="color:#22c55e;">{compliant}</div>
    </div>
    <div class="card">
      <div class="label">Non-Compliant</div>
      <div class="value" style="color:{"#ef4444" if non_compliant else "#22c55e"};">{non_compliant}</div>
    </div>
    <div class="card">
      <div class="label">Compliance</div>
      <div class="value" style="color:{pct_color};">{pct}%</div>
    </div>
  </div>

  <h2>RESOURCE TYPES</h2>
  <table>
    <thead>
      <tr><th>Type</th><th>Total</th><th>Compliant</th><th>Non-Compliant</th><th>Rate</th></tr>
    </thead>
    <tbody>{type_rows}</tbody>
  </table>

  {findings_html}

  {children_html}

  <h2>ALL RESOURCES</h2>
  <table>
    <thead>
      <tr><th>Status</th><th>Type</th><th>Name</th><th>ARN / ID</th></tr>
    </thead>
    <tbody>{resource_rows}</tbody>
  </table>

  <div style="margin-top:24px; text-align:center; font-size:10px; color:#374151; padding-bottom:16px;">
    <a href="/architecture/custom" style="color:#3b82f6; text-decoration:none;">Architecture</a> &middot;
    <a href="/tree" style="color:#3b82f6; text-decoration:none;">TF Tree</a> &middot;
    <a href="/network" style="color:#3b82f6; text-decoration:none;">Network</a> &middot;
    <a href="/" style="color:#3b82f6; text-decoration:none;">Dashboard</a>
  </div>
</div>
</body>
</html>"""

    return content


def _render_not_found(module_address, user_email=""):
    """Render a 'module not found' page."""
    nav_html = render_nav("architecture", user_email)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Module Not Found // __BRAND__</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    background: #050508; color: #e2e8f0; line-height: 1.6;
    min-height: 100vh;
  }}
  {NAV_CSS}
  .container {{ max-width: 800px; margin: 0 auto; padding: 48px 24px; text-align: center; }}
</style>
</head>
<body>
{nav_html}
<div class="container">
  <div style="background:#111118; border:1px solid #1e1e2e; border-radius:12px;
              padding:32px; max-width:600px; margin:0 auto;">
    <div style="font-size:18px; font-weight:700; color:#ef4444; margin-bottom:12px;">
      Module Not Found
    </div>
    <div style="color:#6b7280; font-size:13px; margin-bottom:8px;">
      <code style="background:rgba(239,68,68,0.1); color:#ef4444; padding:2px 8px;
                   border-radius:4px; font-size:12px;">{_e(module_address)}</code>
    </div>
    <div style="color:#4b5563; font-size:12px; margin-bottom:20px;">
      This module is not present in the current Terraform state.
      It may not be deployed, or the module address may be incorrect.
    </div>
    <a href="/architecture/custom" style="color:#3b82f6; text-decoration:none; font-size:13px;">
      &larr; Back to Architecture
    </a>
    &middot;
    <a href="/tree" style="color:#3b82f6; text-decoration:none; font-size:13px;">
      TF Tree
    </a>
    &middot;
    <a href="/" style="color:#3b82f6; text-decoration:none; font-size:13px;">
      Dashboard
    </a>
  </div>
</div>
</body>
</html>"""

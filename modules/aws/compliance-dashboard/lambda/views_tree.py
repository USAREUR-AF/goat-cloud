"""
TF State Tree — interactive module hierarchy with compliance heatmap.

CSS-only collapsible tree (checkbox hack, no JavaScript frameworks).
Each module node shows compliance rate as a color-coded bar.
Resources within each module link to their CURE page.
"""

import urllib.parse

from views_base import TYPE_ICONS, _e, _layout, _rate_color


def render_tree(tree, user_email=""):
    """Render the full TF state tree with compliance overlay."""
    if not tree:
        content = '<div class="empty">No Terraform state data available.</div>'
        return _layout("TF Tree", content, user_email, active_page="tree")

    # Aggregate stats
    total_resources = sum(m["stats"]["total"] for m in tree)
    total_compliant = sum(m["stats"]["compliant"] for m in tree)
    total_nc = total_resources - total_compliant
    pct = round(100 * total_compliant / total_resources) if total_resources else 100

    content = f"""
    <h1>TERRAFORM STATE TREE</h1>
    <div class="cards" style="margin-bottom:20px;">
      <div class="card">
        <div class="label">MODULES</div>
        <div class="value">{len(tree)}</div>
      </div>
      <div class="card">
        <div class="label">RESOURCES</div>
        <div class="value">{total_resources}</div>
      </div>
      <div class="card">
        <div class="label">COMPLIANT</div>
        <div class="value" style="color:#22c55e;">{total_compliant}</div>
      </div>
      <div class="card">
        <div class="label">NON-COMPLIANT</div>
        <div class="value" style="color:{"#ef4444" if total_nc else "#22c55e"};">{total_nc}</div>
      </div>
      <div class="card">
        <div class="label">COMPLIANCE RATE</div>
        <div class="value" style="color:{_rate_color(pct)};">{pct}%</div>
      </div>
    </div>

    <style>
      /* Tree collapse: checkbox hack */
      .tree-toggle {{ display: none; }}
      .tree-toggle + label {{ cursor: pointer; user-select: none; }}
      .tree-toggle + label + .tree-children {{ max-height: 0; overflow: hidden; transition: max-height 0.3s ease; }}
      .tree-toggle:checked + label + .tree-children {{ max-height: 50000px; }}
      /* Module node */
      .tree-node {{
        background: #111118; border: 1px solid #1e1e2e; border-radius: 8px;
        margin-bottom: 8px; overflow: hidden; transition: border-color 0.15s;
      }}
      .tree-node:hover {{ border-color: #2a2a3a; }}
      .tree-node-header {{
        display: flex; align-items: center; gap: 12px; padding: 12px 16px;
      }}
      .tree-node-header:hover {{ background: #141420; }}
      /* Chevron */
      .tree-chevron {{
        width: 16px; height: 16px; color: #4b5563; transition: transform 0.2s;
        flex-shrink: 0;
      }}
      .tree-toggle:checked + label .tree-chevron {{ transform: rotate(90deg); }}
      /* Compliance bar */
      .compliance-bar {{
        height: 4px; border-radius: 2px; background: #1e1e2e;
        flex: 1; max-width: 120px; overflow: hidden;
      }}
      .compliance-bar-fill {{ height: 100%; border-radius: 2px; transition: width 0.3s; }}
      /* Resource list inside expanded node */
      .tree-resources {{
        padding: 0 16px 12px 44px;
      }}
      .tree-resource {{
        display: flex; align-items: center; gap: 8px; padding: 4px 0;
        font-size: 12px; border-top: 1px solid #0d0d14;
      }}
      .tree-resource:first-child {{ border-top: none; }}
      .tree-dot {{
        width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
      }}
      .tree-dot-ok {{ background: #22c55e; box-shadow: 0 0 4px rgba(34,197,94,0.4); }}
      .tree-dot-bad {{ background: #ef4444; box-shadow: 0 0 4px rgba(239,68,68,0.4); }}
      /* Child module indentation */
      .tree-child-header {{
        padding: 6px 16px 6px 44px; font-size: 11px; color: #6b7280;
        letter-spacing: 0.05em; border-top: 1px solid #0d0d14;
      }}
    </style>

    <div style="margin-bottom:8px; display:flex; gap:12px; align-items:center;">
      <span style="font-size:11px; color:#6b7280;">LEGEND:</span>
      <span style="display:flex;align-items:center;gap:4px;font-size:11px;color:#6b7280;">
        <span class="tree-dot tree-dot-ok" style="display:inline-block;"></span> Compliant
      </span>
      <span style="display:flex;align-items:center;gap:4px;font-size:11px;color:#6b7280;">
        <span class="tree-dot tree-dot-bad" style="display:inline-block;"></span> Non-compliant
      </span>
    </div>
    """

    # Render each module as a collapsible node
    for i, mod in enumerate(tree):
        content += _render_module_node(mod, i)

    return _layout("TF Tree", content, user_email, active_page="tree")


def _render_module_node(mod, index):
    """Render a single module as a collapsible tree node."""
    name = mod["module"]
    stats = mod["stats"]
    total = stats["total"]
    compliant = stats["compliant"]
    nc = stats["non_compliant"]
    file_path = mod.get("file_path", "")
    pct = round(100 * compliant / total) if total else 100

    # Border color based on compliance
    if nc == 0:
        border_color = "#22c55e"  # green — fully compliant
    elif pct >= 70:
        border_color = "#f59e0b"  # amber — mostly compliant
    else:
        border_color = "#ef4444"  # red — poor compliance

    bar_color = _rate_color(pct)
    bar_width = pct

    # Unique ID for checkbox hack
    node_id = f"tree-{index}"

    html = f"""
    <div class="tree-node" style="border-left: 3px solid {border_color};">
      <input type="checkbox" class="tree-toggle" id="{node_id}">
      <label for="{node_id}">
        <div class="tree-node-header">
          <svg class="tree-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="9 18 15 12 9 6"></polyline>
          </svg>
          <div style="flex:1; min-width:0;">
            <div style="font-size:13px; font-weight:600; color:#e2e8f0;">{_e(name)}</div>
            {'<div style="font-size:10px; color:#4b5563;">' + _e(file_path) + "</div>" if file_path else ""}
          </div>
          <div class="compliance-bar">
            <div class="compliance-bar-fill" style="width:{bar_width}%; background:{bar_color};"></div>
          </div>
          <div style="text-align:right; min-width:100px;">
            <span style="font-size:13px; font-weight:600; color:{bar_color};">{pct}%</span>
            <span style="font-size:10px; color:#6b7280; margin-left:4px;">{compliant}/{total}</span>
          </div>
        </div>
      </label>
      <div class="tree-children">
        <div class="tree-resources">"""

    # If module has children (nested sub-modules), group by child
    children = mod.get("children", {})
    if children:
        # Resources directly in the top module
        for child_key, resources in sorted(children.items()):
            # Show child module header, then its resources
            html += f"""
          <div class="tree-child-header">{_e(child_key)}</div>"""
            for r in resources:
                html += _render_resource_row(r)
    else:
        # Flat — all resources belong directly to this module
        for r in mod.get("resources", []):
            html += _render_resource_row(r)

    html += """
        </div>
      </div>
    </div>"""

    return html


def _render_resource_row(r):
    """Render a single resource row within a module."""
    compliant = r["compliant"]
    dot_class = "tree-dot-ok" if compliant else "tree-dot-bad"
    rtype = r.get("type", "")
    name = r.get("name", "")
    arn = r.get("arn", "")
    rid = r.get("id", "")
    r.get("tf_address", "")
    icon = TYPE_ICONS.get(_tf_type_to_icon_key(rtype), "")

    # Link to CURE page if non-compliant and has an ARN
    name_display = _e(name) or _e(rtype)
    if not compliant and arn:
        import urllib.parse

        cure_url = f"/cure?arn={urllib.parse.quote(arn, safe='')}"
        name_display = f'<a href="{cure_url}" style="color:#ef4444; text-decoration:none;" title="View CURE page">{name_display}</a>'

    return f"""
          <div class="tree-resource">
            <span class="tree-dot {dot_class}"></span>
            <span style="color:#3b82f6; font-size:11px; min-width:180px;">{_e(rtype)}</span>
            <span style="color:#e2e8f0; flex:1;">{icon} {name_display}</span>
            <span style="color:#4b5563; font-size:10px; max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{_e(rid)}</span>
          </div>"""


def _tf_type_to_icon_key(tf_type):
    """Map a Terraform resource type to a TYPE_ICONS key."""
    mapping = {
        "aws_instance": "ec2:instance",
        "aws_ebs_volume": "ec2:volume",
        "aws_db_instance": "rds:instance",
        "aws_ecs_service": "ecs:service",
        "aws_ecs_cluster": "ecs:cluster",
        "aws_lambda_function": "lambda:function",
        "aws_s3_bucket": "s3:bucket",
        "aws_secretsmanager_secret": "secretsmanager:secret",
        "aws_elasticache_cluster": "elasticache:cluster",
        "aws_lb": "elbv2:application",
        "aws_dynamodb_table": "dynamodb:table",
    }
    return mapping.get(tf_type, "")


def render_repo_tree(slug, user_email=""):
    """Render a per-repo tree view using service-type grouping."""
    from data_repo import get_repo_info, get_repo_tree

    info = get_repo_info(slug)
    tree = get_repo_tree(slug)

    repo_name = info.get("repo_slug", slug) if info else slug
    base = f"/repos/{urllib.parse.quote(slug, safe='')}"

    if not tree:
        content = f"""
        <div style="margin-bottom:12px;">
          <a href="/repos" style="color:#6b7280; text-decoration:none; font-size:12px;">&larr; Repositories</a>
          &rsaquo; <a href="{base}" style="color:#6b7280; text-decoration:none; font-size:12px;">{_e(slug)}</a>
        </div>
        <div class="empty">No resources found for <strong>{_e(slug)}</strong>.</div>"""
        return _layout(f"Tree: {slug}", content, user_email, active_page="repos")

    # Aggregate stats
    total_resources = sum(m["stats"]["total"] for m in tree)
    total_compliant = sum(m["stats"]["compliant"] for m in tree)
    total_nc = total_resources - total_compliant
    pct = round(100 * total_compliant / total_resources) if total_resources else 100

    content = f"""
    <div style="margin-bottom:12px;">
      <a href="/repos" style="color:#6b7280; text-decoration:none; font-size:12px;">&larr; Repositories</a>
      &rsaquo; <a href="{base}" style="color:#6b7280; text-decoration:none; font-size:12px;">{_e(slug)}</a>
      &rsaquo; Tree
    </div>

    <h1>{_e(repo_name.upper())} &mdash; RESOURCE TREE</h1>
    <div class="cards" style="margin-bottom:20px;">
      <div class="card">
        <div class="label">SERVICE GROUPS</div>
        <div class="value">{len(tree)}</div>
      </div>
      <div class="card">
        <div class="label">RESOURCES</div>
        <div class="value">{total_resources}</div>
      </div>
      <div class="card">
        <div class="label">COMPLIANT</div>
        <div class="value" style="color:#22c55e;">{total_compliant}</div>
      </div>
      <div class="card">
        <div class="label">NON-COMPLIANT</div>
        <div class="value" style="color:{"#ef4444" if total_nc else "#22c55e"};">{total_nc}</div>
      </div>
      <div class="card">
        <div class="label">COMPLIANCE RATE</div>
        <div class="value" style="color:{_rate_color(pct)};">{pct}%</div>
      </div>
    </div>

    <style>
      .tree-toggle {{ display: none; }}
      .tree-toggle + label {{ cursor: pointer; user-select: none; }}
      .tree-toggle + label + .tree-children {{ max-height: 0; overflow: hidden; transition: max-height 0.3s ease; }}
      .tree-toggle:checked + label + .tree-children {{ max-height: 50000px; }}
      .tree-node {{
        background: #111118; border: 1px solid #1e1e2e; border-radius: 8px;
        margin-bottom: 8px; overflow: hidden; transition: border-color 0.15s;
      }}
      .tree-node:hover {{ border-color: #2a2a3a; }}
      .tree-node-header {{
        display: flex; align-items: center; gap: 12px; padding: 12px 16px;
      }}
      .tree-node-header:hover {{ background: #141420; }}
      .tree-chevron {{
        width: 16px; height: 16px; color: #4b5563; transition: transform 0.2s; flex-shrink: 0;
      }}
      .tree-toggle:checked + label .tree-chevron {{ transform: rotate(90deg); }}
      .compliance-bar {{
        height: 4px; border-radius: 2px; background: #1e1e2e; flex: 1; max-width: 120px; overflow: hidden;
      }}
      .compliance-bar-fill {{ height: 100%; border-radius: 2px; transition: width 0.3s; }}
      .tree-resources {{ padding: 0 16px 12px 44px; }}
      .tree-resource {{
        display: flex; align-items: center; gap: 8px; padding: 4px 0;
        font-size: 12px; border-top: 1px solid #0d0d14;
      }}
      .tree-resource:first-child {{ border-top: none; }}
      .tree-dot {{ width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }}
      .tree-dot-ok {{ background: #22c55e; box-shadow: 0 0 4px rgba(34,197,94,0.4); }}
      .tree-dot-bad {{ background: #ef4444; box-shadow: 0 0 4px rgba(239,68,68,0.4); }}
    </style>

    <div style="margin-bottom:8px; display:flex; gap:12px; align-items:center;">
      <span style="font-size:11px; color:#6b7280;">LEGEND:</span>
      <span style="display:flex;align-items:center;gap:4px;font-size:11px;color:#6b7280;">
        <span class="tree-dot tree-dot-ok" style="display:inline-block;"></span> Compliant
      </span>
      <span style="display:flex;align-items:center;gap:4px;font-size:11px;color:#6b7280;">
        <span class="tree-dot tree-dot-bad" style="display:inline-block;"></span> Non-compliant
      </span>
    </div>
    """

    for i, mod in enumerate(tree):
        content += _render_module_node(mod, i)

    return _layout(f"Tree: {slug}", content, user_email, active_page="repos")

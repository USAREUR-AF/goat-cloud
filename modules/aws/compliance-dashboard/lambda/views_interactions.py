"""
D3 Force-Directed Service Interaction Graph.

Global view: all services with zone-based layout.
Per-service view: focal service with its direct neighborhood.

Accessed at:
  /architecture/interactions              — global (all services)
  /architecture/interactions/<module_addr> — per-service (focal neighborhood)
"""

import re
import urllib.parse
from pathlib import Path

import site_config
from arch_shared import (
    COMPONENT_COLORS,
    EDGE_ID,
    EDGE_NODE,
    EDGE_STYLES,
    NOTABLE_RESOURCE_TYPES,
    ZONE_COLORS,
    get_active_connections,
    get_connection_pair,
    get_module_meta,
    get_service_subgraph,
    get_submodule_display_name,
)
from data import get_compliance_report
from data_terraform import get_tf_module_tree
from views_base import NAV_CSS_FIXED, _e, _js_json, render_nav

# Load JS template once at module level (Lambda cold start).
# The template uses __PLACEHOLDER__ markers for data injection via str.replace(),
# avoiding f-string brace escaping for the entire JS codebase.
_JS_TEMPLATE = (Path(__file__).parent / "interactions_d3.js").read_text()

# ── Zone-based layout anchors (global mode only) ──
# Position zones on the canvas so the graph has meaningful spatial structure.
_ZONE_ANCHORS = {
    "edge": {"x": 0, "y": -350},
    "public": {"x": 0, "y": -200},
    "apps": {"x": -200, "y": 100},
    "identity": {"x": 250, "y": 50},
    "serverless": {"x": 0, "y": 350},
    "security": {"x": -350, "y": 300},
    "infra": {"x": 350, "y": 300},
}

# Per-node overrides for key hub nodes.
_NODE_ANCHORS = {
    "alb": {"x": 0, "y": -150},
    **site_config.get("node_anchors"),
}


# ── Data helpers ──


def _get_module_components(module_address):
    """Extract notable infrastructure components for a module from TF state.

    Returns a list of component dicts: {label, count, color, desc, submodule}.
    """
    report = get_compliance_report()
    tree = get_tf_module_tree(report)

    # Find matching module in tree
    mod_entry = None
    for m in tree:
        if m["module"] == module_address:
            mod_entry = m
            break

    if not mod_entry:
        return []

    # Group resources by notable type, tracking submodule
    groups = {}  # (label, submodule_display) -> {count, color, desc}

    # Collect addresses of child-module resources to avoid double-counting
    # (mod_entry["resources"] is a flattened list that includes children)
    child_addresses = set()
    for resources in mod_entry.get("children", {}).values():
        for res in resources:
            child_addresses.add(res.get("tf_address", ""))

    # Process direct (top-level) resources only
    for res in mod_entry.get("resources", []):
        if res.get("tf_address", "") in child_addresses:
            continue
        rtype = res.get("type", "")
        info = NOTABLE_RESOURCE_TYPES.get(rtype)
        if not info:
            continue
        label = info["label"]
        key = (label, "")
        if key not in groups:
            groups[key] = {"count": 0, "color": COMPONENT_COLORS.get(label, "#6b7280"), "desc": info["desc"]}
        groups[key]["count"] += 1

    # Process child module resources
    for child_addr, resources in mod_entry.get("children", {}).items():
        submod_display = get_submodule_display_name(child_addr)
        for res in resources:
            rtype = res.get("type", "")
            info = NOTABLE_RESOURCE_TYPES.get(rtype)
            if not info:
                continue
            label = info["label"]
            key = (label, submod_display)
            if key not in groups:
                groups[key] = {"count": 0, "color": COMPONENT_COLORS.get(label, "#6b7280"), "desc": info["desc"]}
            groups[key]["count"] += 1

    # Convert to list sorted by submodule then label
    components = []
    for (label, submod), data in sorted(groups.items(), key=lambda x: (x[0][1], x[0][0])):
        components.append(
            {
                "label": label,
                "count": data["count"],
                "color": data["color"],
                "desc": data["desc"],
                "submodule": submod,
            }
        )

    return components


def _build_node(nid, focal_id=""):
    """Build a node dict for the D3 graph."""
    meta = get_module_meta().get(f"module.{nid}", {})
    zone = meta.get("zone", "infra")

    if EDGE_NODE and nid == EDGE_ID:
        meta = {
            "name": EDGE_NODE.get("name", "Edge"),
            "zone": "edge",
            "type": EDGE_NODE.get("graph_type", EDGE_NODE.get("type", "Edge")),
            "icon": EDGE_NODE.get("icon", "shield"),
            "host": "",
            "auth": "",
            "desc": EDGE_NODE.get("graph_desc", EDGE_NODE.get("desc", "")),
        }
        zone = "edge"

    node = {
        "id": nid,
        "name": meta.get("name", nid.replace("_", " ").title()),
        "zone": zone,
        "color": ZONE_COLORS.get(zone, "#6b7280") if zone != "edge" else "#f97316",
        "type": meta.get("type", ""),
        "host": meta.get("host", ""),
        "auth": meta.get("auth", ""),
        "desc": meta.get("desc", ""),
        "icon": meta.get("icon", "ecs"),
        "link": f"/architecture/custom/module.{nid}" if f"module.{nid}" in get_module_meta() else "",
        "focal": nid == focal_id,
    }

    # Attach infrastructure components (if module exists in TF state)
    if f"module.{nid}" in get_module_meta():
        components = _get_module_components(f"module.{nid}")
        if components:
            node["components"] = components

    return node


def _build_edge(conn):
    """Build an edge dict from a CONNECTIONS entry."""
    return {
        "source": conn["from"],
        "target": conn["to"],
        "type": conn.get("type", "http"),
        "label": conn.get("label", ""),
        "protocol": conn.get("protocol", ""),
        "async": conn.get("async", False),
        "weight": conn.get("weight", 1),
    }


# ── Public render functions ──


def render_interactions_global(user_email=""):
    """Render the global D3 interaction graph (all services)."""
    meta = get_module_meta()
    node_ids = set()
    for addr, m in meta.items():
        if m.get("tier") == "config":
            continue
        node_ids.add(addr.replace("module.", ""))
    # Add virtual nodes referenced in active connections
    active_conns = get_active_connections()
    for conn in active_conns:
        src, tgt = get_connection_pair(conn)
        node_ids.add(src)
        node_ids.add(tgt)

    nodes = [_build_node(nid) for nid in sorted(node_ids)]
    edges = [_build_edge(conn) for conn in active_conns]

    data = {"nodes": nodes, "edges": edges, "focal": "", "global": True}
    return _render_page("Service Interactions", data, user_email, global_mode=True)


def render_interactions(module_address, user_email=""):
    """Render a per-service D3 interaction graph."""
    short_name = module_address.replace("module.", "")
    focal_meta = get_module_meta().get(module_address, {})
    focal_display = focal_meta.get("name", short_name.replace("_", " ").title())

    node_ids, subgraph_edges = get_service_subgraph(short_name)

    nodes = [_build_node(nid, focal_id=short_name) for nid in sorted(node_ids)]
    edges = [_build_edge(conn) for conn in subgraph_edges]

    data = {"nodes": nodes, "edges": edges, "focal": short_name, "global": False}
    return _render_page(
        f"{focal_display} Interactions",
        data,
        user_email,
        focal_display=focal_display,
        module_address=module_address,
    )


# ── Page template ──


def _render_page(
    title,
    data,
    user_email,
    focal_display="",
    module_address="",
    global_mode=False,
    title_bar_html="",
    active_page="architecture",
    node_anchors=None,
    banner_html="",
):
    """Render the full HTML page with D3 interaction graph."""
    data_json = _js_json(data, separators=(",", ":"))
    edge_styles_json = _js_json(EDGE_STYLES, separators=(",", ":"))
    zone_anchors_json = _js_json(_ZONE_ANCHORS, separators=(",", ":"))
    node_anchors_json = _js_json(node_anchors or _NODE_ANCHORS, separators=(",", ":"))

    # Build edge type legend (types present in this graph)
    used_types = {e["type"] for e in data["edges"]}
    legend_rows = ""
    for etype, style in EDGE_STYLES.items():
        if etype not in used_types:
            continue
        dash = f"stroke-dasharray='{style['dash']}'" if style["dash"] else ""
        legend_rows += (
            f'  <div class="legend-row">'
            f'<svg width="24" height="8"><line x1="0" y1="4" x2="24" y2="4" '
            f'stroke="{style["color"]}" stroke-width="2" {dash}/></svg>'
            f" {style['label']}</div>\n"
        )

    # Title bar content
    if title_bar_html:
        title_bar = title_bar_html
    elif global_mode:
        title_bar = (
            '<div class="svc-title-bar">'
            '<strong style="color:#e2e8f0;">All Service Interactions</strong>'
            '<span style="color:#374151;">&middot;</span>'
            f'<span style="color:#4b5563;">{len(data["nodes"])} services &middot; '
            f"{len(data['edges'])} connections</span>"
            "</div>"
        )
    else:
        title_bar = (
            '<div class="svc-title-bar">'
            f'<span style="color:#6b7280;">Interactions for</span>'
            f'<strong style="color:#e2e8f0;">{_e(focal_display)}</strong>'
            f'<span style="color:#374151;">&middot;</span>'
            f'<span style="color:#4b5563;">{len(data["nodes"])} nodes &middot; '
            f"{len(data['edges'])} edges</span>"
            f'<a href="/architecture/custom/{_e(urllib.parse.quote(module_address, safe=""))}">&larr; Service view</a>'
            "</div>"
        )

    # Single pass: a placeholder name that appears inside the embedded data
    # must not be substituted a second time (it would land outside a JS string).
    js_values = {
        "__DATA_JSON__": data_json,
        "__EDGE_STYLES_JSON__": edge_styles_json,
        "__ZONE_ANCHORS_JSON__": zone_anchors_json,
        "__NODE_ANCHORS_JSON__": node_anchors_json,
    }
    js_code = re.sub("|".join(js_values), lambda m: js_values[m.group(0)], _JS_TEMPLATE)

    return (
        f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} // __BRAND__</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #050508; color: #e2e8f0;
         font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
         overflow: hidden; }}
  {NAV_CSS_FIXED}
  canvas#bg {{ position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 0; }}
  svg#graph {{ position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 1; }}
  .tooltip {{
    position: fixed; pointer-events: none; background: rgba(8,8,12,0.94);
    backdrop-filter: blur(16px); border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px; padding: 14px 18px; font-size: 12px; max-width: 380px;
    z-index: 200; box-shadow: 0 12px 48px rgba(0,0,0,0.6); opacity: 0;
    transition: opacity 0.12s;
  }}
  .tooltip.visible {{ opacity: 1; }}
  .tooltip .tt-title {{ font-weight: 700; font-size: 14px; margin-bottom: 3px; }}
  .tooltip .tt-type {{ color: #6b7280; font-size: 10px; text-transform: uppercase;
                       letter-spacing: 0.1em; margin-bottom: 8px; }}
  .tooltip .tt-row {{ display: flex; justify-content: space-between; gap: 16px;
                      font-size: 11px; padding: 2px 0; }}
  .tooltip .tt-label {{ color: #6b7280; }}
  .tooltip .tt-value {{ color: #e2e8f0; font-weight: 600; text-align: right; }}
  .legend {{
    position: fixed; bottom: 24px; left: 24px; z-index: 90;
    background: rgba(8,8,12,0.85); backdrop-filter: blur(12px);
    border: 1px solid rgba(255,255,255,0.06); border-radius: 10px;
    padding: 14px 18px; font-size: 11px;
  }}
  .legend-title {{ font-weight: 700; margin-bottom: 6px; color: #94a3b8; font-size: 10px;
                   letter-spacing: 0.1em; }}
  .legend-row {{ display: flex; align-items: center; gap: 8px; margin: 5px 0; color: #6b7280; }}
  .hint-box {{
    position: fixed; bottom: 24px; right: 24px; z-index: 90;
    color: #374151; font-size: 10px; text-align: right; line-height: 1.6;
  }}
  .hint-box kbd {{
    background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06);
    border-radius: 3px; padding: 1px 5px; font-size: 9px;
  }}
  .d3-banner {{
    position: fixed; top: 82px; left: 50%; transform: translateX(-50%);
    z-index: 90; display: flex; flex-direction: column; align-items: center; gap: 6px;
  }}
  .exp-badge {{
    display: inline-block; cursor: pointer; font-size: 10px; font-weight: 600;
    color: #d97706; background: rgba(245,158,11,0.08); border: 1px solid rgba(245,158,11,0.25);
    border-radius: 4px; padding: 2px 10px; letter-spacing: 0.03em;
    transition: all 0.15s;
  }}
  .exp-badge:hover {{ background: rgba(245,158,11,0.14); border-color: rgba(245,158,11,0.4); }}
  .exp-badge.open {{ border-radius: 4px 4px 0 0; border-bottom-color: transparent; }}
  .exp-detail {{
    display: none; max-width: 520px; background: rgba(8,8,12,0.92);
    backdrop-filter: blur(12px); border: 1px solid rgba(245,158,11,0.25);
    border-radius: 0 8px 8px 8px; padding: 10px 14px;
  }}
  .exp-detail.open {{ display: block; }}
  .svc-title-bar {{
    position: fixed; top: 82px; left: 50%; transform: translateX(-50%);
    z-index: 90; display: flex; gap: 10px; align-items: center;
    background: rgba(8,8,12,0.85); backdrop-filter: blur(12px);
    border: 1px solid rgba(255,255,255,0.06); border-radius: 8px;
    padding: 6px 16px; font-size: 12px; transition: top 0.15s;
  }}
  .svc-title-bar a {{
    color: #3b82f6; text-decoration: none; font-size: 11px;
    padding: 2px 8px; border: 1px solid rgba(59,130,246,0.2);
    border-radius: 4px; background: rgba(59,130,246,0.06);
  }}
  .svc-title-bar a:hover {{
    background: rgba(59,130,246,0.12); border-color: rgba(59,130,246,0.4);
  }}
  .filter-bar {{
    position: fixed; top: 118px; left: 50%; transform: translateX(-50%);
    z-index: 90; display: flex; gap: 6px; background: rgba(8,8,12,0.85);
    backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.06);
    border-radius: 8px; padding: 6px 12px; transition: top 0.15s;
  }}
  .filter-pill {{
    font-size: 10px; padding: 3px 10px; border-radius: 4px; cursor: pointer;
    border: 1px solid rgba(255,255,255,0.08); background: transparent;
    font-family: inherit; transition: all 0.15s;
  }}
  .filter-pill:hover {{ border-color: rgba(255,255,255,0.15); }}
  .filter-pill.active {{ font-weight: 600; }}
</style>
</head>
<body>
{render_nav(active_page, user_email)}

<div class="d3-banner" id="d3-banner">{banner_html}</div>

{title_bar}

<div class="filter-bar" id="filter-bar">
  <button class="filter-pill active" data-type="all" onclick="setTypeFilter('all')" style="color:#e2e8f0;">All</button>
</div>

<script>
(function(){{
  var banner=document.getElementById('d3-banner');
  var bar=document.querySelector('.svc-title-bar');
  var fb=document.getElementById('filter-bar');
  if(!banner||!banner.children.length)return;
  function layout(){{
    var h=banner.offsetHeight;
    var top=82+h+6;
    if(bar)bar.style.top=top+'px';
    if(fb)fb.style.top=(top+(bar?bar.offsetHeight+4:0))+'px';
  }}
  window._layoutBanner=function(){{setTimeout(layout,20);}};
  layout();
}})();
</script>

<canvas id="bg"></canvas>
<svg id="graph"></svg>
<div class="tooltip" id="tooltip"></div>

<div class="legend">
  <div class="legend-title">EDGE TYPES</div>
{legend_rows}
  <div style="margin-top:10px;border-top:1px solid rgba(255,255,255,0.06);padding-top:8px;">
    <div class="legend-title">NODES</div>
    <div class="legend-row"><span style="width:12px;height:12px;border-radius:50%;background:#e2e8f0;display:inline-block;border:2px solid #e2e8f0;"></span> Focal service</div>
    <div class="legend-row"><span style="width:10px;height:10px;border-radius:50%;background:#3b82f6;display:inline-block;"></span> Application</div>
    <div class="legend-row"><span style="width:10px;height:10px;border-radius:50%;background:#a855f7;display:inline-block;"></span> Identity</div>
    <div class="legend-row"><span style="width:10px;height:10px;border-radius:50%;background:#f97316;display:inline-block;"></span> Serverless / Edge</div>
    <div class="legend-row"><span style="width:10px;height:10px;border-radius:50%;background:#22c55e;display:inline-block;"></span> Public</div>
    <div class="legend-row"><span style="width:10px;height:10px;border-radius:50%;background:#ef4444;display:inline-block;"></span> Security</div>
    <div class="legend-row"><span style="width:10px;height:10px;border-radius:50%;background:#6b7280;display:inline-block;"></span> Infra</div>
  </div>
</div>

<div class="hint-box">
  <kbd>scroll</kbd> zoom &middot; <kbd>drag</kbd> pan<br>
  <kbd>click</kbd> node: expand components<br>
  <kbd>dblclick</kbd> node: open service detail<br>
  <kbd>R</kbd> reset &middot; <kbd>F</kbd> fit
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js" integrity="sha512-vc58qvvBdrDR4etbxMdlTt4GBQk1qjvyORR2nrsPsFPyrs+/u5c3+1Ct6upOgdZoIl7eq6k3a1UPDSNAQi/32A==" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>
"""
        + js_code
        + """
</script>
</body>
</html>"""
    )


def _build_repo_interaction_data(slug):
    """Build node/edge data for a repo's service interaction graph.

    Returns (nodes, edges, svc_nodes) where svc_nodes is a dict keyed by service key.
    """
    import urllib.parse as _up

    from data_repo import _SERVICE_TYPE_MAP, _service_from_type, get_repo_resources

    resources = get_repo_resources(slug)
    base = f"/repos/{_up.quote(slug, safe='')}"

    if not resources:
        return [], [], {}

    svc_nodes = {}
    for r in resources:
        svc = _service_from_type(r["resource_type"])
        meta = _SERVICE_TYPE_MAP.get(svc, {"label": svc.upper(), "zone": "other"})
        if svc not in svc_nodes:
            zone = meta.get("zone", "other")
            svc_nodes[svc] = {
                "id": svc,
                "name": meta["label"],
                "zone": zone,
                "color": ZONE_COLORS.get(zone, "#6b7280"),
                "type": "",
                "host": "",
                "auth": "",
                "desc": "",
                "icon": meta.get("icon", "ecs"),
                "link": f"{base}/architecture/custom/{_up.quote(svc, safe='')}",
                "focal": False,
                "components": [],
            }
        svc_nodes[svc]["components"].append(
            {
                "label": r["name"] or r["resource_type"],
                "count": 1,
                "color": "#6b7280",
                "desc": r["resource_type"],
                "submodule": "",
            }
        )

    nodes = list(svc_nodes.values())

    # Infer connections between service types present in this repo
    svc_keys = set(svc_nodes.keys())
    _INFERRED = [
        ("apigateway", "lambda", "http", "HTTP invoke"),
        ("lambda", "dynamodb", "data", "Read/Write"),
        ("lambda", "s3", "data", "Read/Write"),
        ("lambda", "sqs", "event", "Send/Receive"),
        ("lambda", "sns", "event", "Publish"),
        ("events", "lambda", "event", "Trigger"),
        ("apigateway", "s3", "http", "Static hosting"),
    ]
    edges = []
    for src, tgt, etype, label in _INFERRED:
        if src in svc_keys and tgt in svc_keys:
            edges.append(
                {
                    "source": src,
                    "target": tgt,
                    "type": etype,
                    "label": label,
                    "protocol": "",
                    "async": etype == "event",
                    "weight": 1,
                }
            )

    return nodes, edges, svc_nodes


_REPO_EXPERIMENTAL_BANNER = (
    '<span class="exp-badge" id="exp-badge" onclick="document.getElementById(\'exp-detail\').classList.toggle(\'open\');'
    "document.getElementById('exp-badge').classList.toggle('open');window._layoutBanner&&window._layoutBanner()\">"
    "&#9888; Experimental</span>"
    '<div class="exp-detail" id="exp-detail">'
    '<span style="color:#94a3b8; line-height:1.5; font-size:12px;">'
    "Generated from live compliance data &mdash; resource counts and compliance"
    " status are accurate. However, service groupings and relationships are"
    " inferred automatically and may not reflect the actual architecture."
    "</span></div>"
)


def render_repo_interactions(slug, user_email=""):
    """Render a per-repo interactive D3 graph, reusing the existing _render_page pipeline."""
    import urllib.parse as _up

    from data_repo import get_repo_info

    info = get_repo_info(slug)
    repo_name = info.get("repo_slug", slug) if info else slug
    base = f"/repos/{_up.quote(slug, safe='')}"

    nodes, edges, _svc_nodes = _build_repo_interaction_data(slug)

    if not nodes:
        return _render_page(
            f"{repo_name} Interactions",
            {"nodes": [], "edges": [], "focal": "", "global": True},
            user_email,
            active_page="repos",
            node_anchors={},
            banner_html=_REPO_EXPERIMENTAL_BANNER,
        )

    data = {"nodes": nodes, "edges": edges, "focal": "", "global": True}

    title_bar = (
        '<div class="svc-title-bar">'
        f'<a href="{_e(base)}/architecture">&larr; Architecture</a>'
        f'<span style="color:#374151;">&middot;</span>'
        f'<strong style="color:#e2e8f0;">{_e(repo_name)} Interactions</strong>'
        f'<span style="color:#374151;">&middot;</span>'
        f'<span style="color:#4b5563;">{len(nodes)} services &middot; '
        f"{len(edges)} connections</span>"
        "</div>"
    )

    return _render_page(
        f"{repo_name} Interactions",
        data,
        user_email,
        title_bar_html=title_bar,
        active_page="repos",
        node_anchors={},
        banner_html=_REPO_EXPERIMENTAL_BANNER,
    )


def render_repo_interactions_focal(slug, service_type, user_email=""):
    """Render a focal per-repo interaction graph centered on one service type."""
    import urllib.parse as _up

    from data_repo import _SERVICE_TYPE_MAP, get_repo_info

    info = get_repo_info(slug)
    repo_name = info.get("repo_slug", slug) if info else slug
    base = f"/repos/{_up.quote(slug, safe='')}"

    meta = _SERVICE_TYPE_MAP.get(service_type, {"label": service_type.upper(), "icon": "ecs", "zone": "other"})
    focal_display = meta["label"]

    nodes, edges, svc_nodes = _build_repo_interaction_data(slug)

    if service_type not in svc_nodes:
        return _render_page(
            f"{focal_display} — {repo_name}",
            {"nodes": [], "edges": [], "focal": "", "global": True},
            user_email,
            active_page="repos",
            node_anchors={},
            banner_html=_REPO_EXPERIMENTAL_BANNER,
        )

    # Filter to focal node + 1-hop neighbors
    neighbors = {service_type}
    for e in edges:
        if e["source"] == service_type:
            neighbors.add(e["target"])
        elif e["target"] == service_type:
            neighbors.add(e["source"])

    focal_nodes = []
    for n in nodes:
        if n["id"] in neighbors:
            n = dict(n)
            n["focal"] = n["id"] == service_type
            focal_nodes.append(n)

    focal_edges = [e for e in edges if e["source"] in neighbors and e["target"] in neighbors]

    data = {
        "nodes": focal_nodes,
        "edges": focal_edges,
        "focal": service_type,
        "global": False,
    }

    title_bar = (
        '<div class="svc-title-bar">'
        f'<a href="{_e(base)}/architecture/interactions">&larr; All Interactions</a>'
        f'<span style="color:#374151;">&middot;</span>'
        f'<span style="color:#6b7280;">Interactions for</span>'
        f'<strong style="color:#e2e8f0;">{_e(focal_display)}</strong>'
        f'<span style="color:#374151;">&middot;</span>'
        f'<span style="color:#4b5563;">{len(focal_nodes)} nodes &middot; '
        f"{len(focal_edges)} edges</span>"
        f'<a href="{_e(base)}/architecture/custom/{_up.quote(service_type, safe="")}">'
        f"&larr; Service view</a>"
        "</div>"
    )

    return _render_page(
        f"{focal_display} — {repo_name} Interactions",
        data,
        user_email,
        title_bar_html=title_bar,
        active_page="repos",
        node_anchors={},
        banner_html=_REPO_EXPERIMENTAL_BANNER,
    )

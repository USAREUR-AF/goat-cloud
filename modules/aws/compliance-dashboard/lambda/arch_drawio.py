"""
Architecture Diagram — draw.io XML export.

Generates an mxGraphModel XML file from the shared architecture metadata
(MODULE_META, CONNECTIONS) enriched with tag-derived arch metadata from TF
state.  The output is a complete draw.io XML document that can be opened
directly in draw.io / diagrams.net for manual refinement.

Zone swimlanes group modules by architectural zone.  Connections are drawn
between modules.  Modules with visible=false are excluded.  Auth badge text
is appended as a label suffix.

The auto-layout arranges modules in a grid within each zone, with zones
stacked vertically.  The user is expected to refine positions in draw.io
after import.
"""

import html
from xml.sax.saxutils import escape as _sax_escape

from arch_shared import EDGE_ID, EDGE_NODE, get_active_connections, get_connection_pair, get_module_meta
from data import get_compliance_report, get_tf_module_tree


def xml_escape(text):
    """Escape text for an XML attribute value (also escapes the double quote)."""
    return _sax_escape(str(text), {'"': "&quot;"})


# ── Zone configuration — colors and labels ────────────────────────────────
_ZONE_CONFIG = {
    "edge": {"color": "#f59e0b", "label": "Internet Edge"},
    "public": {"color": "#ef4444", "label": "Public Subnet"},
    "apps": {"color": "#3b82f6", "label": "Applications (Private)"},
    "identity": {"color": "#a855f7", "label": "Identity Stack"},
    "serverless": {"color": "#22c55e", "label": "Serverless"},
    "security": {"color": "#6b7280", "label": "Security & Governance"},
    "infra": {"color": "#475569", "label": "Infrastructure"},
}

# Order zones are laid out top-to-bottom
_ZONE_ORDER = ["edge", "public", "apps", "identity", "serverless", "security", "infra"]

# ── AWS-style shapes for draw.io ──────────────────────────────────────────
# Maps canonical icon keys to draw.io shape styles.  Where possible, use the
# built-in AWS shapes from draw.io's AWS 19 library.
_ICON_TO_SHAPE = {
    "alb": "shape=mxgraph.aws4.application_load_balancer;",
    "ecs": "shape=mxgraph.aws4.ecs_service;",
    "ec2": "shape=mxgraph.aws4.ec2_instances;",
    "lambda": "shape=mxgraph.aws4.lambda_function;",
    "redis": "shape=mxgraph.aws4.elasticache_for_redis;",
    "vpn": "shape=mxgraph.aws4.client_vpn;",
    "trail": "shape=mxgraph.aws4.cloudtrail;",
    "shield": "shape=mxgraph.aws4.certificate_manager_3;",
    "monitoring": "shape=mxgraph.aws4.cloudwatch;",
    "guardduty": "shape=mxgraph.aws4.guardduty;",
    "waf": "shape=mxgraph.aws4.waf;",
    "vpc": "shape=mxgraph.aws4.vpc;",
    "cloudflare": "shape=mxgraph.aws4.cloud_alt;",
}

# Fallback for role-based shape selection
_ROLE_TO_ICON = {
    "service": "ecs",
    "loadbalancer": "alb",
    "database": "redis",
    "function": "lambda",
    "network": "vpc",
    "certificate": "shield",
    "monitoring": "monitoring",
    "storage": "ecs",
}

# ── Layout constants ──────────────────────────────────────────────────────
_NODE_W = 160  # Module node width
_NODE_H = 60  # Module node height
_GRID_PAD_X = 30  # Horizontal padding between nodes in a zone
_GRID_PAD_Y = 20  # Vertical padding between rows in a zone
_ZONE_PAD_X = 20  # Internal padding inside zone swimlane
_ZONE_PAD_Y = 40  # Internal padding (top includes header)
_ZONE_GAP = 30  # Gap between zone swimlanes
_COLS = 4  # Number of columns in the node grid
_ZONE_HEADER_H = 30  # Height of the swimlane header bar
_START_X = 40  # Left offset for first zone
_START_Y = 40  # Top offset for first zone


def _node_id(mod_key):
    """Convert module.xxx to a short connection-compatible ID.

    This must match the IDs used in CONNECTIONS (e.g. 'alb', 'wiki').
    """
    return mod_key.replace("module.", "")


def _cell_id(prefix, name):
    """Generate a unique cell ID for draw.io XML."""
    safe = name.replace(".", "_").replace("-", "_").replace("(", "").replace(")", "")
    return f"{prefix}_{safe}"


def _build_effective_meta(live_modules, module_arch):
    """Build effective metadata per module, merging MODULE_META with tag-derived arch.

    Filters out invisible modules and the synthetic (root) module.
    Returns dict of mod_key -> meta dict.
    """
    effective = {}
    for mod_key in live_modules:
        if mod_key == "(root)":
            continue
        arch = module_arch.get(mod_key, {})
        if not arch.get("visible", True):
            continue

        base = get_module_meta().get(mod_key)
        if base:
            meta = dict(base)
        else:
            meta = {
                "name": mod_key.replace("module.", ""),
                "zone": "infra",
                "icon": "ecs",
                "type": "",
                "host": "",
                "auth": "",
                "desc": "",
            }

        # Tag-derived zone overrides MODULE_META
        arch_zone = arch.get("zone")
        if arch_zone is not None:
            meta["zone"] = arch_zone

        # Tag-derived role overrides icon
        arch_role = arch.get("role")
        if arch_role is not None:
            meta["icon"] = _ROLE_TO_ICON.get(arch_role, meta.get("icon", "ecs"))

        effective[mod_key] = meta

    return effective


def _build_label(meta):
    """Build the display label for a module node.

    Includes name, type, host, and auth badge when present.
    Uses HTML-like label with line breaks for draw.io.
    """
    parts = [meta["name"]]
    rtype = meta.get("type", "")
    if rtype:
        parts.append(f"({rtype})")
    host = meta.get("host", "")
    if host:
        parts.append(host)
    auth = meta.get("auth", "")
    if auth:
        parts.append(f"[{auth}]")
    return "\n".join(parts)


def generate_drawio_xml(user_email=""):
    """Generate a draw.io mxGraphModel XML string from TF state.

    Returns raw XML string suitable for saving as a .drawio file.
    """
    try:
        report = get_compliance_report()
        tree = get_tf_module_tree(report)
    except Exception as e:
        return _error_xml(f"Failed to load data: {e}")

    if not tree:
        return _error_xml("No modules found in Terraform state.")

    # Collect live module data
    live_modules = set()
    module_stats = {}
    module_arch = {}
    for mod in tree:
        live_modules.add(mod["module"])
        module_stats[mod["module"]] = mod["stats"]
        module_arch[mod["module"]] = mod.get("arch") or {}

    effective_meta = _build_effective_meta(live_modules, module_arch)

    # The deployment's internet edge node, when it declares one
    edge_meta = None
    if EDGE_NODE:
        edge_meta = {
            "name": EDGE_NODE.get("name", "Edge"),
            "zone": "edge",
            "icon": EDGE_NODE.get("icon", "shield"),
            "type": EDGE_NODE.get("type", "Edge"),
            "host": EDGE_NODE.get("host", ""),
            "auth": "",
            "desc": EDGE_NODE.get("desc", ""),
        }

    # Group modules by zone
    zones = {}  # zone_key -> list of (mod_key, meta)
    for mod_key, meta in effective_meta.items():
        zone = meta["zone"]
        if zone not in zones:
            zones[zone] = []
        zones[zone].append((mod_key, meta))

    if edge_meta:
        zones.setdefault("edge", []).insert(0, (EDGE_ID, edge_meta))

    # Sort modules within each zone by name
    for zone_key in zones:
        zones[zone_key].sort(key=lambda x: x[1]["name"])

    # ── Build XML cells ───────────────────────────────────────────────
    cells = []
    # Root cells required by mxGraphModel
    cells.append('      <mxCell id="0"/>')
    cells.append('      <mxCell id="1" parent="0"/>')

    cell_id_counter = 2
    node_cell_ids = {}  # connection_id -> cell_id (for edges)
    zone_cell_ids = {}  # zone_key -> cell_id

    y_cursor = _START_Y

    for zone_key in _ZONE_ORDER:
        if zone_key not in zones:
            continue

        mods = zones[zone_key]
        zconf = _ZONE_CONFIG.get(zone_key, {"color": "#6b7280", "label": zone_key})

        # Calculate zone dimensions based on grid layout
        num_mods = len(mods)
        cols = min(_COLS, num_mods) if num_mods > 0 else 1
        rows = (num_mods + cols - 1) // cols

        zone_inner_w = cols * _NODE_W + (cols - 1) * _GRID_PAD_X
        zone_inner_h = rows * _NODE_H + (rows - 1) * _GRID_PAD_Y

        zone_w = zone_inner_w + 2 * _ZONE_PAD_X
        zone_h = zone_inner_h + _ZONE_PAD_Y + _ZONE_HEADER_H + _ZONE_PAD_X

        # Zone swimlane cell
        zone_cid = str(cell_id_counter)
        cell_id_counter += 1
        zone_cell_ids[zone_key] = zone_cid

        color = zconf["color"]
        label = xml_escape(zconf["label"])

        cells.append(
            f'      <mxCell id="{zone_cid}" value="{label}" '
            f'style="swimlane;startSize={_ZONE_HEADER_H};'
            f"fillColor={color};fontColor=#ffffff;strokeColor={color};"
            f"swimlaneLine=1;rounded=1;arcSize=8;"
            f'fontStyle=1;fontSize=13;opacity=20;" '
            f'vertex="1" parent="1">'
        )
        cells.append(
            f'        <mxGeometry x="{_START_X}" y="{y_cursor}" width="{zone_w}" height="{zone_h}" as="geometry"/>'
        )
        cells.append("      </mxCell>")

        # Module nodes inside the zone
        for idx, (mod_key, meta) in enumerate(mods):
            col = idx % cols
            row = idx // cols

            node_x = _ZONE_PAD_X + col * (_NODE_W + _GRID_PAD_X)
            node_y = _ZONE_HEADER_H + _GRID_PAD_Y + row * (_NODE_H + _GRID_PAD_Y)

            node_cid = str(cell_id_counter)
            cell_id_counter += 1

            # Map connection ID to cell ID
            conn_id = _node_id(mod_key) if mod_key != EDGE_ID else EDGE_ID
            node_cell_ids[conn_id] = node_cid

            # Node cells use html=1, so draw.io parses the decoded value as HTML:
            # HTML-escape the text first, then escape for the XML attribute.
            label_text = xml_escape(html.escape(_build_label(meta), quote=False))
            # Replace newlines with <br> for draw.io HTML labels
            label_html = label_text.replace("\n", "&lt;br&gt;")

            icon_key = meta.get("icon", "ecs")
            shape_style = _ICON_TO_SHAPE.get(icon_key, "rounded=1;")

            # Non-compliant border
            stats = module_stats.get(mod_key, {})
            nc = stats.get("non_compliant", 0)
            stroke = "strokeColor=#ef4444;strokeWidth=3;dashed=1;" if nc > 0 else "strokeColor=#334155;"

            cells.append(
                f'      <mxCell id="{node_cid}" '
                f'value="{label_html}" '
                f'style="{shape_style}{stroke}'
                f"fillColor=#1e293b;fontColor=#e2e8f0;fontSize=10;"
                f'whiteSpace=wrap;html=1;verticalAlign=middle;spacingTop=2;" '
                f'vertex="1" parent="{zone_cid}">'
            )
            cells.append(
                f'        <mxGeometry x="{node_x}" y="{node_y}" width="{_NODE_W}" height="{_NODE_H}" as="geometry"/>'
            )
            cells.append("      </mxCell>")

        y_cursor += zone_h + _ZONE_GAP

    # ── Connection edges ──────────────────────────────────────────────
    for conn in get_active_connections():
        src_id, tgt_id = get_connection_pair(conn)
        src_cid = node_cell_ids.get(src_id)
        tgt_cid = node_cell_ids.get(tgt_id)
        if not src_cid or not tgt_cid:
            continue

        edge_cid = str(cell_id_counter)
        cell_id_counter += 1

        cells.append(
            f'      <mxCell id="{edge_cid}" value="" '
            f'style="endArrow=classic;strokeColor=#64748b;strokeWidth=1;'
            f"rounded=1;exitX=0.5;exitY=1;exitDx=0;exitDy=0;"
            f'entryX=0.5;entryY=0;entryDx=0;entryDy=0;" '
            f'edge="1" source="{src_cid}" target="{tgt_cid}" parent="1">'
        )
        cells.append('        <mxGeometry relative="1" as="geometry"/>')
        cells.append("      </mxCell>")

    # ── Assemble full XML ─────────────────────────────────────────────
    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<mxfile host="app.diagrams.net" type="device">',
        '  <diagram name="Architecture" id="arch-diagram">',
        '    <mxGraphModel dx="1422" dy="762" grid="1" gridSize="10" '
        'guides="1" tooltips="1" connect="1" arrows="1" fold="1" '
        'page="1" pageScale="1" pageWidth="1654" pageHeight="1169" '
        'math="0" shadow="0">',
        "      <root>",
    ]
    xml_lines.extend(cells)
    xml_lines.extend(
        [
            "      </root>",
            "    </mxGraphModel>",
            "  </diagram>",
            "</mxfile>",
        ]
    )

    return "\n".join(xml_lines)


def _error_xml(message):
    """Return a minimal draw.io XML with an error text box."""
    safe = xml_escape(message)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net" type="device">
  <diagram name="Error" id="error-diagram">
    <mxGraphModel>
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="2" value="{safe}" style="text;fillColor=#ffcccc;strokeColor=#ef4444;fontSize=14;fontColor=#991b1b;rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="400" height="60" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""

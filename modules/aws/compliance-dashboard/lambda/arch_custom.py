"""
Custom Architecture Diagram — containment-style HTML/CSS infrastructure map.

Approach A: Zero external dependencies. Pure Python generating a self-contained
HTML page with inline SVG icons and CSS-only layout. Shows the infrastructure
grouped by network zones (Internet Edge, VPC Public/Private, Serverless,
Security, Shared Infra) with compliance indicators from the scanner report.

Only modules present in TF state are displayed. Missing state = error page.
"""

import urllib.parse

from arch_shared import EDGE_ID, EDGE_NODE, get_module_meta
from cost_display import (
    HINT_CSS_FSTR,
    HINT_JS_FSTR,
    fmt_cost_badge,
)
from cost_display import (
    cost_tier_color as _cost_tier_color,
)
from cost_display import (
    fmt_cost as _fmt_cost,
)
from data_core import REGION
from data_reports import get_compliance_report
from data_terraform import _load_tf_state, get_tf_module_tree
from views_base import ENVIRONMENT, NAV_CSS, _e, render_nav_arch

# Zone display order, labels, colors, and border colors
_ZONE_CONFIG = {
    "edge": {
        "label": "Internet Edge",
        "color": "#f97316",
        "bg": "rgba(249,115,22,0.06)",
        "border": "rgba(249,115,22,0.35)",
    },
    "public": {
        "label": "Public Subnets",
        "color": "#22c55e",
        "bg": "rgba(34,197,94,0.06)",
        "border": "rgba(34,197,94,0.35)",
    },
    "apps": {
        "label": "Applications",
        "color": "#3b82f6",
        "bg": "rgba(59,130,246,0.06)",
        "border": "rgba(59,130,246,0.35)",
    },
    "identity": {
        "label": "Identity Stack",
        "color": "#a855f7",
        "bg": "rgba(168,85,247,0.06)",
        "border": "rgba(168,85,247,0.35)",
    },
    "serverless": {
        "label": "AWS Serverless",
        "color": "#f97316",
        "bg": "rgba(249,115,22,0.06)",
        "border": "rgba(249,115,22,0.35)",
    },
    "security": {
        "label": "AWS Security & Monitoring",
        "color": "#ef4444",
        "bg": "rgba(239,68,68,0.06)",
        "border": "rgba(239,68,68,0.35)",
    },
    "infra": {
        "label": "Utility Resources",
        "color": "#6b7280",
        "bg": "rgba(107,114,128,0.06)",
        "border": "rgba(107,114,128,0.35)",
    },
    "parked": {
        "label": "Parked Resources",
        "color": "#d97706",
        "bg": "rgba(217,119,6,0.03)",
        "border": "rgba(217,119,6,0.25)",
    },
}

# Maps arch role tag values to icon keys used in _ICONS below.
# Falls back to "ecs" for unknown roles.
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

# ─────────────────────────────────────────────────────────────────────────────
# Inline SVG icons (32x32 viewBox, simple recognizable shapes)
# ─────────────────────────────────────────────────────────────────────────────
_ICONS = {
    "alb": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M16 2L30 16L16 30L2 16Z" stroke="{color}" stroke-width="1.5" fill="none"/>'
        '<line x1="16" y1="8" x2="16" y2="24" stroke="{color}" stroke-width="1.5"/>'
        '<line x1="8" y1="16" x2="24" y2="16" stroke="{color}" stroke-width="1.5"/>'
        "</svg>"
    ),
    "ecs": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="4" y="6" width="24" height="20" rx="2" stroke="{color}" stroke-width="1.5" fill="none"/>'
        '<rect x="8" y="10" width="7" height="5" rx="1" stroke="{color}" stroke-width="1" fill="{color}" fill-opacity="0.2"/>'
        '<rect x="17" y="10" width="7" height="5" rx="1" stroke="{color}" stroke-width="1" fill="{color}" fill-opacity="0.2"/>'
        '<rect x="8" y="18" width="7" height="5" rx="1" stroke="{color}" stroke-width="1" fill="{color}" fill-opacity="0.2"/>'
        "</svg>"
    ),
    "ec2": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="6" y="4" width="20" height="24" rx="2" stroke="{color}" stroke-width="1.5" fill="none"/>'
        '<line x1="6" y1="10" x2="26" y2="10" stroke="{color}" stroke-width="1"/>'
        '<line x1="6" y1="16" x2="26" y2="16" stroke="{color}" stroke-width="1"/>'
        '<line x1="6" y1="22" x2="26" y2="22" stroke="{color}" stroke-width="1"/>'
        '<circle cx="10" cy="7" r="1.2" fill="{color}"/>'
        '<circle cx="10" cy="13" r="1.2" fill="{color}"/>'
        '<circle cx="10" cy="19" r="1.2" fill="{color}"/>'
        "</svg>"
    ),
    "lambda": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M8 26L16 6L24 26" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '<line x1="5" y1="26" x2="27" y2="26" stroke="{color}" stroke-width="1.5" stroke-linecap="round"/>'
        "</svg>"
    ),
    "redis": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M16 4L28 10V22L16 28L4 22V10Z" stroke="{color}" stroke-width="1.5" fill="none"/>'
        '<circle cx="16" cy="16" r="5" stroke="{color}" stroke-width="1.5" fill="{color}" fill-opacity="0.15"/>'
        "</svg>"
    ),
    "vpn": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="9" y="14" width="14" height="14" rx="2" stroke="{color}" stroke-width="1.5" fill="none"/>'
        '<path d="M12 14V10a4 4 0 018 0v4" stroke="{color}" stroke-width="1.5" fill="none"/>'
        '<circle cx="16" cy="21" r="2" fill="{color}"/>'
        "</svg>"
    ),
    "trail": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M4 24C8 24 10 10 16 10C22 10 24 24 28 24" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round"/>'
        '<circle cx="16" cy="10" r="2.5" fill="{color}" fill-opacity="0.3" stroke="{color}" stroke-width="1"/>'
        "</svg>"
    ),
    "shield": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M16 3L27 8V16C27 22 22 27 16 29C10 27 5 22 5 16V8Z" stroke="{color}" stroke-width="1.5" fill="{color}" fill-opacity="0.1"/>'
        "</svg>"
    ),
    "monitoring": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="3" y="5" width="26" height="18" rx="2" stroke="{color}" stroke-width="1.5" fill="none"/>'
        '<polyline points="7,18 12,12 16,16 20,9 25,14" stroke="{color}" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '<line x1="10" y1="27" x2="22" y2="27" stroke="{color}" stroke-width="1.5" stroke-linecap="round"/>'
        "</svg>"
    ),
    "guardduty": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M16 3L27 8V16C27 22 22 27 16 29C10 27 5 22 5 16V8Z" stroke="{color}" stroke-width="1.5" fill="{color}" fill-opacity="0.1"/>'
        '<circle cx="16" cy="15" r="3.5" stroke="{color}" stroke-width="1.5" fill="none"/>'
        '<circle cx="16" cy="15" r="1.2" fill="{color}"/>'
        "</svg>"
    ),
    "waf": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M16 3L27 8V16C27 22 22 27 16 29C10 27 5 22 5 16V8Z" stroke="{color}" stroke-width="1.5" fill="{color}" fill-opacity="0.1"/>'
        '<polyline points="11,16 14,19 21,12" stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
    ),
    "vpc": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="4" y="4" width="24" height="24" rx="3" stroke="{color}" stroke-width="1.5" stroke-dasharray="4 2" fill="none"/>'
        '<circle cx="16" cy="16" r="4" stroke="{color}" stroke-width="1" fill="{color}" fill-opacity="0.15"/>'
        "</svg>"
    ),
    "cloudflare": (
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M8 22C5 22 3 20 3 17.5S5 13 8 13c0-3.5 3-7 8-7s8 3.5 8 7c3 0 5 2 5 4.5S27 22 24 22Z" stroke="{color}" stroke-width="1.5" fill="{color}" fill-opacity="0.1"/>'
        "</svg>"
    ),
}

# Fallback icon for unknown modules
_ICON_FALLBACK = (
    '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
    '<rect x="4" y="4" width="24" height="24" rx="4" stroke="{color}" stroke-width="1.5" fill="{color}" fill-opacity="0.1"/>'
    '<circle cx="16" cy="16" r="3" fill="{color}" fill-opacity="0.4"/>'
    "</svg>"
)


def _icon_svg(icon_key, color):
    """Return an inline SVG string for the given icon key and color."""
    template = _ICONS.get(icon_key, _ICON_FALLBACK)
    return template.replace("{color}", color)


def _compliance_dot(stats):
    """Return (css_color, label) for a module's compliance stats."""
    total = stats.get("total", 0)
    nc = stats.get("non_compliant", 0)
    if total == 0:
        return "#6b7280", "N/A"
    pct = round(100 * (total - nc) / total)
    if nc == 0:
        return "#22c55e", f"{pct}%"
    if pct >= 70:
        return "#f59e0b", f"{pct}%"
    return "#ef4444", f"{pct}%"


def _esc(text):
    """HTML-escape a string."""
    import html

    return html.escape(str(text)) if text else ""


# ─────────────────────────────────────────────────────────────────────────────
# Shared CSS for zone boxes, service cards, stats, and legends.
# Imported by arch_repo.py so per-repo views render with the same visual
# language as the platform repository's architecture diagram.
# ─────────────────────────────────────────────────────────────────────────────

ZONE_CARD_CSS = """
  /* Main container */
  .arch-container {
    max-width: 1200px; margin: 0 auto; padding: 24px 24px 64px;
  }

  /* Stats strip */
  .stats-strip {
    display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap;
  }
  .stat-pill {
    background: #0d0d14; border: 1px solid #1e1e2e; border-radius: 6px;
    padding: 8px 16px; display: flex; align-items: center; gap: 8px;
    font-size: 12px;
  }
  .stat-pill .stat-label { color: #6b7280; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; }
  .stat-pill .stat-value { font-weight: 700; font-size: 16px; }

  /* Zone boxes */
  .zone-box {
    border: 1px solid; border-radius: 10px; margin-bottom: 10px;
    overflow: hidden; transition: border-color 0.2s;
  }
  .zone-box:hover { filter: brightness(1.02); }
  .zone-header {
    padding: 10px 16px; display: flex; align-items: center; gap: 10px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }
  .zone-header .zone-dot {
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  }
  .zone-header .zone-label {
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.1em;
  }
  .zone-header .zone-count {
    margin-left: auto; font-size: 10px; color: #4b5563;
  }
  .zone-body {
    padding: 10px; display: flex; flex-wrap: wrap; gap: 8px;
  }

  /* Service cards */
  .svc-card {
    background: #0a0a10; border: 1px solid #1a1a28; border-radius: 8px;
    padding: 10px; min-width: 180px; flex: 0 1 auto; display: flex; gap: 8px;
    transition: all 0.15s; position: relative; cursor: pointer;
    text-decoration: none; color: inherit;
  }
  .svc-card:hover {
    border-color: #2a2a3a; background: #0e0e16;
    transform: translateY(-1px);
    box-shadow: 0 4px 16px rgba(0,0,0,0.3);
  }
  .svc-icon {
    width: 36px; height: 36px; border-radius: 6px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    padding: 4px;
  }
  .svc-icon svg { width: 24px; height: 24px; }
  .svc-info { flex: 1; min-width: 0; padding-right: 52px; }
  .svc-name {
    font-size: 13px; font-weight: 600; color: #e2e8f0;
    white-space: nowrap;
  }
  .svc-host {
    font-size: 10px; color: #4b5563; margin-top: 1px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .svc-desc {
    font-size: 10px; color: #374131; margin-top: 3px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .svc-badges {
    display: flex; gap: 4px; margin-top: 5px; flex-wrap: wrap;
  }
  .svc-badge {
    display: inline-block; padding: 1px 6px; border-radius: 3px;
    font-size: 9px; font-weight: 600; letter-spacing: 0.04em;
  }
  .svc-badge-type {
    background: rgba(59,130,246,0.10); color: #60a5fa;
    border: 1px solid rgba(59,130,246,0.2);
  }
  .svc-badge-auth {
    background: rgba(168,85,247,0.12); color: #a855f7;
    border: 1px solid rgba(168,85,247,0.2);
  }
  .svc-badge-count {
    background: rgba(107,114,128,0.12); color: #6b7280;
    border: 1px solid rgba(107,114,128,0.2);
  }

  /* Compliance dot on card */
  .svc-compliance {
    position: absolute; top: 8px; right: 8px;
    width: 8px; height: 8px; border-radius: 50%;
  }

  /* Visit service button */
  .svc-ext-link {
    position: absolute; top: 6px; right: 22px;
    color: #4b5563; text-decoration: none; padding: 2px 6px;
    border-radius: 3px; transition: all 0.15s; z-index: 2;
    display: flex; align-items: center; justify-content: center;
    border: 1px solid rgba(255,255,255,0.08);
    background: rgba(255,255,255,0.03);
    font-size: 8px; font-weight: 600; letter-spacing: 0.04em;
    opacity: 0.5;
  }
  .svc-card:hover .svc-ext-link { opacity: 1; }
  .svc-ext-link:hover {
    color: #3b82f6; background: rgba(59,130,246,0.1);
    border-color: rgba(59,130,246,0.3); opacity: 1;
  }

  /* Connection lines (decorative) */
  .conn-line {
    width: 2px; height: 12px; margin: 0 auto;
    background: linear-gradient(to bottom, rgba(255,255,255,0.08), rgba(255,255,255,0.02));
    border-radius: 1px;
  }

  /* Legend */
  .legend {
    margin-top: 24px; padding: 16px; background: #0a0a10;
    border: 1px solid #1e1e2e; border-radius: 8px;
    display: flex; gap: 24px; flex-wrap: wrap; align-items: center;
  }
  .legend-title {
    font-size: 10px; font-weight: 700; color: #6b7280;
    text-transform: uppercase; letter-spacing: 0.1em;
  }
  .legend-item {
    display: flex; align-items: center; gap: 6px; font-size: 11px; color: #6b7280;
  }
  .legend-dot {
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  }

  /* Responsive */
  @media (max-width: 900px) {
    .svc-card { flex: 1 1 100%; width: 100%; }
    .stats-strip { gap: 8px; }
  }
"""

# ─────────────────────────────────────────────────────────────────────────────
# Main render function
# ─────────────────────────────────────────────────────────────────────────────


def render_architecture_custom(user_email=""):
    """Render a self-contained HTML architecture diagram.

    Loads TF state and compliance report to determine which modules exist
    and their compliance status, then renders a containment diagram grouped
    by network zones.
    """
    env_upper = ENVIRONMENT.upper()

    # Load data
    try:
        state = _load_tf_state()
        report = get_compliance_report()
        tree = get_tf_module_tree(report)
    except Exception as e:
        return _render_error(env_upper, f"Failed to load infrastructure data: {e}", user_email)

    if not state and not tree:
        return _render_error(
            env_upper, "Terraform state not available. Check TF_STATE_BUCKET and TF_STATE_KEY.", user_email
        )

    # Build lookups: module address -> stats and arch metadata from tree
    module_stats = {}
    module_arch = {}
    for mod in tree:
        module_stats[mod["module"]] = mod["stats"]
        module_arch[mod["module"]] = mod.get("arch") or {}

    # Determine which modules are active (present in TF state)
    active_modules = set(module_stats.keys())

    # Classify modules into zones
    # Tag-derived arch metadata takes priority over get_module_meta() fallback.
    # Modules with arch.visible == False are filtered out entirely.
    zones = {
        "public": [],
        "apps": [],
        "identity": [],
        "serverless": [],
        "security": [],
        "infra": [],
    }

    for mod_addr in sorted(active_modules):
        # Skip synthetic root module (top-level resources, not a real service)
        if mod_addr == "(root)":
            continue
        arch = module_arch.get(mod_addr, {})

        # Filter out invisible modules (arch tag says visible=false)
        if not arch.get("visible", True):
            continue

        meta = get_module_meta().get(mod_addr)
        if meta and meta.get("tier") == "config":
            continue  # Skip config-only modules (e.g., IaC registry entries)
        if meta:
            meta = dict(meta)  # shallow copy so we don't mutate the original
        else:
            # Unknown module -> generic metadata
            meta = {
                "name": mod_addr.replace("module.", ""),
                "zone": "infra",
                "icon": "ecs",
                "desc": "Terraform module",
            }

        # Prefer tag-derived zone over get_module_meta() zone
        arch_zone = arch.get("zone")
        if arch_zone is not None:
            zone = arch_zone
            meta["zone"] = arch_zone
        else:
            zone = meta.get("zone", "infra")

        # Prefer tag-derived role as icon key over get_module_meta() icon
        arch_role = arch.get("role")
        if arch_role is not None:
            meta["icon"] = _ROLE_TO_ICON.get(arch_role, meta.get("icon", "ecs"))

        zones.setdefault(zone, []).append((mod_addr, meta, module_stats.get(mod_addr, {})))

    # Aggregate stats (over all modules, including invisible ones — they're still real resources)
    total_resources = sum(m["stats"]["total"] for m in tree)
    total_compliant = sum(m["stats"]["compliant"] for m in tree)
    total_nc = total_resources - total_compliant
    overall_pct = round(100 * total_compliant / total_resources) if total_resources else 100
    total_cost = sum(m["stats"].get("cost", 0) for m in tree)
    total_cost_unknown = sum(m["stats"].get("cost_unknown", 0) for m in tree)

    # Visible module count (for display — excludes arch.visible=false modules)
    visible_module_count = sum(len(mods) for mods in zones.values())

    # ── Build the HTML ──
    page = _page_open(
        env_upper,
        user_email,
        total_resources,
        total_compliant,
        total_nc,
        overall_pct,
        visible_module_count,
        total_cost,
        total_cost_unknown,
    )

    # Internet Edge: shown only when site.json declares an edge node
    page += _render_edge_zone()

    # VPC container with Public + Private columns
    page += _render_vpc_open(_vpc_cidr(state))
    page += _render_zone_box("public", zones.get("public", []))

    # Private subnets: apps + identity side by side
    page += _render_private_open()
    page += _render_zone_box("apps", zones.get("apps", []))
    page += _render_zone_box("identity", zones.get("identity", []))
    page += _render_private_close()

    page += _render_vpc_close()

    # Flow arrows between VPC and serverless zones
    page += _render_flow_arrows()

    # Serverless
    page += _render_zone_box("serverless", zones.get("serverless", []))

    # Security & Monitoring
    page += _render_zone_box("security", zones.get("security", []))

    # Shared Infrastructure
    page += _render_zone_box("infra", zones.get("infra", []))

    # Parked Resources (data-driven — modules with ArchZone=parked tag)
    page += _render_zone_box("parked", zones.get("parked", []))

    page += _page_close()
    return page


# ─────────────────────────────────────────────────────────────────────────────
# HTML generation helpers
# ─────────────────────────────────────────────────────────────────────────────


def _page_open(
    env_upper,
    user_email,
    total_resources,
    total_compliant,
    total_nc,
    overall_pct,
    module_count,
    total_cost=0,
    total_cost_unknown=0,
):
    """Opening HTML with styles, top bar, and stats header."""
    pct_color = "#22c55e" if overall_pct >= 80 else ("#f59e0b" if overall_pct >= 50 else "#ef4444")
    cost_color = _cost_tier_color(total_cost)

    # Cost display: distinguish known from unknown
    if total_cost == 0 and total_cost_unknown > 0:
        cost_html = '<span class="hint" style="color:#4b5563;" data-tip="Scanner has no pricing model for these resources. See burn page for actual charges.">no model</span>'
    else:
        cost_html = f'{_fmt_cost(total_cost)}<span style="font-size:10px;color:#4b5563;">/mo</span>'
        if total_cost_unknown > 0:
            cost_html += (
                f' <span class="hint" style="color:#4b5563;font-size:9px;" '
                f'data-tip="{total_cost_unknown} resource(s) have no pricing model — actual cost may be higher">*</span>'
            )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Architecture // __BRAND__</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    background: #050508; color: #e2e8f0; line-height: 1.5;
    min-height: 100vh;
  }}
  ::selection {{ background: rgba(59,130,246,0.3); }}

  {NAV_CSS}
  .controls button {{
    color: #6b7280; text-decoration: none; font-size: 12px; padding: 6px 12px;
    border: 1px solid rgba(255,255,255,0.06); border-radius: 4px; transition: all 0.15s;
    background: transparent; cursor: pointer; font-family: inherit;
  }}
  .controls button:hover {{ color: #e2e8f0; border-color: #374151; background: rgba(255,255,255,0.03); }}
  .controls .sep {{ color: #1e293b; }}

  /* ── Main container ── */
  .arch-container {{
    max-width: 1200px; margin: 0 auto; padding: 24px 24px 64px;
  }}

  /* ── Stats strip ── */
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

  /* ── Zone boxes ── */
  .zone-box {{
    border: 1px solid; border-radius: 10px; margin-bottom: 10px;
    overflow: hidden; transition: border-color 0.2s;
  }}
  .zone-box:hover {{ filter: brightness(1.02); }}
  .zone-header {{
    padding: 10px 16px; display: flex; align-items: center; gap: 10px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }}
  .zone-header .zone-dot {{
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  }}
  .zone-header .zone-label {{
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.1em;
  }}
  .zone-header .zone-count {{
    margin-left: auto; font-size: 10px; color: #4b5563;
  }}
  .zone-body {{
    padding: 10px; display: flex; flex-wrap: wrap; gap: 8px;
  }}

  /* ── Service cards ── */
  .svc-card {{
    background: #0a0a10; border: 1px solid #1a1a28; border-radius: 8px;
    padding: 10px; min-width: 180px; flex: 0 1 auto; display: flex; gap: 8px;
    transition: all 0.15s; position: relative; cursor: pointer;
    text-decoration: none; color: inherit;
  }}
  .svc-card:hover {{
    border-color: #2a2a3a; background: #0e0e16;
    transform: translateY(-1px);
    box-shadow: 0 4px 16px rgba(0,0,0,0.3);
  }}
  .svc-icon {{
    width: 36px; height: 36px; border-radius: 6px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    padding: 4px;
  }}
  .svc-icon svg {{ width: 24px; height: 24px; }}
  .svc-info {{ flex: 1; min-width: 0; padding-right: 52px; }}
  .svc-name {{
    font-size: 13px; font-weight: 600; color: #e2e8f0;
    white-space: nowrap;
  }}
  .svc-host {{
    font-size: 10px; color: #4b5563; margin-top: 1px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .svc-desc {{
    font-size: 10px; color: #374151; margin-top: 3px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .svc-badges {{
    display: flex; gap: 4px; margin-top: 5px; flex-wrap: wrap;
  }}
  .svc-badge {{
    display: inline-block; padding: 1px 6px; border-radius: 3px;
    font-size: 9px; font-weight: 600; letter-spacing: 0.04em;
  }}
  .svc-badge-type {{
    background: rgba(59,130,246,0.10); color: #60a5fa;
    border: 1px solid rgba(59,130,246,0.2);
  }}
  .svc-badge-auth {{
    background: rgba(168,85,247,0.12); color: #a855f7;
    border: 1px solid rgba(168,85,247,0.2);
  }}
  .svc-badge-count {{
    background: rgba(107,114,128,0.12); color: #6b7280;
    border: 1px solid rgba(107,114,128,0.2);
  }}

  /* Compliance dot on card */
  .svc-compliance {{
    position: absolute; top: 8px; right: 8px;
    width: 8px; height: 8px; border-radius: 50%;
  }}

  /* Visit service button (top-right, next to compliance dot) */
  .svc-ext-link {{
    position: absolute; top: 6px; right: 22px;
    color: #4b5563; text-decoration: none; padding: 2px 6px;
    border-radius: 3px; transition: all 0.15s; z-index: 2;
    display: flex; align-items: center; justify-content: center;
    border: 1px solid rgba(255,255,255,0.08);
    background: rgba(255,255,255,0.03);
    font-size: 8px; font-weight: 600; letter-spacing: 0.04em;
    opacity: 0.5;
  }}
  .svc-card:hover .svc-ext-link {{ opacity: 1; }}
  .svc-ext-link:hover {{
    color: #3b82f6; background: rgba(59,130,246,0.1);
    border-color: rgba(59,130,246,0.3); opacity: 1;
  }}

  /* ── VPC wrapper ── */
  .vpc-box {{
    border: 2px solid rgba(34,197,94,0.25); border-radius: 12px;
    margin-bottom: 10px; overflow: hidden;
    background: rgba(34,197,94,0.02);
  }}
  .vpc-header {{
    padding: 10px 16px; display: flex; align-items: center; gap: 10px;
    border-bottom: 1px solid rgba(34,197,94,0.12);
    background: rgba(34,197,94,0.04);
  }}
  .vpc-header .vpc-label {{
    font-size: 13px; font-weight: 700; color: #22c55e;
    letter-spacing: 0.06em;
  }}
  .vpc-header .vpc-cidr {{
    font-size: 11px; color: #4b5563; margin-left: 8px;
  }}
  .vpc-header .vpc-region {{
    margin-left: auto; font-size: 10px; color: #374151;
  }}
  .vpc-body {{
    padding: 10px;
  }}

  /* ── Private subnets wrapper (side-by-side apps + identity) ── */
  .private-subnets {{
    display: flex; gap: 10px; margin-top: 10px;
  }}
  .private-subnets > .zone-box:first-child {{
    flex: 3; min-width: 0;
  }}
  .private-subnets > .zone-box:last-child {{
    flex: 2; min-width: 0;
  }}

  /* ── Edge zone (special: no TF module, hardcoded) ── */
  .edge-zone {{
    border: 1px solid rgba(249,115,22,0.3); border-radius: 10px;
    margin-bottom: 16px; background: rgba(249,115,22,0.03);
    overflow: hidden;
  }}
  .edge-header {{
    padding: 10px 16px; display: flex; align-items: center; gap: 10px;
    border-bottom: 1px solid rgba(249,115,22,0.1);
    background: rgba(249,115,22,0.04);
  }}
  .edge-header .zone-dot {{ background: #f97316; }}
  .edge-features {{
    padding: 12px 16px; display: flex; gap: 16px; flex-wrap: wrap;
  }}
  .edge-feature {{
    display: flex; align-items: center; gap: 8px;
    font-size: 12px; color: #94a3b8;
  }}
  .edge-feature .ef-icon {{
    width: 28px; height: 28px; border-radius: 6px;
    background: rgba(249,115,22,0.1); display: flex;
    align-items: center; justify-content: center;
    padding: 4px;
  }}
  .edge-feature .ef-icon svg {{ width: 18px; height: 18px; }}

  /* ── Connection lines (decorative) ── */
  .conn-line {{
    width: 2px; height: 12px; margin: 0 auto;
    background: linear-gradient(to bottom, rgba(255,255,255,0.08), rgba(255,255,255,0.02));
    border-radius: 1px;
  }}

  /* ── Legend ── */
  .legend {{
    margin-top: 24px; padding: 16px; background: #0a0a10;
    border: 1px solid #1e1e2e; border-radius: 8px;
    display: flex; gap: 24px; flex-wrap: wrap; align-items: center;
  }}
  .legend-title {{
    font-size: 10px; font-weight: 700; color: #6b7280;
    text-transform: uppercase; letter-spacing: 0.1em;
  }}
  .legend-item {{
    display: flex; align-items: center; gap: 6px; font-size: 11px; color: #6b7280;
  }}
  .legend-dot {{
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  }}

  /* ── Flow arrows ── */
  .flow-arrows-box {{
    border: 1px solid #1e1e2e; border-radius: 10px; margin-bottom: 10px;
    background: #0a0a10; overflow: hidden;
  }}
  .flow-arrows-header {{
    padding: 8px 16px; border-bottom: 1px solid rgba(255,255,255,0.04);
  }}
  .flow-arrows-label {{
    font-size: 10px; font-weight: 700; color: #4b5563;
    text-transform: uppercase; letter-spacing: 0.1em;
  }}
  .flow-arrows-svg {{
    width: 100%; max-width: 600px; display: block;
    margin: 10px auto; padding: 0 12px;
  }}
  .flow-arrows-svg .flow-label {{ font-size: 11px; font-weight: 600; font-family: inherit; }}
  .flow-arrows-svg .flow-sublabel {{ font-size: 9px; font-family: inherit; }}
  .flow-arrows-svg .flow-line {{
    animation: flowPulse 3s ease-in-out infinite;
  }}
  @keyframes flowPulse {{
    0%, 100% {{ opacity: 0.5; }}
    50% {{ opacity: 1; }}
  }}

  /* ── Responsive ── */
  @media (max-width: 900px) {{
    .private-subnets {{ flex-direction: column; }}
    .svc-card {{ flex: 1 1 100%; width: 100%; }}
    .stats-strip {{ gap: 8px; }}
  }}

  {HINT_CSS_FSTR}

  @media print {{
    .topbar, .controls, .breadcrumb {{ display: none !important; }}
    .arch-container {{ padding-top: 0; }}
    body {{ background: #fff; color: #111; }}
    .zone-box {{ break-inside: avoid; }}
  }}
</style>
</head>
<body>
{render_nav_arch("custom", user_email, meta=f"{module_count} modules &middot; {total_resources} resources")}
<div style="position:sticky;top:68px;z-index:75;background:#0a0a10;border-bottom:1px solid rgba(255,255,255,0.03);padding:4px 24px;display:flex;gap:8px;align-items:center;">
  <button onclick="downloadHtml()" style="color:#6b7280;font-size:11px;padding:4px 10px;border:1px solid rgba(255,255,255,0.06);border-radius:4px;background:transparent;cursor:pointer;font-family:inherit;">HTML</button>
  <button onclick="window.print()" style="color:#6b7280;font-size:11px;padding:4px 10px;border:1px solid rgba(255,255,255,0.06);border-radius:4px;background:transparent;cursor:pointer;font-family:inherit;">PDF</button>
  <a href="/architecture/drawio" style="color:#6b7280;font-size:11px;padding:4px 10px;border:1px solid rgba(255,255,255,0.06);border-radius:4px;background:transparent;cursor:pointer;font-family:inherit;text-decoration:none;" title="Experimental — layout needs manual cleanup in draw.io">draw.io (experimental)</a>

</div>
<div class="arch-container">
  <div class="stats-strip">
    <div class="stat-pill">
      <div>
        <div class="stat-label">Modules</div>
        <div class="stat-value" style="color:#3b82f6;">{module_count}</div>
      </div>
    </div>
    <div class="stat-pill">
      <div>
        <div class="stat-label">Resources</div>
        <div class="stat-value" style="color:#e2e8f0;">{total_resources}</div>
      </div>
    </div>
    <div class="stat-pill">
      <div>
        <div class="stat-label">Compliant</div>
        <div class="stat-value" style="color:#22c55e;">{total_compliant}</div>
      </div>
    </div>
    <div class="stat-pill">
      <div>
        <div class="stat-label">Non-Compliant</div>
        <div class="stat-value" style="color:{"#ef4444" if total_nc else "#22c55e"};">{total_nc}</div>
      </div>
    </div>
    <div class="stat-pill">
      <div>
        <div class="stat-label">Compliance</div>
        <div class="stat-value" style="color:{pct_color};">{overall_pct}%</div>
      </div>
    </div>
    <div class="stat-pill">
      <div>
        <div class="stat-label">Est. Monthly</div>
        <div class="stat-value" style="color:{cost_color};">{cost_html}</div>
      </div>
    </div>
  </div>
"""


def _render_edge_zone():
    """Render the Internet Edge zone for the deployment's edge node; nothing when it declares none."""
    if not EDGE_NODE:
        return ""
    edge_name = _e(EDGE_NODE.get("name", "Edge"))
    cf_icon = _icon_svg(EDGE_NODE.get("icon", "shield"), "#f97316")
    waf_icon = _icon_svg("waf", "#f97316")
    shield_icon = _icon_svg("shield", "#f97316")

    return f"""
  <div class="edge-zone" data-module="{_e(EDGE_ID)}">
    <div class="edge-header">
      <span class="zone-dot" style="background:#f97316;"></span>
      <span style="font-size:11px; font-weight:700; color:#f97316; text-transform:uppercase; letter-spacing:0.1em;">Internet Edge</span>
      <span style="margin-left:auto; font-size:10px; color:#4b5563;">{edge_name}</span>
    </div>
    <div class="edge-features">
      <div class="edge-feature">
        <div class="ef-icon">{cf_icon}</div>
        <div>
          <div style="font-size:12px; font-weight:600; color:#e2e8f0;">{edge_name} CDN</div>
          <div style="font-size:10px; color:#4b5563;">Edge caching + Anycast</div>
        </div>
      </div>
      <div class="edge-feature">
        <div class="ef-icon">{waf_icon}</div>
        <div>
          <div style="font-size:12px; font-weight:600; color:#e2e8f0;">WAF</div>
          <div style="font-size:10px; color:#4b5563;">OWASP + managed rules</div>
        </div>
      </div>
      <div class="edge-feature">
        <div class="ef-icon">{shield_icon}</div>
        <div>
          <div style="font-size:12px; font-weight:600; color:#e2e8f0;">DDoS Protection</div>
          <div style="font-size:10px; color:#4b5563;">L3/L4/L7 mitigation</div>
        </div>
      </div>
    </div>
  </div>
  <div class="conn-line"></div>
"""


def _vpc_cidr(state):
    """CIDR block of the first VPC in the Terraform state, or "" when there is none."""
    for resource in (state or {}).get("resources", []):
        if resource.get("type") != "aws_vpc" or resource.get("mode") != "managed":
            continue
        for instance in resource.get("instances", []):
            cidr = (instance.get("attributes") or {}).get("cidr_block")
            if cidr:
                return cidr
    return ""


def _render_vpc_open(cidr=""):
    """Open the VPC containment box, labelled with its CIDR (when known) and the deployment's region."""
    cidr_html = f'<span class="vpc-cidr">{_e(cidr)}</span>' if cidr else ""
    region_html = f'<span class="vpc-region">{_e(REGION)}</span>' if REGION else ""
    return f"""
  <div class="vpc-box">
    <div class="vpc-header">
      <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="#22c55e" stroke-width="1.5">
        <rect x="2" y="2" width="16" height="16" rx="3" stroke-dasharray="3 1.5"/>
      </svg>
      <span class="vpc-label">AWS VPC</span>
      {cidr_html}
      {region_html}
    </div>
    <div class="vpc-body">
"""


def _render_vpc_close():
    """Close the VPC containment box."""
    return """
    </div>
  </div>
  <div class="conn-line"></div>
"""


def _render_flow_arrows():
    """Return HTML for a small SVG flow diagram showing main data paths.

    Displayed between the VPC zone and the serverless zone to illustrate
    the primary traffic and auth flows through the infrastructure.
    """
    return """
  <div class="flow-arrows-box">
    <div class="flow-arrows-header">
      <span class="flow-arrows-label">Data Flow</span>
    </div>
    <svg class="flow-arrows-svg" viewBox="0 0 600 60" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <marker id="fa-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-auto">
          <path d="M0,0 L8,4 L0,8 Z" fill="#4b5563"/>
        </marker>
      </defs>
      <!-- Internet Edge -> ALB -> Applications -->
      <text x="10" y="16" class="flow-label" fill="#f97316">Edge</text>
      <line x1="50" y1="13" x2="90" y2="13" stroke="#4b5563" stroke-width="1.2" marker-end="url(#fa-arrow)" class="flow-line"/>
      <text x="96" y="16" class="flow-label" fill="#22c55e">ALB</text>
      <line x1="125" y1="13" x2="165" y2="13" stroke="#4b5563" stroke-width="1.2" marker-end="url(#fa-arrow)" class="flow-line"/>
      <text x="171" y="16" class="flow-label" fill="#3b82f6">Applications</text>

      <!-- ALB -> Identity Stack -->
      <text x="290" y="16" class="flow-label" fill="#22c55e">ALB</text>
      <line x1="319" y1="13" x2="359" y2="13" stroke="#4b5563" stroke-width="1.2" marker-end="url(#fa-arrow)" class="flow-line"/>
      <text x="365" y="16" class="flow-label" fill="#a855f7">Identity Stack</text>

      <!-- Applications -> Identity (auth flows) -->
      <text x="10" y="46" class="flow-label" fill="#3b82f6">Apps</text>
      <line x1="47" y1="43" x2="87" y2="43" stroke="#4b5563" stroke-width="1.2" stroke-dasharray="4 2" marker-end="url(#fa-arrow)" class="flow-line"/>
      <text x="93" y="46" class="flow-label" fill="#a855f7">Identity</text>
      <text x="150" y="46" class="flow-sublabel" fill="#374151">(auth)</text>

      <!-- Scanner -> Dashboard -->
      <text x="290" y="46" class="flow-label" fill="#f97316">Scanner</text>
      <line x1="346" y1="43" x2="386" y2="43" stroke="#4b5563" stroke-width="1.2" stroke-dasharray="4 2" marker-end="url(#fa-arrow)" class="flow-line"/>
      <text x="392" y="46" class="flow-label" fill="#f97316">Dashboard</text>
    </svg>
  </div>
  <div class="conn-line"></div>
"""


def _render_private_open():
    """Open the private subnets flex container."""
    return """
      <div class="private-subnets">
"""


def _render_private_close():
    """Close the private subnets flex container."""
    return """
      </div>
"""


def _render_zone_box(zone_key, modules, zone_config=None, card_link_fn=None):
    """Render a zone box with its service cards.

    modules is a list of (mod_addr, meta_dict, stats_dict) tuples.
    Pass ``zone_config`` to override the default network-topology zones
    (e.g., per-repo views use service-type zones).
    Pass ``card_link_fn(mod_addr) -> url`` to override card click targets.
    """
    if not modules:
        return ""

    if zone_config is None:
        zone_config = _ZONE_CONFIG
    cfg = (
        zone_config.get(zone_key)
        or zone_config.get("infra")
        or zone_config.get("other")
        or next(iter(zone_config.values()))
    )
    color = cfg["color"]
    bg = cfg["bg"]
    border = cfg["border"]
    label = cfg["label"]
    total_in_zone = sum(s.get("total", 0) for _, _, s in modules)

    html = f"""
      <div class="zone-box" style="border-color:{border}; background:{bg};">
        <div class="zone-header">
          <span class="zone-dot" style="background:{color}; box-shadow: 0 0 6px {color};"></span>
          <span class="zone-label" style="color:{color};">{_esc(label)}</span>
          <span class="zone-count">{len(modules)} services &middot; {total_in_zone} resources</span>
        </div>
        <div class="zone-body">
"""
    for mod_addr, meta, stats in modules:
        card_link = card_link_fn(mod_addr) if card_link_fn else None
        html += _render_service_card(mod_addr, meta, stats, color, link=card_link)

    html += """
        </div>
      </div>
"""
    return html


def _render_service_card(mod_addr, meta, stats, zone_color, link=None):
    """Render a single service card within a zone.

    Can be called from per-repo views by passing a custom ``link``.
    """
    name = meta.get("name", mod_addr)
    icon_key = meta.get("icon", "ecs")
    host = meta.get("host", "")
    auth = meta.get("auth", "")
    desc = meta.get("desc", "")
    rtype = meta.get("type", "")
    total = stats.get("total", 0)
    nc = stats.get("non_compliant", 0)
    cost = stats.get("cost", 0)
    cost_unknown = stats.get("cost_unknown", 0)

    # Compliance dot color
    dot_color, pct_label = _compliance_dot(stats)

    # Simpler approach: use a universal low-opacity background
    icon_bg_style = f"background: {zone_color}1a;"

    icon_html = _icon_svg(icon_key, zone_color)

    # Link to service-level architecture diagram
    if link is None:
        link = f"/architecture/custom/{urllib.parse.quote(mod_addr, safe='')}"

    # Visit-service button for services with a hostname
    visit_html = ""
    if host and not host.startswith("*"):
        visit_html = (
            f'<a href="https://{_esc(host)}" target="_blank" rel="noopener"'
            f' onclick="event.stopPropagation();event.preventDefault();'
            f'window.open(this.href,&apos;_blank&apos;);" class="svc-ext-link"'
            f' title="Open {_esc(host)}">'
            f'<span style="font-size:9px;font-weight:600;letter-spacing:0.03em;">VISIT</span>'
            f"</a>"
        )

    short_id = mod_addr.replace("module.", "")
    card = f"""
          <div class="svc-card" data-module="{_esc(short_id)}" data-href="{_esc(link)}" onclick="window.location.href=this.dataset.href;">
            <div class="svc-compliance" style="background:{dot_color}; box-shadow: 0 0 6px {dot_color};" title="Compliance: {pct_label}"></div>
            {visit_html}
            <div class="svc-icon" style="{icon_bg_style}">
              {icon_html}
            </div>
            <div class="svc-info">
              <div class="svc-name" title="{_esc(mod_addr)}">{_esc(name)}</div>
"""
    if host and not host.startswith("*"):
        card += f'              <div class="svc-host" title="{_esc(host)}">{_esc(host)}</div>\n'
    if desc:
        card += f'              <div class="svc-desc">{_esc(desc)}</div>\n'

    card += '              <div class="svc-badges">\n'
    if rtype:
        card += f'                <span class="svc-badge svc-badge-type">{_esc(rtype)}</span>\n'
    if auth:
        card += f'                <span class="svc-badge svc-badge-auth">{_esc(auth)}</span>\n'
    if total > 0:
        card += f'                <span class="svc-badge svc-badge-count">{total} res</span>\n'
    if nc > 0:
        card += f'                <span class="svc-badge" style="background:rgba(239,68,68,0.12);color:#ef4444;border:1px solid rgba(239,68,68,0.2);">{nc} NC</span>\n'
    # Cost badge — uses fmt_cost_badge for consistent None/0 handling
    badge_label, badge_color, badge_tip = fmt_cost_badge(cost, cost_unknown)
    if badge_tip:
        card += f'                <span class="svc-badge hint" style="background:{badge_color}15;color:{badge_color};border:1px solid {badge_color}33;" data-tip="{_esc(badge_tip)}">{badge_label}</span>\n'
    elif total > 0:
        card += f'                <span class="svc-badge" style="background:{badge_color}15;color:{badge_color};border:1px solid {badge_color}33;">{badge_label}</span>\n'
    card += "              </div>\n"

    card += """
            </div>
          </div>
"""
    return card


def _page_close():
    """Closing HTML with legend and footer."""
    legend_items = [
        ("#22c55e", "Fully compliant"),
        ("#f59e0b", "Partially compliant"),
        ("#ef4444", "Non-compliant"),
        ("#6b7280", "No data"),
    ]
    zone_items = [
        ("#22c55e", "Public subnet"),
        ("#3b82f6", "Application"),
        ("#a855f7", "Identity"),
        ("#f97316", "Serverless / Edge"),
        ("#ef4444", "Security"),
        ("#6b7280", "Infrastructure"),
        ("#d97706", "Parked / inactive"),
    ]

    legend_html = ""
    for color, label in legend_items:
        legend_html += f'    <div class="legend-item"><span class="legend-dot" style="background:{color};box-shadow:0 0 4px {color};"></span> {label}</div>\n'

    zone_legend_html = ""
    for color, label in zone_items:
        zone_legend_html += (
            f'    <div class="legend-item"><span class="legend-dot" style="background:{color};"></span> {label}</div>\n'
        )

    flows = [
        (
            "Internet",
            "#6b7280",
            EDGE_NODE.get("name", "Edge") if EDGE_NODE else "Load balancer",
            "#f97316",
            "ALB" if EDGE_NODE else None,
            "#22c55e" if EDGE_NODE else None,
            "Applications",
            "#3b82f6",
        ),
        ("Applications", "#3b82f6", "Identity", "#a855f7", None, None, "(auth)", "#374151"),
        ("Scanner", "#f97316", "Dashboard", "#f97316", None, None, "(compliance reports)", "#374151"),
    ]
    flow_html = ""
    for flow in flows:
        parts = f'<span style="color:{flow[1]};">{_esc(flow[0])}</span>'
        parts += ' <span style="color:#374151;">&rarr;</span> '
        parts += f'<span style="color:{flow[3]};">{_esc(flow[2])}</span>'
        if flow[4]:
            parts += ' <span style="color:#374151;">&rarr;</span> '
            parts += f'<span style="color:{flow[5]};">{_esc(flow[4])}</span>'
        if flow[6]:
            parts += f' <span style="color:{flow[7]};">{_esc(flow[6])}</span>'
        flow_html += f'    <div class="legend-item" style="gap:4px;">{parts}</div>\n'

    return f"""
  <div class="legend" style="margin-bottom:8px;">
    <span class="legend-title">Data Flow</span>
{flow_html}
  </div>
  <div class="legend">
    <span class="legend-title">Compliance</span>
{legend_html}
  </div>
  <div class="legend" style="margin-top:8px;">
    <span class="legend-title">Zones</span>
{zone_legend_html}
  </div>

  <div style="margin-top:24px; text-align:center; font-size:10px; color:#374151; padding-bottom:16px;">
    Generated from live Terraform state &middot; Cost estimates from scanner &middot;
    <a href="/burn" style="color:#3b82f6; text-decoration:none;">Actual billing</a> &middot;
    <a href="/tree" style="color:#3b82f6; text-decoration:none;">Explore tree</a> &middot;
    <a href="/network" style="color:#3b82f6; text-decoration:none;">Network view</a>
  </div>
  <div style="text-align:center; font-size:10px; color:#374151; padding-bottom:16px;">
    Click any service card for interactions &amp; detail view
  </div>
</div>

{HINT_JS_FSTR}
<script>
function downloadHtml() {{
  var clone = document.documentElement.cloneNode(true);
  var ctrls = clone.querySelectorAll('.topbar, .controls, script');
  ctrls.forEach(function(el) {{ el.remove(); }});
  var html = '<!DOCTYPE html>\\n<html>' + clone.innerHTML + '</html>';
  var blob = new Blob([html], {{type: 'text/html'}});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'architecture-custom.html';
  a.click();
  URL.revokeObjectURL(a.href);
}}
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Error page
# ─────────────────────────────────────────────────────────────────────────────


def _render_error(env_upper, error, user_email=""):
    """Render an error page when data loading fails."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Architecture Error // __BRAND__</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #050508; color: #e2e8f0;
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    display: flex; align-items: center; justify-content: center; min-height: 100vh;
  }}
  .error-box {{
    background: #111118; border: 1px solid #1e1e2e; border-radius: 12px;
    padding: 32px; max-width: 600px; text-align: center;
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
<div class="error-box">
  <h1>Architecture Diagram Unavailable</h1>
  <p><code>{_esc(error)}</code></p>
  <p>This view requires access to the Terraform state file in S3
     and a recent compliance report.</p>
  <a href="/tree">&larr; Tree View</a> &middot;
  <a href="/">Dashboard</a>
</div>
</body>
</html>"""

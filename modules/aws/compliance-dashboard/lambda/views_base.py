"""
Shared constants, helpers, and layout for the compliance dashboard views.

All view modules import from here: _e, _layout, SEV_COLORS, SEV_BG, etc.
"""

import html
import json
import os
import re

from compliance_schema import REQUIRED_TAGS as _SCHEMA_REQUIRED_TAGS
from data_core import REGION

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

# Severity colors — neon accents on dark backgrounds
SEV_COLORS = {
    "HIGH": "#ef4444",
    "MEDIUM": "#f59e0b",
    "LOW": "#22c55e",
}

# Muted background variants for severity badges
SEV_BG = {
    "HIGH": "rgba(239,68,68,0.15)",
    "MEDIUM": "rgba(245,158,11,0.15)",
    "LOW": "rgba(34,197,94,0.15)",
}

STATUS_COLORS = {
    "warned": "#f59e0b",
    "aborted": "#6b7280",
    "torn_down": "#ef4444",
    "resolved": "#22c55e",
    "protected": "#3b82f6",
    "error": "#ef4444",
    "dry_run": "#a78bfa",
}

TYPE_ICONS = {
    "ec2:instance": "&#x1F5A5;",
    "ec2:volume": "&#x1F4BE;",
    "rds:instance": "&#x1F5C4;",
    "ecs:service": "&#x2699;",
    "ecs:cluster": "&#x2699;",
    "lambda:function": "&#x26A1;",
    "s3:bucket": "&#x1F4E6;",
    "secretsmanager:secret": "&#x1F512;",
    "elasticache:cluster": "&#x1F9CA;",
    "elbv2:application": "&#x1F310;",
    "dynamodb:table": "&#x1F4CA;",
}


def _e(text):
    """HTML-escape a string."""
    return html.escape(str(text)) if text else ""


_JS_JSON_ESCAPES = {
    ord("<"): "\\u003c",
    ord(">"): "\\u003e",
    ord("&"): "\\u0026",
    0x2028: "\\u2028",
    0x2029: "\\u2029",
}


def _js_json(obj, **kwargs):
    """Serialize obj as JSON that is safe to embed in an inline <script> block.

    json.dumps alone does not neutralise ``</script>`` or ``<!--``; this also
    escapes ``<``, ``>``, ``&`` and the U+2028/U+2029 line separators as
    \\uXXXX, which leaves the parsed value unchanged. For an HTML attribute
    (data-*, onclick) wrap the result in _e() as well.
    """
    return json.dumps(obj, **kwargs).translate(_JS_JSON_ESCAPES)


_SAFE_URL_RE = re.compile(r"^https?://[^\s\\]+$", re.IGNORECASE)
_CTRL_CHARS_RE = re.compile(r"[\x00-\x20\x7f]")


def _safe_url(url):
    """Return url if it is an absolute http(s) URL, else "".

    Use for any href/src built from tag values, registry entries or other
    external data, so that ``javascript:``/``data:``/``vbscript:`` URLs never
    become clickable links. The result still needs _e() when placed in an
    attribute.
    """
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    if _CTRL_CHARS_RE.search(url) or not _SAFE_URL_RE.match(url):
        return ""
    return url


def _safe_path(path, default="/"):
    """Return path if it is a same-site relative path (``/x``), else default.

    Rejects scheme-relative (``//host``) and backslash forms so a redirect or
    back link built from request data cannot leave the site or carry a scheme.
    """
    if not path or not isinstance(path, str):
        return default
    if not path.startswith("/") or path.startswith("//") or "\\" in path or _CTRL_CHARS_RE.search(path):
        return default
    return path


def _badge(label, color="blue", extra_class="", title="", border=False):
    """Render a badge span. color: red/amber/green/blue/gray/purple/orange."""
    cls = f"badge badge-{color}"
    if border:
        cls += " badge-border"
    if extra_class:
        cls += f" {extra_class}"
    title_attr = f' title="{_e(title)}"' if title else ""
    return f'<span class="{cls}"{title_attr}>{label}</span>'


# Map severity to badge color name
_SEV_BADGE = {"HIGH": "red", "MEDIUM": "amber", "LOW": "green"}

# Map service zone to badge color name
_ZONE_BADGE = {
    "public": "green",
    "apps": "blue",
    "identity": "purple",
    "serverless": "orange",
    "security": "red",
    "infra": "gray",
}

# Map EC2/ECS state to badge color name
_STATE_BADGE = {
    "running": "green",
    "available": "green",
    "active": "green",
    "in-use": "green",
    "stopped": "amber",
    "stopping": "amber",
    "pending": "blue",
    "starting": "blue",
    "terminated": "red",
    "shutting-down": "red",
    "deleted": "red",
    "deleting": "red",
}


def _rate_color(pct):
    """Return a CSS color for a compliance rate percentage."""
    if pct >= 80:
        return "#22c55e"
    if pct >= 50:
        return "#f59e0b"
    return "#ef4444"


# ── Standard navigation ─────────────────────────────────────────────────────
# Shared across all pages: topbar, controls bar, breadcrumb.

_NAV_STANDALONE = []

_NAV_GROUPS = [
    (
        "Ops",
        [
            ("dashboard", "Dashboard", "/"),
            ("compliant", "Compliant", "/compliant"),
            ("changes", "Changes", "/changes"),
            ("protected", "Protected", "/protected"),
        ],
    ),
    (
        "Observability",
        [
            ("repos", "Repos", "/repos"),
            ("services", "Services", "/services"),
            ("security", "Security", "/security"),
            ("burn", "Burn", "/burn"),
            ("self", "Self", "/self"),
        ],
    ),
    (
        "Viz",
        [
            ("tree", "Tree", "/tree"),
            ("network", "Network", "/network"),
            ("architecture", "Architecture", "/architecture"),
        ],
    ),
    (
        "Docs",
        [
            ("overview", "Overview", "/overview"),
            ("guide", "Guide", "/guide"),
            ("docs", "Reference", "/docs"),
        ],
    ),
]

# Admin-only nav group — rendered conditionally based on user role
_NAV_ADMIN_GROUP = (
    "Admin",
    [
        ("access", "Access", "/access"),
        ("overrides", "Overrides", "/overrides"),
        ("pending", "Pending", "/pending"),
        ("invite", "Invite", "/invite"),
    ],
)

# Flat lookup: page_key → group_label (or None for standalone)
_PAGE_GROUP = {}
for _gl, _items in _NAV_GROUPS:
    for _k, _l, _h in _items:
        _PAGE_GROUP[_k] = _gl
# Include admin group pages in lookup
for _k, _l, _h in _NAV_ADMIN_GROUP[1]:
    _PAGE_GROUP[_k] = _NAV_ADMIN_GROUP[0]

# Architecture sub-page labels for breadcrumb display
_ARCH_APPROACHES = {
    "custom": "Custom HTML/CSS",
    "mock": "Mock Showcase",
}

# CSS for the standard nav (include in pages that don't use _layout)
NAV_CSS = """
  /* ── Top bar ── */
  .topbar { background: linear-gradient(90deg, #ef4444 0%, #dc2626 100%);
             padding: 4px 24px; font-size: 11px; font-weight: 700;
             letter-spacing: 0.15em; text-transform: uppercase; color: #fff;
             display: flex; justify-content: space-between;
             position: sticky; top: 0; z-index: 100; }
  /* ── Controls bar ── */
  .controls { background: rgba(8,8,12,0.95); backdrop-filter: blur(12px);
               border-bottom: 1px solid rgba(255,255,255,0.04);
               padding: 8px 24px; display: flex; align-items: center; gap: 12px;
               position: sticky; top: 28px; z-index: 90; flex-wrap: wrap; }
  .controls .brand { color: #ef4444; font-weight: 700; font-size: 14px;
                     letter-spacing: 0.08em; }
  .controls .brand span { color: #4b5563; font-weight: 400; font-size: 11px;
                          margin-left: 6px; }
  .controls a { color: #6b7280; text-decoration: none; font-size: 12px; padding: 6px 12px;
                border: 1px solid rgba(255,255,255,0.06); border-radius: 4px;
                transition: all 0.15s; }
  .controls a:hover { color: #e2e8f0; border-color: #374151;
                      background: rgba(255,255,255,0.03); }
  .controls a.active { color: #ef4444; border-color: rgba(239,68,68,0.3);
                       background: rgba(239,68,68,0.08); }
  .controls .meta { margin-left: auto; font-size: 10px; color: #4b5563; }
  /* ── User identity widget ── */
  .user-widget { position: relative; margin-left: 8px; cursor: default; }
  .user-pill {
    display: flex; align-items: center; gap: 5px; padding: 4px 10px;
    border: 1px solid rgba(255,255,255,0.06); border-radius: 4px;
    font-size: 10px; color: #4b5563; transition: all 0.15s;
  }
  .user-pill:hover { color: #94a3b8; border-color: #374151; background: rgba(255,255,255,0.03); }
  .user-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
  .user-popup {
    display: none; position: absolute; top: 100%; right: 0;
    background: #111118; border: 1px solid #2a2a3a; border-radius: 6px;
    padding: 10px 14px; padding-top: 16px; min-width: 200px; z-index: 200;
    box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    font-size: 11px; line-height: 1.6;
  }
  .user-widget:hover .user-popup { display: block; }
  .user-widget.open .user-popup { display: block; }
  .user-popup .up-label { color: #4b5563; font-size: 9px; text-transform: uppercase;
                          letter-spacing: 0.1em; }
  .user-popup .up-value { color: #e2e8f0; word-break: break-all; }
  .user-popup .up-badge {
    display: inline-block; padding: 1px 6px; border-radius: 3px;
    font-size: 9px; font-weight: 600; margin-top: 4px;
  }
  /* ── Nav dropdown groups ── */
  .nav-group { position: relative; display: inline-flex; }
  .nav-group-label {
    color: #6b7280; font-size: 12px; padding: 6px 12px;
    border: 1px solid rgba(255,255,255,0.06); border-radius: 4px;
    cursor: default; transition: all 0.15s; user-select: none;
  }
  .nav-group-label::after { content: ' \u25be'; font-size: 9px; opacity: 0.5; }
  .nav-group:hover .nav-group-label { color: #e2e8f0; border-color: #374151;
    background: rgba(255,255,255,0.03); }
  .nav-group-label.group-active { color: #ef4444; border-color: rgba(239,68,68,0.3);
    background: rgba(239,68,68,0.08); }
  .nav-dropdown {
    display: none; position: absolute; top: 100%; left: 0;
    background: #111118; border: 1px solid #2a2a3a; border-radius: 6px;
    padding: 4px; padding-top: 8px; min-width: 140px; z-index: 200;
    box-shadow: 0 8px 24px rgba(0,0,0,0.5); flex-direction: column;
  }
  .nav-group:hover .nav-dropdown { display: flex; }
  .nav-group.open .nav-dropdown { display: flex; }
  .nav-dropdown a { border: none !important; border-radius: 4px; }
  .nav-dropdown a.active { color: #ef4444; background: rgba(239,68,68,0.08); }
  /* ── Breadcrumb (architecture sub-pages) ── */
  .breadcrumb { background: #0a0a10; border-bottom: 1px solid rgba(255,255,255,0.03);
                padding: 6px 24px; font-size: 11px; display: flex; align-items: center;
                gap: 8px; position: sticky; top: 68px; z-index: 80; }
  .breadcrumb a { color: #6b7280; text-decoration: none; transition: color 0.15s; }
  .breadcrumb a:hover { color: #e2e8f0; }
  .breadcrumb .sep { color: #374151; }
  .breadcrumb .current { color: #ef4444; font-weight: 600; }
"""

# Fixed-position variant for full-screen pages (network, diagram viewers)
NAV_CSS_FIXED = (
    NAV_CSS.replace("position: sticky; top: 0;", "position: fixed; top: 0; left: 0; right: 0;")
    .replace("position: sticky; top: 28px;", "position: fixed; top: 28px; left: 0; right: 0;")
    .replace("position: sticky; top: 68px;", "position: fixed; top: 68px; left: 0; right: 0;")
)

# JS for touch/click dropdown toggling (hover doesn't work on mobile)
_NAV_TOUCH_JS = """<script>(function(){
document.addEventListener('click',function(e){
  var label=(e.target instanceof Element?e.target:document.documentElement).closest('.nav-group-label');
  var pill=(e.target instanceof Element?e.target:document.documentElement).closest('.user-pill');
  if(label){
    var g=label.parentElement;
    document.querySelectorAll('.nav-group.open').forEach(function(el){
      if(el!==g)el.classList.remove('open');});
    document.querySelectorAll('.user-widget.open').forEach(function(el){
      el.classList.remove('open');});
    g.classList.toggle('open');return;
  }
  if(pill){
    var w=pill.parentElement;
    document.querySelectorAll('.user-widget.open').forEach(function(el){
      if(el!==w)el.classList.remove('open');});
    document.querySelectorAll('.nav-group.open').forEach(function(el){
      el.classList.remove('open');});
    w.classList.toggle('open');return;
  }
  if(!(e.target instanceof Element?e.target:document.documentElement).closest('.nav-dropdown')&&!(e.target instanceof Element?e.target:document.documentElement).closest('.user-popup')){
    document.querySelectorAll('.nav-group.open,.user-widget.open').forEach(function(el){
      el.classList.remove('open');});
  }
});
})()</script>"""


def _render_user_widget(user_email):
    """Render a compact identity widget with hover popup."""
    if not user_email:
        return ""
    from auth import is_admin

    admin = is_admin(user_email)
    dot_color = "#22c55e" if admin else "#3b82f6"
    role_badge = (
        '<span class="up-badge" style="background:rgba(34,197,94,0.15); '
        'color:#22c55e; border:1px solid rgba(34,197,94,0.3);">Admin</span>'
        if admin
        else '<span class="up-badge" style="background:rgba(59,130,246,0.15); '
        'color:#3b82f6; border:1px solid rgba(59,130,246,0.3);">Viewer</span>'
    )
    cache_btn = (
        '<div style="margin-top:8px; border-top:1px solid #1e1e2e; padding-top:8px;">'
        '<form method="POST" action="/cache/clear" style="margin:0;" '
        'onsubmit="this.return_to.value=location.pathname+location.search">'
        '<input type="hidden" name="return_to" value="/self">'
        '<button type="submit" style="background:rgba(239,68,68,0.1); '
        "color:#ef4444; border:1px solid rgba(239,68,68,0.3); border-radius:4px; "
        'padding:3px 8px; font-size:10px; cursor:pointer; width:100%;">'
        "Clear Caches</button></form>"
        "<button type=\"button\" onclick=\"document.getElementById('cache-info-modal').style.display='flex'\" "
        'style="background:none; border:none; padding:0; margin-top:4px; cursor:pointer; '
        "font-size:9px; color:#6b7280; display:flex; align-items:center; gap:3px; "
        'width:100%; justify-content:center;">'
        '<span style="display:inline-flex; align-items:center; justify-content:center; '
        "width:13px; height:13px; border-radius:50%; border:1px solid #4b5563; "
        'font-size:8px; color:#6b7280; flex-shrink:0;">?</span>'
        " Why clear cache?"
        "</button>"
        "</div>"
        if admin
        else ""
    )
    return f"""<div class="user-widget">
  <div class="user-pill">
    <span class="user-dot" style="background:{dot_color};box-shadow:0 0 4px {dot_color};"></span>
    <span>Logged in</span>
  </div>
  <div class="user-popup">
    <div class="up-label">Identity</div>
    <div class="up-value">{_e(user_email)}</div>
    <div style="margin-top:6px;">{role_badge}</div>
    {cache_btn}
    <div style="margin-top:8px; border-top:1px solid #1e1e2e; padding-top:8px;">
      <a href="/logout" style="display:block; text-align:center; font-size:11px;
        color:#9ca3af; text-decoration:none;">Log out</a>
    </div>
  </div>
</div>"""


def _build_nav_links(active_page, user_email=""):
    """Build HTML for the grouped navigation links."""
    from auth import is_admin

    active_group = _PAGE_GROUP.get(active_page)
    parts = []

    # Standalone links
    for key, label, href in _NAV_STANDALONE:
        cls = ' class="active"' if key == active_page else ""
        parts.append(f'<a href="{href}"{cls}>{label}</a>')

    # Grouped dropdown menus
    groups = list(_NAV_GROUPS)
    if is_admin(user_email):
        groups.append(_NAV_ADMIN_GROUP)

    for group_label, items in groups:
        group_cls = " group-active" if group_label == active_group else ""
        sub_links = []
        for key, label, href in items:
            cls = ' class="active"' if key == active_page else ""
            sub_links.append(f'<a href="{href}"{cls}>{label}</a>')
        sub_html = "\n    ".join(sub_links)
        parts.append(
            f'<div class="nav-group">'
            f'<span class="nav-group-label{group_cls}">{group_label}</span>'
            f'<div class="nav-dropdown">\n    {sub_html}\n  </div>'
            f"</div>"
        )

    return "\n  ".join(parts)


def render_nav(active_page, user_email="", meta=""):
    """Return the standard topbar + controls bar HTML.

    active_page: page key (e.g. "dashboard", "burn", "architecture").
    meta: optional right-side metadata text (e.g. "42 modules").
    """
    env_upper = ENVIRONMENT.upper()
    links_html = _build_nav_links(active_page, user_email)
    meta_html = f'<span class="meta">{_e(meta)}</span>' if meta else ""
    user_html = _render_user_widget(user_email)

    return f"""<div class="topbar">
  <span>__BRAND__</span>
  <span>{env_upper}</span>
</div>
<nav class="controls">
  <span class="brand">__BRAND__<span>// compliance</span></span>
  {links_html}
  {meta_html}
  {user_html}
</nav>
{_NAV_TOUCH_JS}"""


def render_nav_arch(active_approach, user_email="", meta="", sub_breadcrumb=""):
    """Return topbar + controls bar with Architecture highlighted, plus breadcrumb.

    active_approach: one of "custom", "mock", or "" for the architecture
                     index page.
    sub_breadcrumb:  optional third breadcrumb level (e.g., service name).
                     When set, the approach becomes a link and sub_breadcrumb
                     is the current (non-link) segment.
    """
    env_upper = ENVIRONMENT.upper()
    links_html = _build_nav_links("architecture", user_email)
    meta_html = f'<span class="meta">{_e(meta)}</span>' if meta else ""
    user_html = _render_user_widget(user_email)

    # Breadcrumb for sub-pages
    breadcrumb = ""
    if active_approach:
        approach_label = _ARCH_APPROACHES.get(active_approach, active_approach)
        if sub_breadcrumb:
            breadcrumb = f"""<nav class="breadcrumb">
  <a href="/architecture">All Approaches</a>
  <span class="sep">/</span>
  <a href="/architecture/{_e(active_approach)}">{_e(approach_label)}</a>
  <span class="sep">/</span>
  <span class="current">{_e(sub_breadcrumb)}</span>
</nav>"""
        else:
            breadcrumb = f"""<nav class="breadcrumb">
  <a href="/architecture">All Approaches</a>
  <span class="sep">/</span>
  <span class="current">{_e(approach_label)}</span>
</nav>"""

    return f"""<div class="topbar">
  <span>__BRAND__</span>
  <span>{env_upper}</span>
</div>
<nav class="controls">
  <span class="brand">__BRAND__<span>// compliance</span></span>
  {links_html}
  {meta_html}
  {user_html}
</nav>
{_NAV_TOUCH_JS}
{breadcrumb}"""


# ── Page layout ──────────────────────────────────────────────────────────────


def _cache_info_modal(user_email):
    """Render the 'Why clear cache?' modal — only for admin users."""
    if not user_email:
        return ""
    from auth import is_admin

    if not is_admin(user_email):
        return ""
    return """<div id="cache-info-modal" style="display:none; position:fixed; inset:0;
  background:rgba(0,0,0,0.7); z-index:9999; align-items:center; justify-content:center;
  backdrop-filter:blur(2px);"
  onclick="if(event.target===this)this.style.display='none'">
  <div style="background:#111118; border:1px solid #2a2a3a; border-radius:10px;
    max-width:620px; width:90vw; max-height:85vh; overflow-y:auto; padding:28px 32px;
    box-shadow:0 16px 48px rgba(0,0,0,0.6); position:relative;">
    <button onclick="document.getElementById('cache-info-modal').style.display='none'"
      style="position:absolute; top:12px; right:16px; background:none; border:none;
      color:#6b7280; font-size:18px; cursor:pointer; line-height:1;">&times;</button>
    <h3 style="font-size:15px; color:#f1f5f9; margin:0 0 6px; letter-spacing:0.02em;">
      Why Clear Cache?</h3>
    <p style="font-size:12px; color:#94a3b8; margin:0 0 18px; line-height:1.5;">
      Data passes through <strong>three independent cache layers</strong> before
      reaching your browser. The <span style="color:#ef4444;">Clear Caches</span>
      button only flushes one of them.</p>

    <!-- ── Data Flow Diagram ── -->
    <div style="display:flex; align-items:stretch; gap:0; margin:0 0 20px;
      font-size:11px; line-height:1.4;">

      <!-- DynamoDB -->
      <div style="flex:1; text-align:center;">
        <div style="background:#1a1a2e; border:1px solid #2a2a3a; border-radius:6px;
          padding:10px 8px;">
          <div style="font-size:10px; color:#6b7280; text-transform:uppercase;
            letter-spacing:0.08em;">Source</div>
          <div style="color:#e2e8f0; font-weight:600; margin:4px 0 2px;">DynamoDB</div>
          <div style="font-size:9px; color:#6b7280;">S3 &bull; Cost Explorer<br>
            TF State &bull; HK State</div>
        </div>
      </div>

      <!-- Arrow 1 -->
      <div style="display:flex; align-items:center; padding:0 2px;">
        <span style="color:#4b5563; font-size:16px;">&rarr;</span>
      </div>

      <!-- Lambda memory — highlighted -->
      <div style="flex:1.2; text-align:center;">
        <div style="background:rgba(239,68,68,0.08); border:2px solid rgba(239,68,68,0.4);
          border-radius:6px; padding:10px 8px; position:relative;">
          <div style="position:absolute; top:-9px; left:50%; transform:translateX(-50%);
            background:#111118; padding:0 6px; font-size:8px; color:#ef4444;
            text-transform:uppercase; letter-spacing:0.1em; white-space:nowrap;">
            Cleared by button</div>
          <div style="font-size:10px; color:#ef4444; text-transform:uppercase;
            letter-spacing:0.08em;">Server Cache</div>
          <div style="color:#e2e8f0; font-weight:600; margin:4px 0 2px;">
            Lambda Memory</div>
          <div style="font-size:9px; color:#6b7280;">
            Warm container RAM<br>
            TTL: 30s &ndash; 15min</div>
        </div>
      </div>

      <!-- Arrow 2 -->
      <div style="display:flex; align-items:center; padding:0 2px;">
        <span style="color:#4b5563; font-size:16px;">&rarr;</span>
      </div>

      <!-- Edge -->
      <div style="flex:1; text-align:center;">
        <div style="background:#1a1a2e; border:1px solid #2a2a3a; border-radius:6px;
          padding:10px 8px; opacity:0.6;">
          <div style="font-size:10px; color:#6b7280; text-transform:uppercase;
            letter-spacing:0.08em;">Edge</div>
          <div style="color:#e2e8f0; font-weight:600; margin:4px 0 2px;">CDN or proxy</div>
          <div style="font-size:9px; color:#6b7280;">DYNAMIC responses<br>
            (not cached here)</div>
        </div>
      </div>

      <!-- Arrow 3 -->
      <div style="display:flex; align-items:center; padding:0 2px;">
        <span style="color:#4b5563; font-size:16px;">&rarr;</span>
      </div>

      <!-- Browser -->
      <div style="flex:1; text-align:center;">
        <div style="background:#1a1a2e; border:1px solid #2a2a3a; border-radius:6px;
          padding:10px 8px; opacity:0.6;">
          <div style="font-size:10px; color:#6b7280; text-transform:uppercase;
            letter-spacing:0.08em;">Client</div>
          <div style="color:#e2e8f0; font-weight:600; margin:4px 0 2px;">Browser</div>
          <div style="font-size:9px; color:#6b7280;">HTML/JS/CSS cache<br>
            Ctrl+Shift+R to bypass</div>
        </div>
      </div>
    </div>

    <!-- ── Cache details table ── -->
    <table style="width:100%; border-collapse:collapse; font-size:11px; margin-bottom:14px;">
      <tr style="border-bottom:1px solid #1e1e2e;">
        <td style="padding:6px 8px; width:20px; color:#22c55e; font-size:14px;">&#x2714;</td>
        <td style="padding:6px 8px;">
          <strong style="color:#e2e8f0;">Lambda memory</strong><br>
          <span style="color:#6b7280;">Report data, TF state, Cost Explorer, HK records
          &mdash; cached in warm container RAM. Immutable reports are cached permanently;
          live data uses short TTLs (30s&ndash;15min). This button flushes
          all of them immediately.</span>
        </td>
      </tr>
      <tr style="border-bottom:1px solid #1e1e2e;">
        <td style="padding:6px 8px; color:#ef4444; font-size:14px;">&#x2718;</td>
        <td style="padding:6px 8px;">
          <strong style="color:#e2e8f0;">CDN or proxy in front</strong><br>
          <span style="color:#6b7280;">Responses are dynamic; a CDN or proxy in front of the
          dashboard does not cache them. No action needed.</span>
        </td>
      </tr>
      <tr>
        <td style="padding:6px 8px; color:#ef4444; font-size:14px;">&#x2718;</td>
        <td style="padding:6px 8px;">
          <strong style="color:#e2e8f0;">Browser cache</strong><br>
          <span style="color:#6b7280;">Your browser may serve stale HTML/JS/CSS from its
          local cache. Use <kbd style="background:#1a1a2e; border:1px solid #2a2a3a;
          border-radius:3px; padding:1px 4px; font-size:10px;">Ctrl+Shift+R</kbd>
          (hard refresh) to bypass it.</span>
        </td>
      </tr>
    </table>

    <p style="font-size:11px; color:#6b7280; margin:0; line-height:1.5;">
      <strong style="color:#94a3b8;">When to clear:</strong>
      a scan just ran and you want fresh data immediately,
      you approved/revoked a resource and the UI looks stale,
      or a deploy shipped new Lambda code but old data persists.</p>
  </div>
</div>"""


def _layout(title, content, user_email="", active_page="dashboard"):
    """Wrap content in the standard page layout."""
    nav_html = render_nav(active_page, user_email)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} // __BRAND__</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
         background: #0a0a0f; color: #e2e8f0; line-height: 1.6; }}
  ::selection {{ background: rgba(239,68,68,0.3); }}
  {NAV_CSS}
  /* ── Container ── */
  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
  h1 {{ font-size: 20px; font-weight: 700; margin-bottom: 16px; color: #f1f5f9;
        letter-spacing: 0.02em; }}
  h2 {{ font-size: 15px; font-weight: 600; margin: 24px 0 12px; color: #94a3b8;
        text-transform: uppercase; letter-spacing: 0.08em; }}
  /* ── Cards ── */
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px; margin-bottom: 24px; }}
  .card {{ background: #111118; border: 1px solid #1e1e2e; border-radius: 8px;
           padding: 16px 20px; transition: border-color 0.15s; }}
  .card:hover {{ border-color: #2a2a3a; }}
  .card .label {{ font-size: 10px; color: #6b7280; text-transform: uppercase;
                  letter-spacing: 0.12em; }}
  .card .value {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
  /* ── Tables ── */
  table {{ width: 100%; border-collapse: collapse; background: #111118;
           border: 1px solid #1e1e2e; border-radius: 8px; overflow: hidden; }}
  th {{ background: #0d0d14; text-align: left; padding: 10px 12px; font-size: 10px;
       color: #6b7280; text-transform: uppercase; letter-spacing: 0.1em;
       border-bottom: 1px solid #1e1e2e; }}
  td {{ padding: 10px 12px; border-top: 1px solid #141420; font-size: 13px; color: #cbd5e1; }}
  tr:hover td {{ background: #141420; }}
  /* ── Badges ── */
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 10px; font-weight: 700; letter-spacing: 0.06em; }}
  .badge-red {{ background: rgba(239,68,68,0.15); color: #ef4444; }}
  .badge-amber {{ background: rgba(245,158,11,0.15); color: #f59e0b; }}
  .badge-green {{ background: rgba(34,197,94,0.15); color: #22c55e; }}
  .badge-blue {{ background: rgba(59,130,246,0.15); color: #3b82f6; }}
  .badge-gray {{ background: rgba(107,114,128,0.15); color: #6b7280; }}
  .badge-purple {{ background: rgba(167,139,250,0.15); color: #a78bfa; }}
  .badge-orange {{ background: rgba(249,115,22,0.15); color: #f97316; }}
  .badge-sm {{ font-size: 9px; }}
  .badge-border {{ border: 1px solid currentColor; border-color: rgba(255,255,255,0.15); }}
  .badge-red.badge-border {{ border-color: rgba(239,68,68,0.3); }}
  .badge-amber.badge-border {{ border-color: rgba(245,158,11,0.3); }}
  .badge-green.badge-border {{ border-color: rgba(34,197,94,0.3); }}
  .badge-blue.badge-border {{ border-color: rgba(59,130,246,0.3); }}
  .badge-gray.badge-border {{ border-color: rgba(107,114,128,0.3); }}
  .badge-purple.badge-border {{ border-color: rgba(167,139,250,0.3); }}
  .badge-orange.badge-border {{ border-color: rgba(249,115,22,0.3); }}
  /* ── Hints (hover tooltips) ── */
  .hint {{ cursor: help; border-bottom: 1px dotted currentColor; }}
  .hint-tip {{ position: fixed; padding: 6px 10px;
    background: #1e1e2e; color: #e2e8f0; border: 1px solid #2a2a3a;
    border-radius: 6px; font-size: 10px; line-height: 1.4;
    max-width: 260px; z-index: 1000;
    pointer-events: none; opacity: 0; transition: opacity 0.15s; }}
  .hint-tip.visible {{ opacity: 1; }}
  /* ── Buttons ── */
  .btn {{ display: inline-block; padding: 6px 14px; border-radius: 4px; font-size: 12px;
          font-weight: 600; text-decoration: none; cursor: pointer; border: 1px solid transparent;
          letter-spacing: 0.03em; transition: all 0.15s; font-family: inherit; }}
  .btn-danger {{ background: rgba(239,68,68,0.15); color: #ef4444; border-color: rgba(239,68,68,0.3); }}
  .btn-danger:hover {{ background: #ef4444; color: #fff; }}
  .btn-warn {{ background: rgba(245,158,11,0.15); color: #f59e0b; border-color: rgba(245,158,11,0.3); }}
  .btn-warn:hover {{ background: #f59e0b; color: #000; }}
  .btn-primary {{ background: rgba(59,130,246,0.15); color: #3b82f6; border-color: rgba(59,130,246,0.3); }}
  .btn-primary:hover {{ background: #3b82f6; color: #fff; }}
  .btn-purple {{ background: rgba(139,92,246,0.15); color: #8b5cf6; border-color: rgba(139,92,246,0.3); }}
  .btn-purple:hover {{ background: #8b5cf6; color: #fff; }}
  .btn-secondary {{ background: #1e1e2e; color: #94a3b8; border-color: #2a2a3a; }}
  .btn-secondary:hover {{ background: #2a2a3a; color: #e2e8f0; }}
  /* ── Utilities ── */
  .mono {{ font-family: inherit; font-size: 12px; color: #6b7280; }}
  .cost {{ font-weight: 600; }}
  .cost-good {{ color: #22c55e; }}
  .cost-bad {{ color: #ef4444; }}
  .form-group {{ margin-bottom: 12px; }}
  .form-group label {{ display: block; font-size: 11px; color: #6b7280; margin-bottom: 4px;
                       text-transform: uppercase; letter-spacing: 0.08em; }}
  .form-group input, .form-group textarea, .form-group select {{
    width: 100%; padding: 8px 10px; background: #0a0a0f; border: 1px solid #2a2a3a;
    border-radius: 4px; font-size: 13px; color: #e2e8f0; font-family: inherit; }}
  .form-group input:focus, .form-group textarea:focus, .form-group select:focus {{
    outline: none; border-color: #ef4444; box-shadow: 0 0 0 2px rgba(239,68,68,0.15); }}
  .form-group textarea {{ resize: vertical; min-height: 60px; }}
  .alert {{ padding: 12px 16px; border-radius: 4px; margin-bottom: 16px; font-size: 13px;
            border-left: 3px solid; }}
  .alert-success {{ background: rgba(34,197,94,0.1); color: #22c55e; border-left-color: #22c55e; }}
  .alert-error {{ background: rgba(239,68,68,0.1); color: #ef4444; border-left-color: #ef4444; }}
  .alert-info {{ background: rgba(59,130,246,0.1); color: #3b82f6; border-left-color: #3b82f6; }}
  .truncate {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .clamp2 {{ display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
             overflow: hidden; text-overflow: ellipsis; line-height: 1.4; }}
  .empty {{ text-align: center; padding: 48px; color: #4b5563; }}
  .scanline {{ position: fixed; top: 0; left: 0; right: 0; height: 2px;
               background: linear-gradient(90deg, transparent, #ef4444, transparent);
               animation: scan 3s ease-in-out infinite; opacity: 0.3; pointer-events: none; z-index: 999; }}
  @keyframes scan {{ 0%,100% {{ transform: translateX(-100%); }} 50% {{ transform: translateX(100%); }} }}
  /* ── Mobile responsiveness ── */
  .scroll-x {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
  @media (max-width: 768px) {{
    .container {{ padding: 12px; }}
    h1 {{ font-size: 16px; }}
    h2 {{ font-size: 13px; margin-top: 20px; }}
    .card .value {{ font-size: 22px; }}
    .cards {{ grid-template-columns: 1fr !important; }}
    table {{ font-size: 12px; display: block; overflow-x: auto;
            -webkit-overflow-scrolling: touch; }}
    thead, tbody, tr {{ min-width: 500px; }}
    th, td {{ padding: 6px 8px; white-space: nowrap; }}
    .controls {{ padding: 6px 12px; gap: 6px; overflow-x: auto; flex-wrap: nowrap; }}
    .controls .brand {{ font-size: 12px; white-space: nowrap; }}
    .controls .brand span {{ display: none; }}
    .controls a {{ font-size: 11px; padding: 4px 8px; white-space: nowrap; }}
    .nav-group-label {{ font-size: 11px; padding: 4px 8px; }}
    .nav-dropdown {{ left: auto; right: 0; }}
    .topbar {{ padding: 3px 12px; font-size: 10px; }}
    .truncate {{ max-width: 120px; }}
    .user-pill span:last-child {{ display: none; }}
    .summary-cards {{ grid-template-columns: 1fr !important; }}
    .breakdown-grid {{ overflow-x: auto; -webkit-overflow-scrolling: touch; min-width: 0; }}
    .burn-row {{ grid-template-columns: 120px 1fr 70px !important; }}
    .burn-label {{ font-size: 11px; }}
  }}
</style>
</head>
<body>
<div class="scanline"></div>
{nav_html}
<div class="container">
{content}
</div>
{_cache_info_modal(user_email)}
<script>(function(){{
  var tip=document.createElement('div');
  tip.className='hint-tip';
  document.body.appendChild(tip);
  document.addEventListener('mouseenter',function(e){{
    var h=(e.target instanceof Element?e.target:document.documentElement).closest('.hint');
    if(!h)return;
    tip.textContent=h.getAttribute('data-tip');
    tip.classList.add('visible');
    var r=h.getBoundingClientRect();
    var above=r.top-tip.offsetHeight-4;
    if(above>0) tip.style.top=above+'px';
    else tip.style.top=(r.bottom+4)+'px';
    var left=r.left;
    if(left+260>window.innerWidth) left=window.innerWidth-270;
    if(left<4) left=4;
    tip.style.left=left+'px';
  }},true);
  document.addEventListener('mouseleave',function(e){{
    if((e.target instanceof Element?e.target:document.documentElement).closest('.hint')) tip.classList.remove('visible');
  }},true);
  window.addEventListener('scroll',function(){{tip.classList.remove('visible');}},true);
}})()</script>
</body>
</html>"""


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_contact(finding):
    """Extract contact email from a finding's tags."""
    tags = finding.get("tags", {})
    return tags.get("Contact", "") or tags.get("OwnerEmail", "") or tags.get("Owner", "") or ""


_CONSOLE_REGION_RE = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d{1,2}$")


def _console_url(arn, region=None):
    """Generate an AWS Console URL for a resource ARN (the deployment's region when none is given).

    The region becomes part of the host name, so a value that is not a region
    name is replaced by the deployment's region.
    """
    region = region if isinstance(region, str) and _CONSOLE_REGION_RE.match(region) else REGION
    base = f"https://{region}.console.aws.amazon.com"
    if ":ec2:" in arn:
        if ":instance/" in arn:
            iid = arn.split("/")[-1]
            return f"{base}/ec2/home?region={region}#InstanceDetails:instanceId={iid}"
        if ":volume/" in arn:
            vid = arn.split("/")[-1]
            return f"{base}/ec2/home?region={region}#VolumeDetails:volumeId={vid}"
        if ":security-group/" in arn:
            sgid = arn.split("/")[-1]
            return f"{base}/ec2/home?region={region}#SecurityGroup:groupId={sgid}"
    if ":ecs:" in arn:
        if ":service/" in arn:
            parts = arn.split("/")
            return f"{base}/ecs/home?region={region}#/clusters/{parts[-2]}/services/{parts[-1]}"
        if ":cluster/" in arn:
            name = arn.split("/")[-1]
            return f"{base}/ecs/home?region={region}#/clusters/{name}"
        if ":task-definition/" in arn:
            name = arn.split("/")[-1]
            return f"{base}/ecs/home?region={region}#/taskDefinitions/{name}"
    if ":rds:" in arn:
        name = arn.split(":")[-1]
        return f"{base}/rds/home?region={region}#database:id={name}"
    if ":s3:::" in arn:
        name = arn.split(":::")[-1]
        return f"https://s3.console.aws.amazon.com/s3/buckets/{name}"
    if ":lambda:" in arn:
        name = arn.split(":")[-1]
        return f"{base}/lambda/home?region={region}#/functions/{name}"
    if ":secretsmanager:" in arn:
        return f"{base}/secretsmanager/secret?name={arn.split(':secret:')[-1]}&region={region}"
    if ":dynamodb:" in arn:
        name = arn.split("/")[-1]
        return f"{base}/dynamodbv2/home?region={region}#table?name={name}"
    if ":elasticache:" in arn:
        return f"{base}/elasticache/home?region={region}"
    if ":elbv2:" in arn or ":elasticloadbalancing:" in arn:
        return f"{base}/ec2/home?region={region}#LoadBalancers:"
    if ":efs:" in arn:
        fsid = arn.split("/")[-1]
        return f"{base}/efs/home?region={region}#/file-systems/{fsid}"
    if ":logs:" in arn:
        # arn:aws:logs:REGION:ACCT:log-group:NAME
        name = arn.split("log-group:")[-1].rstrip("*").rstrip(":")
        return f"{base}/cloudwatch/home?region={region}#logsV2:log-groups/log-group/{name.replace('/', '$252F')}"
    if ":iam:" in arn:
        if ":role/" in arn:
            name = arn.split("/")[-1]
            return f"https://us-east-1.console.aws.amazon.com/iam/home#/roles/details/{name}"
        if ":user/" in arn:
            name = arn.split("/")[-1]
            return f"https://us-east-1.console.aws.amazon.com/iam/home#/users/details/{name}"
        if ":policy/" in arn:
            return f"https://us-east-1.console.aws.amazon.com/iam/home#/policies/arn={arn}"
    if ":acm:" in arn:
        cert_id = arn.split("/")[-1]
        return f"{base}/acm/home?region={region}#/certificates/{cert_id}"
    if ":events:" in arn:
        name = arn.split("/")[-1]
        return f"{base}/events/home?region={region}#/eventbus/default/rules/{name}"
    return ""


def _admin_only(label="Admin only"):
    """Render a subtle admin-only indicator (shield icon + tooltip).

    Placed next to features that non-admin viewers cannot access.
    Only call this when the current user IS an admin — the indicator
    communicates "you see this because you're admin; others don't".
    """
    return (
        f'<span style="color:#22c55e; font-size:9px; opacity:0.6; margin-left:4px; '
        f'cursor:help; vertical-align:middle;" title="{_e(label)}">'
        f"&#x1F6E1;</span>"
    )


def _ecr_scan_url(repo_name, digest, region=None):
    """AWS Console URL for ECR image scan results."""
    region = region or REGION
    account_id = os.environ.get("ACCOUNT_ID", "")
    if not account_id or not digest:
        return ""
    return (
        f"https://{region}.console.aws.amazon.com/ecr/repositories/"
        f"private/{account_id}/{repo_name}/_/image/"
        f"{digest}/scan-results?region={region}"
    )


def _ecr_repo_url(repo_name, region=None):
    """AWS Console URL for an ECR repository."""
    region = region or REGION
    account_id = os.environ.get("ACCOUNT_ID", "")
    if not account_id:
        return ""
    return f"https://{region}.console.aws.amazon.com/ecr/repositories/private/{account_id}/{repo_name}?region={region}"


def _compliance_path(f, managed):
    """Generate a recommended path to compliance for a finding."""
    if not managed:
        return ""
    category = f.get("category", "")
    issue = f.get("issue", "")
    if "tag" in category.lower() or "missing" in issue.lower():
        return "Add required tags in Terraform config"
    if "schedule" in category.lower() or "schedule" in issue.lower():
        return "Add Schedule tag in Terraform config"
    if "lifecycle" in category.lower():
        return "Set Lifecycle tag in Terraform config"
    if "drift" in category.lower() or "unmanaged" in issue.lower():
        return "Verify resource is in Terraform state"
    return "Update Terraform config to fix compliance"


# Allow Terraform env var override, fall back to shared schema constant
_REQUIRED_TAGS = [
    t.strip() for t in (os.environ.get("REQUIRED_TAGS") or ",".join(_SCHEMA_REQUIRED_TAGS)).split(",") if t.strip()
]


def _get_missing_required_tags(tags):
    """Return list of required tags that are missing."""
    return [t for t in _REQUIRED_TAGS if t not in tags]


def render_success(title, message, back_url="/"):
    """Render a success message page."""
    content = f"""
    <div class="alert alert-success">{_e(message)}</div>
    <a href="{_e(_safe_path(back_url))}" class="btn btn-secondary">&larr; Back</a>"""
    return _layout(title, content)


def render_error(title, message, status=400):
    """Render an error message page."""
    content = f"""
    <div class="alert alert-error">{_e(message)}</div>
    <a href="/" class="btn btn-secondary">&larr; Back to Dashboard</a>"""
    return _layout(title, content)


def paginate(items, page, page_size=50):
    """Slice a list for pagination, return (page_items, pagination_html, total)."""
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_items = items[start:end]

    pagination_html = ""
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(f'<a href="?page={page - 1}" class="btn btn-secondary" style="font-size:11px;">&larr; Prev</a>')
        else:
            nav.append('<span class="btn btn-secondary" style="font-size:11px; opacity:0.3;">&larr; Prev</span>')

        for p in range(1, total_pages + 1):
            if p == 1 or p == total_pages or abs(p - page) <= 2:
                if p == page:
                    nav.append(f'<span class="btn btn-primary" style="font-size:11px;">{p}</span>')
                else:
                    nav.append(f'<a href="?page={p}" class="btn btn-secondary" style="font-size:11px;">{p}</a>')
            elif (p == 2 and page > 4) or (p == total_pages - 1 and page < total_pages - 3):
                nav.append('<span style="color:#4b5563; font-size:11px; padding:0 4px;">&hellip;</span>')

        if page < total_pages:
            nav.append(f'<a href="?page={page + 1}" class="btn btn-secondary" style="font-size:11px;">Next &rarr;</a>')
        else:
            nav.append('<span class="btn btn-secondary" style="font-size:11px; opacity:0.3;">Next &rarr;</span>')

        pagination_html = f"""
        <div style="display:flex; gap:4px; align-items:center; margin-top:12px; flex-wrap:wrap;">
          {"".join(nav)}
          <span style="color:#4b5563; font-size:11px; margin-left:8px;">
            {start + 1}&ndash;{end} of {total}
          </span>
        </div>"""

    return page_items, pagination_html, total

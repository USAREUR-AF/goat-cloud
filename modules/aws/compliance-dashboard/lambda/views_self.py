"""
Self-observability page — the compliance dashboard monitoring itself.

Reports on Lambda configuration, data source reachability, route-level
performance from automated E2E runs, and runtime configuration.

Performance data is read from perf/<date>/perf-results.json in the reports
bucket, written by whatever load test the deployment runs. Without it the
performance section says so.
"""

import os

import site_config
from views_base import _e, _layout


def _render_health_cards(report, report_date):
    """Service health summary cards."""
    # Report freshness
    if report_date and report_date != "local":
        freshness = f"Latest report: {_e(report_date)}"
    elif report_date == "local":
        freshness = "Local mode (no S3)"
    else:
        freshness = "No report available"

    resource_count = len(report.get("discovered_resources", [])) if report else 0

    return f"""
    <div class="cards" style="grid-template-columns: repeat(3, 1fr);">
      <div class="card">
        <div class="label">Report Freshness</div>
        <div class="value" style="font-size:16px;">{freshness}</div>
      </div>
      <div class="card">
        <div class="label">Discovered Resources</div>
        <div class="value">{resource_count}</div>
      </div>
      <div class="card">
        <div class="label">Data Mode</div>
        <div class="value" style="font-size:16px;">
          {"Local filesystem" if os.environ.get("LOCAL_DATA_DIR") else "S3 + DynamoDB"}
        </div>
      </div>
    </div>"""


def _render_lambda_context(context):
    """Lambda execution context from the handler's context object."""
    if not context:
        return """
    <div class="card">
      <div class="label">Lambda Context</div>
      <div style="color:#6b7280;">Not available (local mode or missing context)</div>
    </div>"""

    rows = [
        ("Function Name", getattr(context, "function_name", "—")),
        ("Function Version", getattr(context, "function_version", "—")),
        ("Memory Limit", f"{getattr(context, 'memory_limit_in_mb', '—')} MB"),
        ("Log Group", getattr(context, "log_group_name", "—")),
        ("Invoked ARN", getattr(context, "invoked_function_arn", "—")),
    ]

    rows_html = "".join(
        f'<tr><td style="color:#94a3b8; width:180px;">{_e(k)}</td>'
        f'<td style="font-family:monospace; font-size:12px;">{_e(str(v))}</td></tr>'
        for k, v in rows
    )

    return f"""
    <table>
      <thead><tr><th>Property</th><th>Value</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>"""


def _render_performance():
    """E2E performance data from the reports bucket, when a load test writes it."""
    from data import get_perf_results, get_perf_trend

    perf = get_perf_results()
    if not perf:
        return """
    <div class="card" style="border-left:3px solid #4b5563;">
      <div class="label">No Performance Data</div>
      <div style="color:#6b7280; font-size:13px; margin-top:8px;">
        No E2E performance results found in the reports bucket. A load test
        that writes them shows its results here.
      </div>
      <div style="color:#4b5563; font-size:12px; margin-top:8px;">
        Results are read from <span style="font-family:monospace;">perf/&lt;date&gt;/perf-results.json</span> in the reports bucket.
      </div>
    </div>"""

    timestamp = _e(perf.get("timestamp", ""))
    warn_ms = perf.get("ttfb_warn_ms", 500)
    passes = perf.get("passes", {})
    cold = passes.get("cold", {})
    warm = passes.get("warm", {})

    # Summary cards
    cold_summary = cold.get("summary", {})
    warm_summary = warm.get("summary", {})
    cold_failed = len(cold_summary.get("failed_routes", []))
    warm_failed = len(warm_summary.get("failed_routes", []))

    def _ttfb_color(ms):
        if ms > warn_ms:
            return "#ef4444"
        if ms > warn_ms * 0.6:
            return "#f59e0b"
        return "#22c55e"

    cards = f"""
    <div class="cards" style="grid-template-columns: repeat(4, 1fr);">
      <div class="card">
        <div class="label">Last Run</div>
        <div class="value" style="font-size:14px;">{timestamp}</div>
      </div>
      <div class="card">
        <div class="label">Cold Avg TTFB</div>
        <div class="value" style="color:{_ttfb_color(cold_summary.get("avg_ttfb_ms", 0))};">
          {_e(str(cold_summary.get("avg_ttfb_ms", 0)))}ms</div>
      </div>
      <div class="card">
        <div class="label">Warm Avg TTFB</div>
        <div class="value" style="color:{_ttfb_color(warm_summary.get("avg_ttfb_ms", 0))};">
          {_e(str(warm_summary.get("avg_ttfb_ms", 0)))}ms</div>
      </div>
      <div class="card">
        <div class="label">Failed Routes</div>
        <div class="value" style="color:{"#ef4444" if (cold_failed + warm_failed) else "#22c55e"};">
          {cold_failed + warm_failed}</div>
      </div>
    </div>"""

    # Route-level tables (cold + warm side by side via tabs)
    def _route_table(pass_data, pass_name):
        routes = pass_data.get("routes", [])
        if not routes:
            return f'<div style="color:#6b7280;">No route data for {_e(pass_name)}</div>'
        summary = pass_data.get("summary", {})
        rows = []
        for r in routes:
            status = r.get("status", 0)
            ttfb = r.get("ttfb_ms", 0)
            total = r.get("total_ms", 0)
            size = r.get("size_kb", 0)
            cf = r.get("cf_cache", "N/A")

            status_color = "#22c55e" if status == 200 else "#ef4444"
            ttfb_extra = "color:#ef4444; font-weight:600; " if ttfb > warn_ms else ""

            rows.append(
                f"<tr>"
                f'<td style="font-family:monospace; font-size:12px;">{_e(r.get("route", ""))}</td>'
                f'<td style="color:{status_color}; text-align:center;">{_e(str(status))}</td>'
                f'<td style="{ttfb_extra}text-align:right;">{_e(str(ttfb))}</td>'
                f'<td style="text-align:right;">{_e(str(total))}</td>'
                f'<td style="text-align:right;">{_e(str(size))}</td>'
                f'<td style="text-align:center; font-size:11px;">{_e(cf)}</td>'
                f"</tr>"
            )

        p95 = summary.get("p95_ttfb_ms", 0)
        duration = pass_data.get("duration_ms", 0)
        slow = summary.get("slow_routes", [])
        slow_note = ""
        if slow:
            slow_list = ", ".join(_e(s) for s in slow)
            slow_note = f'<div style="color:#f59e0b; font-size:12px; margin-top:6px;">Slow (&gt;{_e(str(warn_ms))}ms): {slow_list}</div>'

        return f"""
        <div style="margin-bottom:6px; font-size:12px; color:#94a3b8;">
          {_e(pass_name)} &mdash; P95: {_e(str(p95))}ms &middot; Total crawl: {_e(str(duration))}ms
        </div>
        <table>
          <thead>
            <tr>
              <th>Route</th><th style="text-align:center;">Status</th>
              <th style="text-align:right;">TTFB(ms)</th>
              <th style="text-align:right;">Total(ms)</th>
              <th style="text-align:right;">Size(KB)</th>
              <th style="text-align:center;">CF Cache</th>
            </tr>
          </thead>
          <tbody>{"".join(rows)}</tbody>
        </table>
        {slow_note}"""

    cold_table = _route_table(cold, "Cold (cache miss)")
    warm_table = _route_table(warm, "Warm (cache hit)")

    # Trend chart (last 14 days)
    trend = get_perf_trend(days=14)
    trend_html = ""
    if len(trend) >= 2:
        max_ttfb = max(
            max(t["p95_cold"] for t in trend),
            max(t["p95_warm"] for t in trend),
            1,
        )
        bars = []
        for t in trend:
            cold_pct = min(100, round(100 * t["p95_cold"] / max_ttfb))
            warm_pct = min(100, round(100 * t["p95_warm"] / max_ttfb))
            label = t["date"][-5:]  # MM-DD
            failed_dot = (
                f'<span style="color:#ef4444; font-size:10px;" title="{_e(str(t["failed_count"]))} failed">&#x25cf;</span>'
                if t["failed_count"]
                else ""
            )
            bars.append(
                f'<div style="display:flex; flex-direction:column; align-items:center; gap:2px; flex:1; min-width:0;">'
                f'<div style="display:flex; gap:2px; align-items:flex-end; height:60px; width:100%;">'
                f'<div style="flex:1; background:#3b82f6; height:{cold_pct}%; border-radius:2px 2px 0 0;" '
                f'title="Cold P95: {_e(str(t["p95_cold"]))}ms"></div>'
                f'<div style="flex:1; background:#22c55e; height:{warm_pct}%; border-radius:2px 0 0;" '
                f'title="Warm P95: {_e(str(t["p95_warm"]))}ms"></div>'
                f"</div>"
                f'<div style="font-size:9px; color:#6b7280; white-space:nowrap;">{_e(label)}{failed_dot}</div>'
                f"</div>"
            )

        trend_html = f"""
        <h3 style="margin-top:24px; font-size:14px; color:#e2e8f0;">P95 TTFB TREND (14 days)</h3>
        <div style="display:flex; gap:3px; align-items:flex-end; margin-top:8px; padding:8px; background:#1e293b; border-radius:6px;">
          {"".join(bars)}
        </div>
        <div style="display:flex; gap:16px; margin-top:6px; font-size:11px; color:#94a3b8;">
          <span><span style="display:inline-block; width:10px; height:10px; background:#3b82f6; border-radius:2px;"></span> Cold</span>
          <span><span style="display:inline-block; width:10px; height:10px; background:#22c55e; border-radius:2px;"></span> Warm</span>
          <span>Max: {max_ttfb}ms</span>
        </div>"""

    return f"""
    {cards}

    <div style="display:grid; grid-template-columns:1fr 1fr; gap:24px; margin-top:16px;">
      <div>{cold_table}</div>
      <div>{warm_table}</div>
    </div>
    {trend_html}"""


def _render_data_sources():
    """Data source reachability checks."""
    local_mode = bool(os.environ.get("LOCAL_DATA_DIR"))

    checks = []

    if local_mode:
        local_dir = os.environ.get("LOCAL_DATA_DIR", "")
        report_path = os.path.join(local_dir, "compliance-report.json")
        state_path = os.path.join(local_dir, "terraform.tfstate")
        checks.append(("Local compliance-report.json", os.path.exists(report_path)))
        checks.append(("Local terraform.tfstate", os.path.exists(state_path)))
    else:
        bucket = os.environ.get("REPORTS_BUCKET", "")
        dynamo_table = os.environ.get("DYNAMODB_TABLE", os.environ.get("HK_STATE_TABLE", ""))
        checks.append(("S3 Bucket", bool(bucket), bucket or "not set"))
        checks.append(("DynamoDB Table", bool(dynamo_table), dynamo_table or "not set"))

    rows = []
    for item in checks:
        label = item[0]
        ok = item[1]
        detail = item[2] if len(item) > 2 else ""
        color = "#22c55e" if ok else "#ef4444"
        icon = "&#x2714;" if ok else "&#x2718;"
        rows.append(
            f"<tr>"
            f'<td><span style="color:{color};">{icon}</span> {_e(label)}</td>'
            f'<td style="font-family:monospace; font-size:12px; color:#94a3b8;">{_e(detail)}</td>'
            f"</tr>"
        )

    return f"""
    <table>
      <thead><tr><th>Data Source</th><th>Detail</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""


def _render_routes():
    """List registered routes with handler status."""
    # These are the routes from index.py — maintained as a static list
    # since introspection of the handler function isn't practical here.
    routes = [
        ("GET", "/", "_handle_home"),
        ("GET", "/findings", "_handle_findings"),
        ("GET", "/compliant", "_handle_compliant"),
        ("GET", "/cure", "_handle_cure"),
        ("GET", "/all", "_handle_all"),
        ("GET", "/changes", "_handle_changes"),
        ("GET", "/services", "_handle_services"),
        ("GET", "/security", "_handle_security"),
        ("GET", "/burn", "_handle_burn"),
        ("GET", "/self", "_handle_self"),
        ("GET", "/tree", "_handle_tree"),
        ("GET", "/network", "_handle_network"),
        ("GET", "/architecture", "_handle_architecture"),
        ("GET", "/architecture/custom", "render_architecture_custom"),
        ("GET", "/architecture/custom/:module", "render_architecture_service"),
        ("GET", "/architecture/drawio", "_drawio_download"),
        ("GET", "/pending", "_handle_pending"),
        ("GET", "/overrides", "_handle_overrides"),
        ("GET", "/access", "_handle_access"),
        ("GET", "/report/download", "_handle_report_download"),
        ("GET", "/report/html", "_handle_html_report_download"),
        ("GET", "/guide", "_handle_guide"),
        ("GET", "/docs", "_handle_docs"),
        ("GET", "/detail/:address", "render_detail"),
        ("POST", "/override", "_handle_create_override"),
        ("POST", "/override/delete", "_handle_delete_override"),
        ("POST", "/abort", "_handle_abort"),
        ("POST", "/extend", "_handle_extend"),
        ("POST", "/approve", "_handle_approve"),
        ("POST", "/revoke", "_handle_revoke"),
        ("POST", "/destroy", "_handle_destroy"),
        ("POST", "/access/revoke", "_handle_revoke_viewer"),
    ]

    rows = []
    for method, path, handler_name in routes:
        method_color = "#3b82f6" if method == "GET" else "#f59e0b"
        rows.append(
            f"<tr>"
            f'<td style="color:{method_color}; font-weight:600; font-family:monospace;">{method}</td>'
            f'<td style="font-family:monospace;">{_e(path)}</td>'
            f'<td style="color:#6b7280; font-size:12px;">{_e(handler_name)}</td>'
            f"</tr>"
        )

    return f"""
    <table>
      <thead><tr><th>Method</th><th>Path</th><th>Handler</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""


def _render_configuration():
    """Environment variables and configuration (non-secret values only)."""
    env_vars = [
        ("REPORTS_BUCKET", os.environ.get("REPORTS_BUCKET", "")),
        ("HK_STATE_TABLE", os.environ.get("HK_STATE_TABLE", "")),
        ("OVERRIDES_TABLE", os.environ.get("OVERRIDES_TABLE", "")),
        ("TF_STATE_BUCKET", os.environ.get("TF_STATE_BUCKET", "")),
        ("TF_STATE_KEY", os.environ.get("TF_STATE_KEY", "")),
        ("DASHBOARD_URL", os.environ.get("DASHBOARD_URL", "")),
        ("ENVIRONMENT", os.environ.get("ENVIRONMENT", "dev")),
        ("LOCAL_DATA_DIR", os.environ.get("LOCAL_DATA_DIR", "")),
    ]

    from auth import ADMIN_EMAILS

    admin_display = ", ".join(ADMIN_EMAILS) if ADMIN_EMAILS else "(none)"

    rows = []
    for name, value in env_vars:
        display = _e(value) if value else '<span style="color:#4b5563;">not set</span>'
        rows.append(
            f"<tr>"
            f'<td style="font-family:monospace; font-weight:600;">{_e(name)}</td>'
            f'<td style="font-family:monospace; font-size:12px;">{display}</td>'
            f"</tr>"
        )
    rows.append(
        f"<tr>"
        f'<td style="font-family:monospace; font-weight:600;">ADMIN_EMAILS</td>'
        f'<td style="font-family:monospace; font-size:12px;">{_e(admin_display)}</td>'
        f"</tr>"
    )

    return f"""
    <table>
      <thead><tr><th>Variable</th><th>Value</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""


def render_self(report, context, user_email="", admin=False, flash=""):
    """Render the self-observability page."""
    from data import get_latest_report_date

    report_date = get_latest_report_date()

    content = f"""
    <h1>SELF-OBSERVABILITY</h1>
    <p style="color:#94a3b8; margin-bottom:20px; font-size:13px;">
      The compliance dashboard monitoring its own health, performance, and
      configuration.
    </p>

    <h2>SERVICE HEALTH</h2>
    {_render_health_cards(report, report_date)}

    <h2>LAMBDA CONTEXT</h2>
    {_render_lambda_context(context)}

    <h2>ROUTE PERFORMANCE</h2>
    {_render_performance()}

    <h2>DATA SOURCES</h2>
    {_render_data_sources()}

    <h2>CONFIGURATION</h2>
    {_render_configuration()}

    <h2>REGISTERED ROUTES ({len(_get_route_count())})</h2>
    {_render_routes()}
    """

    return _layout("Self", content, user_email, active_page="self")


def _get_route_count():
    """Return the static route list for counting."""
    return [
        "/",
        "/findings",
        "/compliant",
        "/cure",
        "/all",
        "/changes",
        "/services",
        "/security",
        "/burn",
        "/self",
        "/tree",
        "/network",
        "/architecture",
        "/architecture/custom",
        *site_config.get("self_check_routes"),
        "/architecture/drawio",
        "/pending",
        "/overrides",
        "/access",
        "/report/download",
        "/report/html",
        "/guide",
        "/docs",
        "/detail/:address",
        "POST /override",
        "POST /override/delete",
        "POST /abort",
        "POST /extend",
        "POST /approve",
        "POST /revoke",
        "POST /destroy",
        "POST /access/revoke",
    ]

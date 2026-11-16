"""
Protected Resources page — /protected

Shows resources that must never be torn down by the hunterkiller,
organized by category (infrastructure, ad-hoc, backup, notes).
"""

from data import get_protected_resources
from views_base import TYPE_ICONS, _e, _layout

# Category display config — ordered by rendering priority
_CATEGORY_META = {
    "terraform_state": {"label": "Terraform State", "color": "#3b82f6", "icon": "&#x1F5C4;"},
    "data": {"label": "Data Buckets", "color": "#22c55e", "icon": "&#x1F4E6;"},
    "infrastructure": {"label": "Infrastructure Foundations", "color": "#3b82f6", "icon": "&#x1F3D7;"},
    "ad_hoc": {"label": "Ad-hoc Resources", "color": "#8b5cf6", "icon": "&#x1F5A5;"},
    "backup": {"label": "Backups", "color": "#06b6d4", "icon": "&#x1F4BE;"},
    "managed_secret": {"label": "TF-Managed Secrets", "color": "#f59e0b", "icon": "&#x1F512;"},
    "domain": {"label": "Domain / DNS", "color": "#6b7280", "icon": "&#x1F310;"},
}


def render_protected(user_email=""):
    """Render the /protected page."""
    data = get_protected_resources()
    summary = data.get("summary", {})
    resources = data.get("resources", [])
    config = data.get("config", {})
    notes = config.get("notes", [])

    h = []

    # Title
    h.append("<h1>Protected Resources</h1>")
    h.append(
        "<p style='color:#6b7280;font-size:12px;margin:-12px 0 20px;'>"
        "Resources exempt from <strong style='color:#94a3b8;'>Hunterkiller</strong> "
        "(individual resource enforcement). HK will never target these regardless of compliance status. "
        "Still scanned for compliance visibility.</p>"
    )
    h.append(
        "<div style='margin-bottom:16px;padding:10px 14px;background:#1e1b2e;"
        "border:1px solid #4c1d9544;border-radius:6px;font-size:11px;color:#a78bfa;'>"
        "<strong>Note:</strong> This protection applies to Hunterkiller only (individual resource teardown). "
        "If a resource belongs to a <em>temporary</em> stack scheduled for reaper teardown, "
        "the entire stack is removed via <code>terraform destroy</code> — which removes all resources "
        "in the stack, including ones HK would not individually target (S3 buckets, DNS records, EIPs, etc.)."
        "</div>"
    )

    # Summary cards
    total = summary.get("total", 0)
    compliant = summary.get("compliant", 0)
    noncompliant = summary.get("noncompliant", 0)
    total_cost = summary.get("total_cost", 0)

    h.append("<div class='cards'>")
    h.append(
        f"<div class='card'><div class='label'>Protected</div>"
        f"<div class='value' style='color:#3b82f6;'>{_e(str(total))}</div></div>"
    )
    h.append(
        f"<div class='card'><div class='label'>Compliant</div>"
        f"<div class='value' style='color:#22c55e;'>{_e(str(compliant))}</div></div>"
    )
    h.append(
        f"<div class='card'><div class='label'>Non-Compliant</div>"
        f"<div class='value' style='color:#f59e0b;'>{_e(str(noncompliant))}</div></div>"
    )
    h.append(
        f"<div class='card'><div class='label'>Monthly Cost</div>"
        f"<div class='value' style='color:#94a3b8;'>${total_cost:.0f}</div></div>"
    )
    h.append("</div>")

    # Group resources by category
    by_category = {}
    for r in resources:
        cat = r.get("category", "unknown")
        by_category.setdefault(cat, []).append(r)

    # Render each category in display order
    for cat_key in _CATEGORY_META:
        items = by_category.get(cat_key, [])
        if not items:
            continue

        meta = _CATEGORY_META[cat_key]
        h.append(f"<h2>{meta['icon']} {meta['label']}</h2>")
        h.append(_resource_table(items))

    # Catch any categories not in _CATEGORY_META
    for cat_key, items in by_category.items():
        if cat_key not in _CATEGORY_META:
            h.append(f"<h2>{_e(str(cat_key).replace('_', ' ').title())}</h2>")
            h.append(_resource_table(items))

    # Notes section
    if notes:
        h.append("<h2>&#x1F4DD; Notes</h2>")
        h.append("<table><tr><th>Name</th><th>Reason</th><th>Action</th></tr>")
        for note in notes:
            name = _e(note.get("name", ""))
            reason = _e(note.get("reason", ""))
            action = note.get("action", "")
            action_badge = _action_badge(action)
            h.append(
                f"<tr><td style='font-weight:600;'>{name}</td>"
                f"<td style='font-size:12px;color:#94a3b8;'>{reason}</td>"
                f"<td>{action_badge}</td></tr>"
            )
        h.append("</table>")

    # Design note about caching
    h.append(
        "<div style='margin-top:24px;padding:12px 16px;background:#111118;"
        "border:1px solid #1e1e2e;border-radius:6px;font-size:11px;color:#4b5563;'>"
        "<strong style='color:#6b7280;'>Design note:</strong> "
        "Protected resources config is read fresh from SSM Parameter Store on every request "
        "(no caching). Compliance findings are from the latest scanner report. "
        "Changes to the SSM parameter take effect immediately.</div>"
    )

    content = "\n".join(h)
    return _layout("Protected Resources", content, user_email, active_page="protected")


def _resource_table(items):
    """Render a protected resources table."""
    h = []
    h.append("<table>")
    h.append("<tr><th>Name</th><th>ARN</th><th>Status</th><th>HK Policy</th><th>Reason</th><th>Cost</th></tr>")

    for item in items:
        name = _e(item.get("name", ""))
        arn = item.get("resolved_arn", item.get("arn_pattern", ""))
        short_arn = _truncate_arn(arn)
        is_compliant = item.get("is_compliant", True)
        action = item.get("action", "")
        reason = _e(item.get("reason", ""))
        raw_cost = item.get("estimated_monthly_cost")
        cost = float(raw_cost) if raw_cost is not None else None
        state = item.get("state", "")
        resource_type = item.get("resource_type", "")
        finding_count = item.get("finding_count", 0)
        auto = item.get("auto_discovered", False)

        # Type icon
        icon = TYPE_ICONS.get(resource_type, "")

        # Auto-discovered badge
        auto_badge = (
            " <span class='badge' style='background:rgba(99,102,241,0.15);color:#818cf8;font-size:9px;'>AUTO</span>"
            if auto
            else ""
        )

        # Compliance badge
        if is_compliant:
            status_badge = "<span class='badge' style='background:rgba(34,197,94,0.15);color:#22c55e;'>COMPLIANT</span>"
        else:
            status_badge = (
                f"<span class='badge' style='background:rgba(245,158,11,0.15);"
                f"color:#f59e0b;'>{_e(str(finding_count))} FINDING{'S' if finding_count != 1 else ''}</span>"
            )

        # State badge (for stopped instances)
        state_badge = ""
        if state and state != "available":
            state_color = "#6b7280" if state == "stopped" else "#22c55e"
            state_badge = (
                f" <span class='badge' style='background:rgba(107,114,128,0.15);"
                f"color:{state_color};'>{_e(state)}</span>"
            )

        action_badge = _action_badge(action)

        # Cost display
        if cost is None:
            cost_str = '<span class="hint" style="color:#4b5563;font-size:10px;" data-tip="Scanner has no pricing model for this resource type.">no model</span>'
        elif cost > 0:
            cost_str = f"${cost:.2f}/mo"
        else:
            cost_str = "-"

        h.append(
            f"<tr>"
            f"<td style='font-weight:600;'>{icon} {name}{auto_badge}</td>"
            f"<td><span class='mono'>{_e(short_arn)}</span></td>"
            f"<td>{status_badge}{state_badge}</td>"
            f"<td>{action_badge}</td>"
            f"<td style='font-size:12px;color:#94a3b8;max-width:300px;'>{reason}</td>"
            f"<td class='mono'>{cost_str}</td>"
            f"</tr>"
        )

    h.append("</table>")
    return "\n".join(h)


def _action_badge(action):
    """Render an action badge."""
    colors = {
        "never_destroy": ("#ef4444", "rgba(239,68,68,0.15)"),
        "keep_stopped": ("#3b82f6", "rgba(59,130,246,0.15)"),
        "manual_renewal": ("#f59e0b", "rgba(245,158,11,0.15)"),
    }
    labels = {
        "never_destroy": "HK EXEMPT",
        "keep_stopped": "KEEP STOPPED",
        "manual_renewal": "MANUAL RENEWAL",
    }
    color, bg = colors.get(action, ("#6b7280", "rgba(107,114,128,0.15)"))
    label = labels.get(action, _e(str(action).upper()))
    return f"<span class='badge' style='background:{bg};color:{color};'>{label}</span>"


def _truncate_arn(arn, max_len=55):
    """Truncate an ARN for display, keeping the meaningful suffix."""
    if not arn or len(arn) <= max_len:
        return arn
    return "..." + arn[-(max_len - 3) :]

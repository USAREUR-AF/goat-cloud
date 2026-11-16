"""
Access Management page — /access (admin only).

Shows all users who have access to the compliance dashboard,
with role, source, enrollment date, last-visited timestamp,
and revoke action for dynamic viewers.
"""

import html as html_mod
from datetime import UTC, datetime

from views_base import _admin_only, _badge, _e, _js_json, _layout, paginate


def _format_ts(ts):
    """Format a unix timestamp as relative + absolute."""
    if ts is None:
        return '<span style="color:#4b5563;">—</span>'
    try:
        dt = datetime.fromtimestamp(int(ts), tz=UTC)
        iso = dt.strftime("%Y-%m-%d %H:%M")
        delta = datetime.now(UTC) - dt
        if delta.days > 30:
            relative = f"{delta.days // 30}mo ago"
        elif delta.days > 0:
            relative = f"{delta.days}d ago"
        elif delta.seconds >= 3600:
            relative = f"{delta.seconds // 3600}h ago"
        elif delta.seconds >= 60:
            relative = f"{delta.seconds // 60}m ago"
        else:
            relative = "just now"
        return (
            f'<span title="{_e(iso)} UTC">{_e(relative)}</span>'
            f'<br><span style="font-size:10px;color:#4b5563;">{_e(iso)}</span>'
        )
    except (ValueError, TypeError, OSError):
        return '<span style="color:#4b5563;">?</span>'


def _source_display(source):
    """Render the access source as badge + optional admin attribution."""
    if source == "static-config":
        return _badge("config", "gray")
    if source.startswith("invite-link:"):
        admin_email = source.split(":", 1)[1]
        return _badge("invite", "blue") + f'<br><span style="font-size:10px;color:#4b5563;">by {_e(admin_email)}</span>'
    if source == "invite-link":
        return _badge("invite", "blue")
    if source.startswith("promoted:"):
        admin_email = source.split(":", 1)[1]
        suffix = f'<br><span style="font-size:10px;color:#4b5563;">by {_e(admin_email)}</span>' if admin_email else ""
        return _badge("promoted", "green") + suffix
    if source == "unknown":
        return _badge("unknown", "gray")
    return _badge(_e(source[:20]), "gray")


def render_access(access_list, user_email="", page=1):
    """Render the /access admin page."""
    admin_count = sum(1 for u in access_list if u["role"] == "admin")
    viewer_count = sum(1 for u in access_list if u["role"] == "viewer")
    dynamic_count = sum(1 for u in access_list if u.get("source", "").startswith("invite-link"))

    h = [
        f"<h1>ACCESS MANAGEMENT{_admin_only('Admin-only page')}</h1>",
        '<p style="color:#6b7280;font-size:12px;margin:-12px 0 20px;">',
        "All users with access to the compliance dashboard. ",
        "Static users are configured via environment variables. ",
        "Dynamic viewers can be revoked.</p>",
    ]

    # Summary cards
    h.append('<div class="cards">')
    h.append(f'<div class="card"><div class="label">Total Users</div><div class="value">{len(access_list)}</div></div>')
    h.append(
        f'<div class="card"><div class="label">Admins</div>'
        f'<div class="value" style="color:#22c55e;">{admin_count}</div></div>'
    )
    h.append(
        f'<div class="card"><div class="label">Viewers</div>'
        f'<div class="value" style="color:#3b82f6;">{viewer_count}</div></div>'
    )
    h.append(
        f'<div class="card"><div class="label">Dynamic</div>'
        f'<div class="value" style="color:#f59e0b;">{dynamic_count}</div></div>'
    )
    h.append("</div>")

    if not access_list:
        h.append('<div class="empty">No users configured.</div>')
        return _layout("Access", "\n".join(h), user_email, active_page="access")

    # Sort: admins first, then by email
    access_list.sort(key=lambda u: (0 if u["role"] == "admin" else 1, u["email"]))

    page_items, pagination_html, _total = paginate(access_list, page, page_size=50)

    h.append("<table><thead><tr>")
    h.append("<th>Email</th><th>Role</th><th>Source</th>")
    h.append("<th>Enrolled</th><th>Last Visited</th><th>Actions</th>")
    h.append("</tr></thead><tbody>")

    for u in page_items:
        email = u["email"]
        role = u["role"]
        source = u.get("source", "")

        role_badge = _badge("admin", "green") if role == "admin" else _badge("viewer", "blue")

        email_enc = html_mod.escape(email, quote=True)
        actions = []

        # Revoke: only for invite-link viewers
        if source.startswith("invite-link"):
            actions.append(
                f'<form method="post" action="/access/revoke" style="display:inline;">'
                f'<input type="hidden" name="email" value="{email_enc}">'
                f'<button type="submit" class="btn btn-danger" style="font-size:11px;" '
                f'onclick="return confirm({_e(_js_json(f"Revoke access for {email}?"))})">'
                f"Revoke</button></form>"
            )

        # Promote: viewers (non-static-admin) can be promoted
        if role == "viewer":
            actions.append(
                f'<form method="post" action="/access/promote" style="display:inline;">'
                f'<input type="hidden" name="email" value="{email_enc}">'
                f'<button type="submit" class="btn btn-primary" style="font-size:11px;" '
                f'onclick="return confirm({_e(_js_json(f"Promote {email} to admin?"))})">'
                f"Promote</button></form>"
            )

        # Demote: dynamic admins (promoted) can be demoted — static admins cannot
        if role == "admin" and source.startswith("promoted:"):
            actions.append(
                f'<form method="post" action="/access/demote" style="display:inline;">'
                f'<input type="hidden" name="email" value="{email_enc}">'
                f'<button type="submit" class="btn btn-danger" style="font-size:11px;" '
                f'onclick="return confirm({_e(_js_json(f"Demote {email} back to viewer?"))})">'
                f"Demote</button></form>"
            )

        h.append("<tr>")
        h.append(f"<td>{_e(email)}</td>")
        h.append(f"<td>{role_badge}</td>")
        h.append(f"<td>{_source_display(source)}</td>")
        h.append(f"<td>{_format_ts(u.get('enrolled_at'))}</td>")
        h.append(f"<td>{_format_ts(u.get('last_visited'))}</td>")
        h.append(f"<td>{' '.join(actions)}</td>")
        h.append("</tr>")

    h.append("</tbody></table>")
    h.append(pagination_html)

    h.append('<div style="margin-top:20px;"><a href="/invite" class="btn btn-primary">Generate Invite Link</a></div>')

    return _layout("Access", "\n".join(h), user_email, active_page="access")

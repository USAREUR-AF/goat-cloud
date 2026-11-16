"""
Compliance Dashboard — Lambda handler (API Gateway HTTP API).

Routes all requests from the catch-all API Gateway route.
Auth via an OIDC proxy or the ALB's authenticate-oidc action (see auth.py).
"""

import base64
import gzip
import html as _html
import os
import re
import urllib.parse
from html import escape as _esc

import site_config
from actions import (
    abort_destroy,
    approve_teardown,
    create_override,
    delete_override,
    extend_grace_period,
    revoke_approval,
    trigger_early_destroy,
)
from arch_custom import render_architecture_custom
from arch_drawio import generate_drawio_xml
from arch_service import render_architecture_service
from auth import (
    add_dynamic_viewer,
    demote_from_admin,
    get_access_list,
    get_all_viewers,
    get_user_email,
    is_admin,
    is_authorized,
    is_same_origin_post,
    promote_to_admin,
    record_last_visited,
    require_admin,
    revoke_dynamic_viewer,
)
from compliance_schema import SEVERITY_ORDER, repo_slug
from data import (
    clear_all_caches,
    get_24h_previous_report,
    get_all_findings,
    get_all_hk_records,
    get_compliance_report,
    get_compliant_summary,
    get_cost_forecast,
    get_current_month_spend,
    get_expiring_soon,
    get_finding_by_arn,
    get_findings_summary,
    get_full_infra_graph,
    get_html_report,
    get_import_hint,
    get_job_statuses,
    get_latest_hk_run_html,
    get_latest_hk_run_report,
    get_latest_report_date,
    get_month_start_report,
    get_overrides,
    get_pending_destroys,
    get_tf_module_tree,
    get_tf_resource_info,
    get_volume_live_context,
    get_volume_parent_instance,
)
from data_core import REPOSITORY_URL, scan_freshness
from delta import compute_delta
from views import (
    render_all_resources,
    render_compliant,
    render_cure,
    render_docs,
    render_error,
    render_findings,
    render_guide,
    render_home,
    render_overrides,
    render_overview,
    render_pending,
    render_tree,
)
from views_access import render_access
from views_architecture import render_architecture
from views_base import _safe_path
from views_burn import render_burn
from views_changes import render_changes
from views_detail import render_detail
from views_interactions import render_interactions, render_interactions_global
from views_network import render_network
from views_protected import render_protected
from views_repos import render_repos
from views_security import render_security
from views_self import render_self
from views_services import render_services

# Per-repo views (lazy imports in _handle_repo_prefix for startup perf)
# from arch_repo import render_repo_architecture
# from views_repo_detail import render_repo_detail
# from views_tree import render_repo_tree
# from views_network import render_repo_network
# from views_interactions import render_repo_interactions

_accept_encoding = ""
# True only for ALB-fronted requests. A request through the Function URL may
# come from an identity proxy in front of it, and such a proxy can drop the
# Content-Encoding response header while streaming a gzip body, so a gzipped
# response would render as garbage. Gzip is therefore applied ONLY on the ALB
# path; the Function URL path returns plain HTML.
_is_alb = False


def handler(event, context):
    """ALB Lambda entry point."""
    return _dispatch_request(event, context)


def _dispatch_request(event, context):
    """Route an ALB/API-Gateway request to the appropriate handler."""
    global _accept_encoding, _is_alb
    _accept_encoding = (event.get("headers") or {}).get("accept-encoding", "")

    # Detect ALB vs API Gateway event format
    request_ctx = event.get("requestContext", {})
    _is_alb = "elb" in request_ctx
    if "elb" in request_ctx:
        # ALB event: method and path at top level
        method = event.get("httpMethod", "GET")
        path = event.get("path", "/")
    else:
        # API Gateway HTTP API event: nested under requestContext.http
        http = request_ctx.get("http", {})
        method = http.get("method", "GET")
        path = http.get("path", "/")

    # Extract the user's email from the sign-in headers (ALB OIDC or an identity proxy)
    user_email = get_user_email(event)

    # Parse query string — ALB does NOT URL-decode queryStringParameters
    raw_qs = event.get("queryStringParameters") or {}
    qs = {k: urllib.parse.unquote(v) for k, v in raw_qs.items()}

    # Parse form body for POST requests
    form_data = {}
    if method == "POST":
        form_data = _parse_form_body(event)

    # Route dispatch
    try:
        if method == "GET":
            # Viewer gate: restrict access to authorized viewers
            # /enroll is exempt (it's the enrollment endpoint itself)
            if path != "/enroll" and not is_authorized(user_email, event):
                return _html_response(
                    403,
                    render_error(
                        "Access Required",
                        "You need an invite link to access this dashboard. Contact a cloud admin to request access.",
                        403,
                    ),
                )

            # Track last access (throttled to once per 5 min per user)
            record_last_visited(user_email)

            # Architecture render routes (direct response)
            if path in _GET_RENDER_ROUTES:
                return _html_response(200, _GET_RENDER_ROUTES[path](user_email))
            # Architecture drawio (special — downloads file)
            if path == "/architecture/drawio":
                return _drawio_download(user_email)
            # Prefix match: /architecture/custom/ (service-level diagram)
            if path.startswith("/architecture/custom/"):
                module_address = urllib.parse.unquote(path[len("/architecture/custom/") :])
                return _html_response(200, render_architecture_service(module_address, user_email))
            # Prefix match: /architecture/interactions/ (per-service D3 graph)
            if path.startswith("/architecture/interactions/"):
                module_address = urllib.parse.unquote(path[len("/architecture/interactions/") :])
                return _html_response(200, render_interactions(module_address, user_email))
            # Prefix match: /detail/
            if path.startswith("/detail/"):
                module_address = urllib.parse.unquote(path[len("/detail/") :])
                return _html_response(200, render_detail(module_address, user_email))
            # Prefix match: /repos/<slug>/... (per-repo views)
            if path.startswith("/repos/") and path != "/repos":
                return _handle_repo_prefix(path, user_email)
            # Standard routes
            route = _GET_ROUTES.get(path)
            if route:
                return _dispatch(route, user_email, qs, form_data, context)
            return _html_response(404, render_error("Not Found", f"Page not found: {path}", 404))

        elif method == "POST":
            # Every POST changes state: accept it only from a page of this site.
            if not is_same_origin_post(event):
                return _html_response(
                    403,
                    render_error(
                        "Forbidden",
                        "This action must be submitted from the dashboard itself. Reload the page and try again.",
                        403,
                    ),
                )
            record_last_visited(user_email)
            route = _POST_ROUTES.get(path)
            if route:
                return _dispatch(route, user_email, qs, form_data, context)
            return _html_response(404, render_error("Not Found", f"Action not found: {path}", 404))

        return _html_response(405, render_error("Method Not Allowed", f"Method {method} not supported"))

    except Exception as e:
        print(f"Error handling {method} {path}: {e}")
        import traceback

        traceback.print_exc()
        try:
            return _html_response(500, render_error("Internal Error", f"An error occurred: {e}"))
        except Exception:
            return {"statusCode": 500, "body": "Internal Server Error", "headers": {"Content-Type": "text/plain"}}


# =============================================================================
# GET Handlers
# =============================================================================


def _handle_home(user_email, qs=None):
    """Dashboard home with summary cards."""
    report_date = get_latest_report_date()
    report = get_compliance_report(report_date)
    summary = get_findings_summary(report)
    pending = get_pending_destroys()
    overrides = get_overrides()

    # Build job statuses: scanner from report, HK + reaper from SSM
    freshness = scan_freshness(report)
    job_statuses = get_job_statuses()
    job_statuses["compliance-scanner"] = {
        "last_run": report.get("generated_at", ""),
        "duration_seconds": report.get("scan_duration_seconds", 0),
        "status": freshness["status"],
        "summary": f"{summary.get('resources', 0)} resources, {summary.get('high', 0) + summary.get('medium', 0) + summary.get('low', 0)} findings",
    }

    flash = (qs or {}).get("flash", "")
    body = render_home(
        summary,
        len(pending),
        len(overrides),
        report_date,
        user_email,
        flash=flash,
        job_statuses=job_statuses,
        scan_freshness=freshness,
    )
    return _html_response(200, body)


def _handle_compliant(user_email):
    """Compliant resources — resources that passed all checks."""
    report = get_compliance_report()
    compliant = get_compliant_summary(report)
    expiring = get_expiring_soon(report)
    body = render_compliant(compliant, user_email, expiring_soon=expiring)
    return _html_response(200, body)


def _handle_all(user_email):
    """All resources — combined compliant + non-compliant overview."""
    report = get_compliance_report()
    compliant = get_compliant_summary(report)
    body = render_all_resources(compliant, user_email)
    return _html_response(200, body)


def _handle_cure(user_email, qs):
    """Cure page — detailed remediation plan for a specific resource."""
    resource_arn = qs.get("arn", "")
    if not resource_arn:
        return _html_response(400, render_error("Missing ARN", "Resource ARN is required."))

    report = get_compliance_report()
    findings = get_finding_by_arn(report, resource_arn)
    tf_info = get_tf_resource_info(resource_arn)

    # Volume-to-instance tracing for EBS volumes
    volume_parent = None
    volume_live = None
    if ":volume/" in resource_arn:
        volume_parent = get_volume_parent_instance(resource_arn)
        vol_id = resource_arn.split("/")[-1]
        volume_live = get_volume_live_context(vol_id)

    # Import hint for untracked resources
    import_hint = None
    tags = findings[0].get("tags", {}) if findings else {}
    if tags.get("ManagedBy", "").lower() != "terraform":
        import_hint = get_import_hint(resource_arn)

    body = render_cure(
        findings,
        tf_info,
        resource_arn,
        user_email,
        volume_parent=volume_parent,
        import_hint=import_hint,
        volume_live=volume_live,
    )
    return _html_response(200, body)


def _handle_findings(user_email, qs):
    """Findings list with optional severity and contact filters, optional contact grouping."""
    severity_filter = qs.get("severity")
    group_by = qs.get("group", "")  # "contact" to group by owner
    contact_filter = qs.get("contact", "")  # filter to single contact
    report = get_compliance_report()
    findings = get_all_findings(report, severity_filter)
    info_findings = get_all_findings(report, severity_filter=None, include_info=True)
    info_findings = [f for f in info_findings if f.get("severity") == "INFO"]

    # Cross-reference with hunterkiller state for enforcement status
    hk_records = get_all_hk_records()
    hk_by_arn = {item.get("resource_arn", ""): item for item in hk_records}
    for f in findings:
        arn = f.get("resource_arn", "")
        if arn in hk_by_arn:
            hk = hk_by_arn[arn]
            f["hk_status"] = hk.get("status", "")
            f["approval_status"] = hk.get("approval_status", "")

    # Sort by severity (HIGH first), then cost (highest first)
    findings.sort(
        key=lambda f: (
            SEVERITY_ORDER.get(f.get("severity", ""), 3),
            -(float(f.get("estimated_monthly_cost") or 0)),
        )
    )

    try:
        page = max(1, int(qs.get("page", "1") or "1"))
    except (ValueError, TypeError):
        page = 1
    body = render_findings(findings, user_email, severity_filter, group_by, contact_filter, page, info_findings)
    return _html_response(200, body)


def _handle_tree(user_email):
    """TF state tree — module hierarchy with compliance heatmap."""
    report = get_compliance_report()
    tree = get_tf_module_tree(report)
    body = render_tree(tree, user_email)
    return _html_response(200, body)


def _handle_changes(user_email):
    """24-hour compliance delta — what changed since the previous scan."""
    today_date = get_latest_report_date()
    today_report = get_compliance_report(today_date)

    yesterday_date, yesterday_report = get_24h_previous_report(today_date)

    if not yesterday_report:
        body = render_changes(None, today_date, None, user_email)
    else:
        delta = compute_delta(today_report, yesterday_report, include_resolution=True)
        body = render_changes(delta, today_date, yesterday_date, user_email)

    return _html_response(200, body)


def _handle_repos(user_email):
    """Repos page — multi-repo IaC registry overview."""
    body = render_repos(user_email)
    return _html_response(200, body)


def _handle_satellites(user_email):
    """Redirect to repos page (satellites was merged into repos)."""
    return {"statusCode": 302, "headers": {"Location": "/repos"}, "body": ""}


def _handle_services(user_email):
    """Services table — auto-generated catalog from MODULE_META + live state."""
    body = render_services(user_email)
    return _html_response(200, body)


def _handle_security(user_email):
    """Security posture — exposure map, auth gaps, container images, ECR findings."""
    body = render_security(user_email)
    return _html_response(200, body)


def _handle_burn(user_email):
    """Cost burn analysis — spend breakdown by service, type, and region."""
    today_date = get_latest_report_date()
    report = get_compliance_report(today_date)
    forecast = get_cost_forecast()
    month_spend = get_current_month_spend()
    # Use month-start baseline (not just previous day) to catch all resources
    # destroyed at any point during the billing month, not just since last scan.
    _, baseline_report = get_month_start_report(today_date)
    body = render_burn(report, user_email, forecast=forecast, month_spend=month_spend, previous_report=baseline_report)
    return _html_response(200, body)


def _handle_self(user_email, qs, context):
    """Self-observability — dashboard monitoring its own health."""
    report = get_compliance_report()
    flash = (qs or {}).get("flash", "")
    body = render_self(report, context, user_email, admin=is_admin(user_email), flash=flash)
    resp = _html_response(200, body)
    resp["headers"]["Cache-Control"] = "no-store"
    return resp


def _handle_protected(user_email):
    """Protected resources — items exempt from hunterkiller teardown."""
    body = render_protected(user_email)
    return _html_response(200, body)


def _handle_network(user_email):
    """D3 force-directed network visualization of full account infra."""
    report = get_compliance_report()
    graph = get_full_infra_graph(report)
    body = render_network(graph, user_email)
    return _html_response(200, body)


def _handle_architecture(user_email):
    """Architecture comparison page — links to all approaches."""
    body = render_architecture(user_email)
    return _html_response(200, body)


def _handle_pending(user_email, qs):
    """Pending destroys with escape hatch controls."""
    from actions import HK_EXECUTOR_ARMED

    try:
        page = max(1, int(qs.get("page", "1") or "1"))
    except (ValueError, TypeError):
        page = 1
    pending = get_pending_destroys()
    body = render_pending(pending, user_email, is_admin(user_email), executor_armed=HK_EXECUTOR_ARMED, page=page)
    return _html_response(200, body)


def _handle_overrides(user_email, qs):
    """Overrides management page."""
    try:
        page = max(1, int(qs.get("page", "1") or "1"))
    except (ValueError, TypeError):
        page = 1
    overrides = get_overrides()
    body = render_overrides(overrides, user_email, is_admin(user_email), page=page)
    return _html_response(200, body)


def _handle_overview(user_email):
    """Platform overview — what everything is and how it fits together."""
    body = render_overview(user_email)
    return _html_response(200, body)


def _handle_guide(user_email):
    """Guide page — annotated explainer of the dashboard."""
    body = render_guide(user_email)
    return _html_response(200, body)


def _handle_docs(user_email):
    """Docs page — compliance rules and scanner documentation."""
    body = render_docs(user_email)
    return _html_response(200, body)


def _handle_report_download(user_email):
    """Download the latest scan report JSON (admin only)."""
    error = require_admin(user_email)
    if error:
        return _html_response(
            403,
            render_error(
                "Forbidden", "Report downloads require admin access. Contact a cloud admin to request access."
            ),
        )

    import json as json_mod

    report_date = get_latest_report_date()
    report = get_compliance_report(report_date)
    if not report:
        return _html_response(404, render_error("Not Found", "No compliance report available."))

    return {
        "statusCode": 200,
        "statusDescription": "200 OK",
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Disposition": f'attachment; filename="compliance-report-{_filename_part(report_date)}.json"',
            **_DOWNLOAD_HEADERS,
        },
        "body": json_mod.dumps(report, indent=2, default=str),
    }


def _handle_html_report_download(user_email):
    """Download the styled HTML email report (admin only)."""
    error = require_admin(user_email)
    if error:
        return _html_response(
            403,
            render_error(
                "Forbidden", "Report downloads require admin access. Contact a cloud admin to request access."
            ),
        )

    report_date = get_latest_report_date()
    html = get_html_report(report_date)
    if not html:
        return _html_response(
            404, render_error("Not Found", "HTML report not available. It will be generated on the next scanner run.")
        )

    return {
        "statusCode": 200,
        "statusDescription": "200 OK",
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Disposition": f'attachment; filename="compliance-report-{_filename_part(report_date)}.html"',
            **_DOWNLOAD_HEADERS,
        },
        "body": html,
    }


def _handle_hk_report_download(user_email):
    """Download the latest hunterkiller run report JSON (admin only)."""
    error = require_admin(user_email)
    if error:
        return _html_response(
            403,
            render_error(
                "Forbidden", "Report downloads require admin access. Contact a cloud admin to request access."
            ),
        )

    import json as json_mod

    report = get_latest_hk_run_report()
    if not report:
        return _html_response(404, render_error("Not Found", "No hunterkiller run data available."))

    batch_id = report.get("batch_id", "unknown")
    return {
        "statusCode": 200,
        "statusDescription": "200 OK",
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Disposition": f'attachment; filename="hunterkiller-report-{_filename_part(batch_id)}.json"',
            **_DOWNLOAD_HEADERS,
        },
        "body": json_mod.dumps(report, indent=2, default=str),
    }


def _handle_hk_html_report_download(user_email):
    """Download the latest hunterkiller run report as styled HTML (admin only)."""
    error = require_admin(user_email)
    if error:
        return _html_response(
            403,
            render_error(
                "Forbidden", "Report downloads require admin access. Contact a cloud admin to request access."
            ),
        )

    html = get_latest_hk_run_html()
    if not html:
        return _html_response(404, render_error("Not Found", "No hunterkiller run data available."))

    report = get_latest_hk_run_report()
    batch_id = report.get("batch_id", "unknown") if report else "unknown"
    return {
        "statusCode": 200,
        "statusDescription": "200 OK",
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Disposition": f'attachment; filename="hunterkiller-report-{_filename_part(batch_id)}.html"',
            **_DOWNLOAD_HEADERS,
        },
        "body": html,
    }


# =============================================================================
# POST Handlers (require admin)
# =============================================================================


def _handle_create_override(user_email, form_data):
    """Create a false positive or exemption override."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    resource_arn = form_data.get("resource_arn", "").strip()
    override_type = form_data.get("override_type", "false_positive")
    reason = form_data.get("reason", "").strip()
    resource_type = form_data.get("resource_type", "")
    resource_name = form_data.get("resource_name", "")

    if not resource_arn:
        return _html_response(400, render_error("Missing Field", "Resource ARN is required."))
    if not reason:
        return _html_response(400, render_error("Missing Field", "A reason is required for overrides."))

    create_override(
        resource_arn=resource_arn,
        override_type=override_type,
        reason=reason,
        created_by=user_email,
        resource_type=resource_type,
        resource_name=resource_name,
    )

    return _redirect("/overrides", f"Override created for {resource_arn[-40:]}")


def _handle_delete_override(user_email, form_data):
    """Remove an override."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    resource_arn = form_data.get("resource_arn", "").strip()
    if not resource_arn:
        return _html_response(400, render_error("Missing Field", "Resource ARN is required."))

    delete_override(resource_arn)
    return _redirect("/overrides", "Override removed.")


def _handle_access(user_email, qs):
    """Access management page (admin only)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    try:
        page = max(1, int(qs.get("page", "1") or "1"))
    except (ValueError, TypeError):
        page = 1

    access_list = get_access_list()
    body = render_access(access_list, user_email, page=page)
    return _html_response(200, body)


def _handle_revoke_viewer(user_email, form_data):
    """Revoke a dynamic viewer's access (admin only)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    email = form_data.get("email", "").strip().lower()
    if not email:
        return _html_response(400, render_error("Missing Field", "Email is required."))

    revoke_dynamic_viewer(email)
    return _redirect("/access", f"Access revoked for {email}")


def _handle_promote_viewer(user_email, form_data):
    """Promote a viewer to admin (admin only)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    email = form_data.get("email", "").strip().lower()
    if not email:
        return _html_response(400, render_error("Missing Field", "Email is required."))

    promote_to_admin(email, promoted_by=user_email)
    return _redirect("/access", f"Promoted {email} to admin")


def _handle_demote_admin(user_email, form_data):
    """Demote a dynamic admin back to viewer (admin only)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    email = form_data.get("email", "").strip().lower()
    if not email:
        return _html_response(400, render_error("Missing Field", "Email is required."))

    from auth import ADMIN_EMAILS

    if email in ADMIN_EMAILS:
        return _html_response(
            400, render_error("Cannot Demote", "Static admins cannot be demoted. Remove from ADMIN_EMAILS env var.")
        )

    demote_from_admin(email)
    return _redirect("/access", f"Demoted {email} back to viewer")


def _handle_abort(user_email, form_data):
    """Abort a pending destroy (escape hatch)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    resource_arn = form_data.get("resource_arn", "").strip()
    if not resource_arn:
        return _html_response(400, render_error("Missing Field", "Resource ARN is required."))

    if not abort_destroy(resource_arn, user_email):
        return _html_response(404, render_error("Not Found", "No pending destroy is recorded for that resource.", 404))
    return _redirect("/pending", f"Destruction ABORTED for {resource_arn[-40:]}")


def _handle_extend(user_email, form_data):
    """Extend grace period."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    resource_arn = form_data.get("resource_arn", "").strip()
    hours = form_data.get("hours", "24")

    if not resource_arn:
        return _html_response(400, render_error("Missing Field", "Resource ARN is required."))

    try:
        hours = int(hours)
    except ValueError:
        hours = 24

    extend_grace_period(resource_arn, hours, user_email)
    return _redirect("/pending", f"Grace period extended by {hours} hours.")


def _handle_approve(user_email, form_data):
    """Approve a resource for teardown (admin action)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    resource_arn = form_data.get("resource_arn", "").strip()
    if not resource_arn:
        return _html_response(400, render_error("Missing Field", "Resource ARN is required."))

    if not approve_teardown(resource_arn, user_email):
        return _html_response(
            409,
            render_error("Not Eligible", "Only a resource with a current warning can be approved for teardown.", 409),
        )
    return _redirect("/pending", f"Teardown APPROVED for {resource_arn[-40:]}")


def _handle_revoke(user_email, form_data):
    """Revoke a teardown approval (admin action)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    resource_arn = form_data.get("resource_arn", "").strip()
    if not resource_arn:
        return _html_response(400, render_error("Missing Field", "Resource ARN is required."))

    if not revoke_approval(resource_arn, user_email):
        return _html_response(404, render_error("Not Found", "No record exists for that resource.", 404))
    return _redirect("/pending", f"Approval REVOKED for {resource_arn[-40:]}")


def _handle_destroy(user_email, form_data):
    """Trigger early destruction."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    resource_arn = form_data.get("resource_arn", "").strip()
    if not resource_arn:
        return _html_response(400, render_error("Missing Field", "Resource ARN is required."))

    result = trigger_early_destroy(resource_arn, user_email)

    if "error" in result:
        return _html_response(500, render_error("Error", result["error"]))

    return _redirect("/pending", f"Early destruction triggered for {resource_arn[-40:]}. Check logs for status.")


def _handle_abort_all(user_email, form_data):
    """Abort all pending destroys (bulk escape hatch)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    from actions import abort_all

    count = abort_all(user_email)
    return _redirect("/pending", f"ABORTED {count} pending destroys.")


def _handle_extend_all(user_email, form_data):
    """Extend grace period for all warned resources (bulk)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    hours = form_data.get("hours", "24")
    try:
        hours = int(hours)
    except ValueError:
        hours = 24

    from actions import extend_all

    count = extend_all(hours, user_email)
    return _redirect("/pending", f"Extended grace period by {hours}h for {count} resources.")


def _handle_approve_all(user_email, form_data):
    """Approve all warned resources for teardown (bulk)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    from actions import approve_all

    count = approve_all(user_email)
    return _redirect("/pending", f"APPROVED {count} resources for teardown.")


def _handle_destroy_all(user_email, form_data):
    """Auto-approve all pending resources, then trigger batch enforcement."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    from actions import HK_EXECUTOR_ARMED, HK_EXECUTOR_FUNCTION_NAME, approve_all

    if not HK_EXECUTOR_ARMED or not HK_EXECUTOR_FUNCTION_NAME:
        return _html_response(
            400, render_error("Not Armed", "Executor not armed — set executor_armed=true in Terraform.")
        )

    # Auto-approve any remaining pending resources
    approved_count = approve_all(user_email)

    import json

    import boto3

    # scope=all_warned: every warned record, whatever its batch; the
    # executor's approval and protection gates still apply to each.
    payload = {
        "mode": "enforce",
        "scope": "all_warned",
        "triggered_by": user_email,
    }

    try:
        client = boto3.client("lambda")
        client.invoke(
            FunctionName=HK_EXECUTOR_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps(payload),
        )
    except Exception as e:
        return _html_response(500, render_error("Error", f"Failed to invoke executor: {e}"))

    msg = f"Approved {approved_count} resources. " if approved_count else ""
    msg += "Batch enforcement triggered — executor processing all approved resources."
    return _redirect("/pending", msg)


# =============================================================================
# Viewer Gate: Enrollment + Invite Handlers
# =============================================================================

# Module-level cache for the HMAC secret
_invite_hmac_secret = None


def _get_invite_hmac_secret():
    """Read HMAC secret from Secrets Manager (cached per warm container)."""
    global _invite_hmac_secret
    if _invite_hmac_secret is not None:
        return _invite_hmac_secret

    import boto3

    secret_arn = os.environ.get("INVITE_HMAC_SECRET_ARN", "")
    if not secret_arn:
        return None

    sm = boto3.client("secretsmanager")
    resp = sm.get_secret_value(SecretId=secret_arn)
    _invite_hmac_secret = resp["SecretString"]
    return _invite_hmac_secret


def _handle_enroll(user_email, qs):
    """Enroll a viewer via invite link token."""
    import hashlib
    import hmac
    import time

    token = qs.get("token", "")
    if not token:
        return _html_response(
            400,
            render_error(
                "Missing Token",
                "This URL requires a valid invite token. Please use the complete invite link you were given.",
            ),
        )

    if not user_email:
        return _html_response(
            403, render_error("Authentication Required", "Please authenticate via the login page first.")
        )

    # Check if already authorized
    if is_authorized(user_email):
        return _html_response(
            200,
            _render_enroll_result(
                "You Already Have Access", f"{user_email} is already authorized to view the dashboard.", success=True
            ),
        )

    # Validate HMAC token
    hmac_secret = _get_invite_hmac_secret()
    if not hmac_secret:
        return _html_response(
            500, render_error("Configuration Error", "Viewer gate is not properly configured. Contact a cloud admin.")
        )

    invited_by = qs.get("invited_by", "")
    try:
        expiry_str, signature = token.split(".", 1)
        expiry = int(expiry_str)

        # Try new format first (with admin attribution in HMAC payload)
        valid = False
        if invited_by:
            payload = f"{expiry_str}:{invited_by}"
            expected = hmac.new(hmac_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            valid = hmac.compare_digest(signature, expected)

        if not valid:
            # Fallback: old format (no attribution)
            expected_old = hmac.new(hmac_secret.encode(), expiry_str.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected_old):
                return _html_response(
                    403,
                    render_error("Invalid Token", "The invite token is not valid. Please request a new invite link."),
                )
            invited_by = ""  # Old token — no attribution available

        if time.time() > expiry:
            return _html_response(
                403,
                render_error(
                    "Expired Token", "This invite link has expired. Please request a new one from a cloud admin."
                ),
            )
    except (ValueError, IndexError):
        return _html_response(
            403,
            render_error(
                "Invalid Token Format", "The invite token format is invalid. Please use the complete invite link."
            ),
        )

    # Enroll the viewer with admin attribution
    enrolled_by = f"invite-link:{invited_by}" if invited_by else "invite-link"
    add_dynamic_viewer(user_email, enrolled_by=enrolled_by)
    return _html_response(
        200,
        _render_enroll_result(
            "You're All Set!", f"{user_email} has been added to the dashboard viewer list.", success=True
        ),
    )


def _render_enroll_result(title, message, success=True):
    """Render the enrollment result page."""

    dashboard_url = os.environ.get("DASHBOARD_URL", "/")
    color = "#2d7d46" if success else "#c0392b"
    return (
        "<!DOCTYPE html><html><head><title>" + _esc(title) + "</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:60px auto;padding:20px;color:#333}"
        "h1{font-size:1.5em}"
        "p{line-height:1.6}"
        ".status{color:" + color + ";font-weight:600}"
        "a{color:#2563eb}"
        ".btn{display:inline-block;margin-top:20px;padding:10px 24px;background:#2563eb;color:#fff;"
        "text-decoration:none;border-radius:6px;font-weight:500}"
        ".btn:hover{background:#1d4ed8}"
        "</style></head><body>"
        "<h1>Compliance Dashboard</h1>"
        '<p class="status">' + _esc(title) + "</p>"
        "<p>" + _esc(message) + "</p>"
        '<a class="btn" href="' + _esc(dashboard_url) + '">Go to Dashboard</a>'
        "</body></html>"
    )


def _handle_invite_page(user_email):
    """Show the invite link generation form (admin only)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    viewers = get_all_viewers()
    return _html_response(200, _render_invite_form(user_email, viewers))


def _handle_invite_generate(user_email, form_data):
    """Generate an invite link (admin only, POST)."""
    import hashlib
    import hmac
    import time

    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    days = form_data.get("days", "7")
    try:
        days = int(days)
        if days < 1 or days > 365:
            days = 7
    except (ValueError, TypeError):
        days = 7

    hmac_secret = _get_invite_hmac_secret()
    if not hmac_secret:
        return _html_response(
            500,
            render_error(
                "Configuration Error",
                "Viewer gate HMAC secret not configured. Set enable_viewer_gate=true in Terraform.",
            ),
        )

    expiry = int(time.time()) + days * 86400
    # Include admin email in HMAC payload for attribution
    payload = f"{expiry}:{user_email}"
    signature = hmac.new(hmac_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = f"{expiry}.{signature}"

    dashboard_url = os.environ.get("DASHBOARD_URL", "")
    admin_param = urllib.parse.quote(user_email, safe="")
    invite_url = f"{dashboard_url}/enroll?token={token}&invited_by={admin_param}"

    viewers = get_all_viewers()
    return _html_response(200, _render_invite_result(user_email, invite_url, days, viewers))


def _render_invite_form(user_email, viewers):
    """Render the invite link generation form."""
    viewer_rows = (
        "".join(f'<tr><td style="padding:4px 12px 4px 0">{_esc(v)}</td></tr>' for v in viewers)
        if viewers
        else '<tr><td style="color:#999">No viewers enrolled yet</td></tr>'
    )

    return (
        "<!DOCTYPE html><html><head><title>Generate Invite Link</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,sans-serif;max-width:700px;margin:40px auto;padding:20px;color:#333}"
        "h1{font-size:1.4em;margin-bottom:4px}"
        ".sub{color:#666;font-size:0.9em;margin-top:0}"
        "h2{font-size:1.1em;margin-top:30px;border-bottom:1px solid #e5e7eb;padding-bottom:6px}"
        "select,button{padding:8px 16px;font-size:0.95em;border:1px solid #d1d5db;border-radius:6px}"
        "button{background:#2563eb;color:#fff;border:none;cursor:pointer;font-weight:500}"
        "button:hover{background:#1d4ed8}"
        "table{font-size:0.9em;margin-top:8px}"
        ".back{color:#2563eb;text-decoration:none;font-size:0.9em}"
        "</style></head><body>"
        '<a class="back" href="/">&larr; Dashboard</a>'
        "<h1>Generate Invite Link</h1>"
        '<p class="sub">Create a time-limited invite link for new dashboard viewers.</p>'
        '<form method="POST" action="/invite" style="margin-top:20px">'
        "<label>Expires in: </label>"
        '<select name="days">'
        '<option value="1">1 day</option>'
        '<option value="7" selected>7 days</option>'
        '<option value="30">30 days</option>'
        "</select> "
        '<button type="submit">Generate Link</button>'
        "</form>"
        "<h2>Current Viewers (" + str(len(viewers)) + ")</h2>"
        "<table>" + viewer_rows + "</table>"
        "</body></html>"
    )


def _render_invite_result(user_email, invite_url, days, viewers):
    """Render the generated invite link page."""
    viewer_rows = (
        "".join(f'<tr><td style="padding:4px 12px 4px 0">{_esc(v)}</td></tr>' for v in viewers)
        if viewers
        else '<tr><td style="color:#999">No viewers enrolled yet</td></tr>'
    )

    return (
        "<!DOCTYPE html><html><head><title>Invite Link Generated</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,sans-serif;max-width:700px;margin:40px auto;padding:20px;color:#333}"
        "h1{font-size:1.4em;margin-bottom:4px}"
        ".sub{color:#666;font-size:0.9em;margin-top:0}"
        "h2{font-size:1.1em;margin-top:30px;border-bottom:1px solid #e5e7eb;padding-bottom:6px}"
        ".url-box{background:#f3f4f6;border:1px solid #d1d5db;border-radius:8px;padding:16px;"
        "word-break:break-all;font-family:monospace;font-size:0.85em;margin:16px 0;position:relative}"
        ".copy-btn{position:absolute;top:8px;right:8px;padding:4px 12px;font-size:0.8em;"
        "background:#2563eb;color:#fff;border:none;border-radius:4px;cursor:pointer}"
        ".copy-btn:hover{background:#1d4ed8}"
        ".note{color:#666;font-size:0.9em}"
        "table{font-size:0.9em;margin-top:8px}"
        ".back{color:#2563eb;text-decoration:none;font-size:0.9em}"
        "a{color:#2563eb}"
        "</style></head><body>"
        '<a class="back" href="/">&larr; Dashboard</a>'
        "<h1>Invite Link Generated</h1>"
        '<p class="sub">Share this link with the person you want to grant dashboard access.</p>'
        '<div class="url-box" id="invite-url">'
        '<button class="copy-btn" onclick="navigator.clipboard.writeText(document.getElementById(\'invite-url-text\').textContent)">'
        "Copy</button>"
        '<span id="invite-url-text">' + _esc(invite_url) + "</span>"
        "</div>"
        '<p class="note">Expires in <strong>'
        + str(days)
        + " day"
        + ("s" if days != 1 else "")
        + "</strong>. "
        + _html.escape(site_config.get("invite_sign_in_note") or "The recipient must sign in when clicking the link.")
        + "</p>"
        '<p><a href="/invite">Generate another link</a></p>'
        "<h2>Current Viewers (" + str(len(viewers)) + ")</h2>"
        "<table>" + viewer_rows + "</table>"
        "</body></html>"
    )


def _drawio_download(user_email):
    """Generate and return a draw.io XML file as a download."""
    xml = generate_drawio_xml(user_email)
    return {
        "statusCode": 200,
        "statusDescription": "200 OK",
        "headers": {
            "Content-Type": "application/xml; charset=utf-8",
            "Content-Disposition": 'attachment; filename="architecture.drawio"',
        },
        "body": xml,
    }


def _handle_logout(user_email):
    """Hand off to OAuth2 Proxy's native sign-out.

    /oauth2/sign_out clears the proxy's session cookie and bounces the user
    back to the dashboard root. An identity provider without an end-session
    endpoint keeps its upstream session — re-navigation
    will silently re-auth via SSO. This is the same effective UX as the
    earlier ALB-cookie-clearing workaround, just without the
    multi-value-headers gymnastics.
    """
    return _redirect("/oauth2/sign_out?rd=/")


def _handle_cache_clear(user_email, form_data):
    """Flush all in-memory caches (admin only). Useful after a deploy."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))
    clear_all_caches()
    # Only a path on this site: no scheme, no //host.
    return _redirect(_safe_path(form_data.get("return_to", "/self"), default="/self"))


def _handle_trigger_scan(user_email):
    """Trigger a compliance scan (admin only)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    from actions import trigger_scan

    result = trigger_scan(triggered_by=user_email)
    if "error" in result:
        return _html_response(500, render_error("Trigger Failed", result["error"]))
    return _redirect("/?flash=scan_triggered")


def _handle_trigger_sprawl(user_email):
    """Trigger the HK sprawl test Lambda (admin only) — creates non-compliant test resources."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    from actions import trigger_sprawl_test

    result = trigger_sprawl_test(triggered_by=user_email)
    if "error" in result:
        return _html_response(500, render_error("Trigger Failed", result["error"]))
    return _redirect("/?flash=sprawl_triggered")


def _handle_trigger_enforce(user_email, form_data):
    """Trigger the hunterkiller enforcer (admin only)."""
    error = require_admin(user_email)
    if error:
        return _html_response(403, render_error("Forbidden", error))

    min_severity = form_data.get("min_severity", "HIGH")
    if min_severity not in ("HIGH", "ALL"):
        return _html_response(400, render_error("Bad Request", f"Invalid severity: {min_severity}"))

    from actions import trigger_enforcer

    result = trigger_enforcer(triggered_by=user_email, min_severity=min_severity)
    if "error" in result:
        return _html_response(500, render_error("Trigger Failed", result["error"]))
    label = "high-only" if min_severity == "HIGH" else "all-severity"
    return _redirect(f"/?flash=enforce_{label}")


# =============================================================================
# Per-repo prefix routing
# =============================================================================


def _handle_repo_prefix(path, user_email):
    """Route /repos/<slug>/... to per-repo views.

    Architecture views follow the canonical hierarchy:
      /repos/<slug>/architecture                        — landing page
      /repos/<slug>/architecture/custom                 — containment diagram
      /repos/<slug>/architecture/custom/<service_type>  — per-service detail
      /repos/<slug>/architecture/interactions            — D3 interaction graph
      /repos/<slug>/architecture/interactions/<svc_type> — focal D3 graph
    """
    # Strip /repos/ prefix and split into slug + sub-path
    rest = path[len("/repos/") :]
    parts = rest.split("/", 1)
    slug = urllib.parse.unquote(parts[0])
    sub_path = parts[1] if len(parts) > 1 else ""

    if not slug:
        return _html_response(404, render_error("Not Found", "No repository specified."))

    # The platform repository uses the canonical architecture views at /architecture/...
    if slug == repo_slug(REPOSITORY_URL) and sub_path.startswith("architecture"):
        canonical_path = "/" + sub_path
        return _redirect(canonical_path)

    if sub_path == "" or sub_path == "/":
        from views_repo_detail import render_repo_detail

        return _html_response(200, render_repo_detail(slug, user_email))

    # ── Architecture hierarchy ──
    elif sub_path == "architecture":
        from arch_repo import render_repo_architecture_landing

        return _html_response(200, render_repo_architecture_landing(slug, user_email))

    elif sub_path == "architecture/custom":
        from arch_repo import render_repo_architecture

        return _html_response(200, render_repo_architecture(slug, user_email))

    elif sub_path.startswith("architecture/custom/"):
        from arch_repo import render_repo_architecture_service

        service_type = urllib.parse.unquote(sub_path[len("architecture/custom/") :])
        return _html_response(200, render_repo_architecture_service(slug, service_type, user_email))

    elif sub_path == "architecture/interactions":
        from views_interactions import render_repo_interactions

        return _html_response(200, render_repo_interactions(slug, user_email))

    elif sub_path.startswith("architecture/interactions/"):
        from views_interactions import render_repo_interactions_focal

        service_type = urllib.parse.unquote(sub_path[len("architecture/interactions/") :])
        return _html_response(200, render_repo_interactions_focal(slug, service_type, user_email))

    # ── Other views ──
    elif sub_path == "tree":
        from views_tree import render_repo_tree

        return _html_response(200, render_repo_tree(slug, user_email))

    elif sub_path == "network":
        from views_network import render_repo_network

        return _html_response(200, render_repo_network(slug, user_email))

    else:
        return _html_response(404, render_error("Not Found", f"Unknown repo view: {sub_path}"))


# =============================================================================
# Helpers
# =============================================================================

HTTP_STATUS_DESCRIPTIONS = {
    200: "200 OK",
    303: "303 See Other",
    400: "400 Bad Request",
    403: "403 Forbidden",
    404: "404 Not Found",
    409: "409 Conflict",
    405: "405 Method Not Allowed",
    500: "500 Internal Server Error",
}


# Sent with every page. Pages use inline scripts and styles, so the policy
# does not restrict script sources; it stops framing, plugin content, <base>
# rewriting and forms that post to another site.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
}

# Sent with stored reports served as downloads: if a browser renders one, it
# runs without scripts and without this site's origin.
_DOWNLOAD_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox",
    "Cache-Control": "no-store",
}


def _filename_part(value):
    """Reduce a value to characters that are safe inside a header filename."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(value))[:80] or "unknown"


def _html_response(status_code, body):
    """Return an HTML response (compatible with both ALB and API Gateway)."""
    headers = {"Content-Type": "text/html; charset=utf-8", **_SECURITY_HEADERS}
    headers["Cache-Control"] = "no-store"
    # Page templates carry the placeholder __BRAND__ for the product name.
    body = body.replace("__BRAND__", _html.escape(site_config.get("brand")))
    resp = {
        "statusCode": status_code,
        "statusDescription": HTTP_STATUS_DESCRIPTIONS.get(status_code, f"{status_code} Unknown"),
        "headers": headers,
        "body": body,
    }
    # Gzip compress large responses when the client supports it — ALB path only.
    # An identity proxy in front of the Function URL can drop Content-Encoding,
    # so gzip there would render as garbage; return plain HTML for it.
    if len(body) > 10_000 and "gzip" in _accept_encoding and _is_alb:
        resp["body"] = base64.b64encode(gzip.compress(body.encode())).decode()
        resp["isBase64Encoded"] = True
        resp["headers"]["Content-Encoding"] = "gzip"
    return resp


def _redirect(path, message=""):
    """HTTP 303 redirect (POST-redirect-GET pattern)."""
    return {
        "statusCode": 303,
        "statusDescription": "303 See Other",
        "headers": {
            "Location": path,
            "Content-Type": "text/html; charset=utf-8",
            **_SECURITY_HEADERS,
        },
        "body": f"Redirecting to {_esc(path)}...",
    }


def _parse_form_body(event):
    """Parse application/x-www-form-urlencoded body from API Gateway."""
    body = event.get("body", "")
    if not body:
        return {}

    # API Gateway may base64-encode the body
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    return dict(urllib.parse.parse_qsl(body, keep_blank_values=True))


# =============================================================================
# Route tables — signature tags: "e" = (email), "eq" = (email, qs),
#   "ec" = (email, context), "ef" = (email, form_data)
# =============================================================================

_GET_ROUTES = {
    "/": (_handle_home, "eq"),
    "/findings": (_handle_findings, "eq"),
    "/compliant": (_handle_compliant, "e"),
    "/cure": (_handle_cure, "eq"),
    "/all": (_handle_all, "e"),
    "/changes": (_handle_changes, "e"),
    "/repos": (_handle_repos, "e"),
    "/satellites": (_handle_satellites, "e"),
    "/services": (_handle_services, "e"),
    "/security": (_handle_security, "e"),
    "/burn": (_handle_burn, "e"),
    "/self": (_handle_self, "eqc"),
    "/protected": (_handle_protected, "e"),
    "/tree": (_handle_tree, "e"),
    "/network": (_handle_network, "e"),
    "/architecture": (_handle_architecture, "e"),
    "/pending": (_handle_pending, "eq"),
    "/overrides": (_handle_overrides, "eq"),
    "/report/download": (_handle_report_download, "e"),
    "/report/html": (_handle_html_report_download, "e"),
    "/report/hunterkiller": (_handle_hk_report_download, "e"),
    "/report/hunterkiller/html": (_handle_hk_html_report_download, "e"),
    "/overview": (_handle_overview, "e"),
    "/guide": (_handle_guide, "e"),
    "/docs": (_handle_docs, "e"),
    "/enroll": (_handle_enroll, "eq"),
    "/invite": (_handle_invite_page, "e"),
    "/access": (_handle_access, "eq"),
    "/logout": (_handle_logout, "e"),
}

_GET_RENDER_ROUTES = {
    "/architecture/custom": render_architecture_custom,
    "/architecture/interactions": render_interactions_global,
}

_POST_ROUTES = {
    "/override": (_handle_create_override, "ef"),
    "/override/delete": (_handle_delete_override, "ef"),
    "/abort": (_handle_abort, "ef"),
    "/extend": (_handle_extend, "ef"),
    "/approve": (_handle_approve, "ef"),
    "/revoke": (_handle_revoke, "ef"),
    "/destroy": (_handle_destroy, "ef"),
    "/invite": (_handle_invite_generate, "ef"),
    "/access/revoke": (_handle_revoke_viewer, "ef"),
    "/access/promote": (_handle_promote_viewer, "ef"),
    "/access/demote": (_handle_demote_admin, "ef"),
    "/cache/clear": (_handle_cache_clear, "ef"),
    "/trigger/scan": (_handle_trigger_scan, "e"),
    "/trigger/sprawl": (_handle_trigger_sprawl, "e"),
    "/trigger/enforce": (_handle_trigger_enforce, "ef"),
    "/abort-all": (_handle_abort_all, "ef"),
    "/extend-all": (_handle_extend_all, "ef"),
    "/approve-all": (_handle_approve_all, "ef"),
    "/destroy-all": (_handle_destroy_all, "ef"),
}


def _dispatch(route_entry, user_email, qs, form_data, context):
    """Call handler with the right signature based on its tag."""
    fn, sig = route_entry
    if sig == "e":
        return fn(user_email)
    elif sig == "eq":
        return fn(user_email, qs)
    elif sig == "ec":
        return fn(user_email, context)
    elif sig == "eqc":
        return fn(user_email, qs, context)
    elif sig == "ef":
        return fn(user_email, form_data)

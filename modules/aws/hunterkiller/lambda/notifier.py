"""
Notifier — HTML email templates for hunterkiller warnings and enforcement reports.

Sends styled HTML emails via SES for:
  1. 24-hour warning notices (per-contact, grouped by recipient)
  2. Enforcement reports (to admins, after teardown execution)
"""

import html
import json
import os
import random
import re
from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "")

# Optional LLM-written summary at the top of enforcement emails. Off unless a
# Bedrock model id is configured; the region defaults to the function's own.
BEDROCK_SUMMARY_MODEL_ID = os.environ.get("BEDROCK_SUMMARY_MODEL_ID", "")
BEDROCK_SUMMARY_REGION = os.environ.get("BEDROCK_SUMMARY_REGION", "")

# Tones for the optional LLM-written summary in enforcement emails. Each
# email picks one tone and an intensity level (5-10).
_PERSONAE = [
    {
        "name": "The night-shift engineer",
        "style": "world-weary, has seen this mess before and just wants it fixed",
    },
    {
        "name": "The philosopher",
        "style": "intense, finds a larger lesson in every cleanup",
    },
    {
        "name": "The laconic ranger",
        "style": "short, decisive sentences that state facts and stop",
    },
    {
        "name": "The machine",
        "style": "matter-of-fact report with clinical efficiency and no emotion",
    },
    {
        "name": "The underdog",
        "style": "treats each cleanup as a hard round won by persistence",
    },
    {
        "name": "The tournament fighter",
        "style": "calm and disciplined, treats the account like a contest of form",
    },
    {
        "name": "The cynic",
        "style": "dry and sardonic, expected this account to be in worse shape",
    },
    {
        "name": "The professional",
        "style": "methodical, precise, tracks every detail and leaves nothing behind",
    },
    {
        "name": "The bouncer",
        "style": "unhurried and polite while clearing out what does not belong",
    },
    {
        "name": "The contrarian",
        "style": "sees removal as making room for something better",
    },
    {
        "name": "The quiet one",
        "style": "says almost nothing, but every word counts",
    },
    {
        "name": "The veteran",
        "style": "warned this would happen and now calmly cleans it up",
    },
]


def _build_factual_summary(torn_down, resolved, protected, failed, savings_monthly, protected_cost, dry_run):
    """Build a plain-text factual summary of the enforcement results for the LLM."""
    parts = []
    mode = "DRY RUN — no resources were actually removed" if dry_run else "LIVE enforcement"
    parts.append(f"Mode: {mode}.")

    if torn_down:
        parts.append(f"{len(torn_down)} non-compliant resources were removed.")
    if resolved:
        parts.append(f"{len(resolved)} resources came into compliance during the grace period (self-resolved).")
    if protected:
        parts.append(f"{len(protected)} resources were protected/exempt from enforcement.")
    if failed:
        parts.append(f"{len(failed)} resources failed removal (errors encountered).")
    if not torn_down and not failed:
        parts.append("No resources needed removal.")
    if savings_monthly > 0:
        parts.append(f"Estimated monthly savings from removals: ${savings_monthly:.0f}.")
    if protected_cost > 0:
        parts.append(f"Protected resources represent ${protected_cost:.0f}/mo in spend.")

    total = len(torn_down) + len(resolved) + len(protected) + len(failed)
    parts.append(f"Total resources processed: {total}.")
    return " ".join(parts)


def _generate_persona_summary(factual_summary):
    """
    Call the configured Bedrock model to generate a 4-sentence voiced summary.
    Returns styled HTML string, or empty string when no model is configured or
    on failure (non-fatal).
    """
    if not BEDROCK_SUMMARY_MODEL_ID:
        return ""
    try:
        persona = random.choice(_PERSONAE)
        intensity = random.randint(5, 10)

        prompt = (
            f"Write as {persona['name']}: {persona['style']}. "
            f"Intensity level: {intensity}/10 (5=a subtle tone, 10=unmistakably in that tone "
            f"but still professional, never parody). "
            f"\n\nWrite exactly 4 sentences summarizing this infrastructure enforcement report "
            f"in that tone. Be factually accurate — include the real numbers. "
            f"Make it useful as a status summary but with personality. "
            f"No hashtags, no emojis, no quotation marks around the whole thing.\n\n"
            f"Report facts: {factual_summary}"
        )

        bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_SUMMARY_REGION or None)
        response = bedrock.invoke_model(
            modelId=BEDROCK_SUMMARY_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.9,
                }
            ),
        )

        result = json.loads(response["body"].read())
        text = result["content"][0]["text"].strip()

        attribution = f"\u2014 {persona['name']}"
        html = (
            f'<div style="background:#1a1a2e;color:#e0e0e0;padding:16px 20px;'
            f"border-radius:8px;margin:0 0 16px;font-size:14px;line-height:1.6;"
            f'border-left:4px solid #e94560;">'
            f"{_html_escape(text)}"
            f'<div style="text-align:right;font-size:12px;color:#a0a0a0;margin-top:8px;'
            f'font-style:italic;">{_html_escape(attribution)}</div>'
            f"</div>"
        )

        print(f"  Persona summary: {persona['name']} (intensity {intensity}/10)")
        return html

    except Exception as e:
        print(f"  Persona summary skipped (non-fatal): {e}")
        return ""


def send_warning_emails(by_recipient, sender_email, grace_period_hours, dry_run, project, environment):
    """
    Send 24-hour warning emails grouped by recipient.

    Args:
        by_recipient: dict of {email: [findings]}
        sender_email: SES verified sender
        grace_period_hours: hours until enforcement
        dry_run: whether hunterkiller is in dry-run mode
        project: project name
        environment: environment name

    Returns:
        int: number of emails sent
    """
    ses = boto3.client("ses")
    sent = 0
    deadline = datetime.now(UTC) + timedelta(hours=grace_period_hours)
    deadline_str = deadline.strftime("%Y-%m-%d %H:%M UTC")

    for recipient_key, findings in by_recipient.items():
        if not recipient_key:
            continue

        # recipient_key may be comma-separated (admin_fallback sends to all admins)
        recipients = _clean_recipients(recipient_key.split(","))
        if not recipients:
            print(f"  Skipping warning: no valid recipient address for {len(findings)} resources")
            continue

        subject = _warning_subject(findings, project, environment, dry_run)
        body_html = _warning_body(
            findings, ", ".join(recipients), deadline_str, grace_period_hours, dry_run, project, environment
        )

        try:
            msg = MIMEMultipart("mixed")
            msg["Subject"] = _header_text(subject)
            msg["From"] = _header_text(sender_email)
            msg["To"] = ", ".join(recipients)

            body_part = MIMEText(body_html, "html", "utf-8")
            msg.attach(body_part)

            ses.send_raw_email(
                Source=sender_email,
                Destinations=recipients,
                RawMessage={"Data": msg.as_string()},
            )
            sent += 1
            print(f"  Warning email sent to {', '.join(recipients)} ({len(findings)} resources)")

        except Exception as e:
            print(f"  Failed to send warning to {', '.join(recipients)}: {e}")

    return sent


def send_enforcement_report(
    results,
    resolved,
    protected,
    sender_email,
    admin_emails,
    dry_run,
    project,
    environment,
    batch_id,
    savings_monthly=0,
    protected_cost=0,
):
    """
    Send enforcement report email to cloud admins.
    """
    if not admin_emails:
        print("No admin emails configured — skipping enforcement report")
        return

    ses = boto3.client("ses")

    torn_down = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    mode_label = "DRY RUN" if dry_run else "LIVE"
    savings_label = f" — ${savings_monthly:.0f}/mo recovered" if savings_monthly > 0 else ""
    if dry_run:
        subject = (
            f"[{project}] Hunterkiller {mode_label}: "
            f"{len(torn_down)} would remove, {len(resolved)} resolved, "
            f"{len(protected)} protected, {len(failed)} errors{savings_label}"
        )
    elif torn_down:
        subject = f"[{project}] Hunterkiller: {len(torn_down)} resources handled{savings_label}"
    else:
        subject = f"[{project}] Hunterkiller: nothing to do — {len(resolved)} resolved, {len(protected)} protected"

    # Generate persona-voiced summary (best-effort, non-fatal)
    persona_html = _generate_persona_summary(
        _build_factual_summary(torn_down, resolved, protected, failed, savings_monthly, protected_cost, dry_run)
    )

    body_html = _enforcement_body(
        results,
        resolved,
        protected,
        dry_run,
        project,
        environment,
        batch_id,
        savings_monthly=savings_monthly,
        protected_cost=protected_cost,
        persona_html=persona_html,
    )

    for recipient in _clean_recipients(admin_emails):
        try:
            msg = MIMEMultipart("mixed")
            msg["Subject"] = _header_text(subject)
            msg["From"] = _header_text(sender_email)
            msg["To"] = recipient

            body_part = MIMEText(body_html, "html", "utf-8")
            msg.attach(body_part)

            ses.send_raw_email(
                Source=sender_email,
                Destinations=[recipient],
                RawMessage={"Data": msg.as_string()},
            )
            print(f"  Enforcement report sent to {recipient}")
        except Exception as e:
            print(f"  Failed to send enforcement report to {recipient}: {e}")


def _warning_subject(findings, project, environment, dry_run):
    total_cost = sum(float(f.get("estimated_monthly_cost") or 0) for f in findings)
    mode = "[DRY RUN] " if dry_run else ""
    return f"{mode}[{project}] 24-Hour Compliance Warning: {len(findings)} resources, ${total_cost:.0f}/mo burn"


def _warning_body(findings, recipient_email, deadline_str, grace_period_hours, dry_run, project, environment):
    project = _html_escape(project)
    environment = _html_escape(environment)
    deadline_str = _html_escape(deadline_str)
    dashboard_url = _safe_url(DASHBOARD_URL)
    h = []
    h.append("<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>")
    h.append("<div style='font-family:system-ui,-apple-system,sans-serif;max-width:800px;margin:0 auto;'>")

    # Header
    mode_banner = ""
    if dry_run:
        mode_banner = (
            "<div style='background:#f59e0b;color:#fff;padding:8px 16px;"
            "border-radius:6px;margin-bottom:16px;font-weight:600;'>"
            "DRY RUN MODE — No resources will be torn down. This is a test notification."
            "</div>"
        )

    h.append(f"""
    <div style='background:#dc2626;color:#fff;padding:16px 24px;border-radius:8px 8px 0 0;'>
        <h1 style='margin:0;font-size:20px;'>24-Hour Compliance Warning</h1>
        <p style='margin:4px 0 0;opacity:0.9;font-size:14px;'>{project} / {environment}</p>
    </div>
    {mode_banner}
    <div style='border:1px solid #e5e7eb;border-top:none;padding:20px 24px;border-radius:0 0 8px 8px;'>
    """)

    total_cost = sum(float(f.get("estimated_monthly_cost") or 0) for f in findings)

    h.append(
        f"<p>The following <strong>{len(findings)}</strong> resource(s) (${total_cost:.0f}/mo total burn) "
        f"are out of compliance and will be <strong>automatically removed</strong> after "
        f"<strong>{deadline_str}</strong> ({grace_period_hours} hours from now) "
        f"unless the compliance issues are resolved.</p>"
    )

    h.append(
        "<p><strong>To prevent removal:</strong> Add the required tags, fix the compliance "
        "issues, or add a <code>Protected=true</code> tag to exempt the resource.</p>"
    )

    # Dashboard escape hatch link
    if dashboard_url:
        h.append(f"""
        <div style='background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;padding:12px 16px;margin:12px 0;'>
            <strong style='color:#1e40af;'>Escape Hatch:</strong>
            <a href='{dashboard_url}/pending' style='color:#2563eb;text-decoration:underline;'>
                Open Compliance Dashboard</a> to abort, delay, or mark resources as false positives.
        </div>
        """)

    # Group findings by contact source for the summary
    by_contact = {}
    for f in findings:
        contact = f.get("contact_email", "")
        source = f.get("contact_source", "unknown")
        key = f"{contact} ({source})" if contact else f"No contact ({source})"
        if key not in by_contact:
            by_contact[key] = []
        by_contact[key].append(f)

    # Contact summary table
    h.append("<h2 style='font-size:16px;margin:20px 0 8px;'>Ownership Summary</h2>")
    h.append("""
    <table style='width:100%;border-collapse:collapse;margin:8px 0 20px;font-size:13px;'>
    <tr style='background:#f9fafb;'>
        <th style='text-align:left;padding:8px;border:1px solid #e5e7eb;'>Contact</th>
        <th style='text-align:left;padding:8px;border:1px solid #e5e7eb;'>Source</th>
        <th style='text-align:right;padding:8px;border:1px solid #e5e7eb;'>Resources</th>
        <th style='text-align:right;padding:8px;border:1px solid #e5e7eb;'>$/mo</th>
    </tr>
    """)

    for contact_key in sorted(by_contact.keys(), key=lambda k: -len(by_contact[k])):
        items = by_contact[contact_key]
        contact_cost = sum(float(f.get("estimated_monthly_cost") or 0) for f in items)
        # Parse out the email and source from the key
        contact_email_display = items[0].get("contact_email", "unknown")
        contact_src = items[0].get("contact_source", "unknown")

        # Color code: admin_fallback is orange, known contacts are blue
        color = "#d97706" if "admin" in contact_src or "no_contact" in contact_src else "#2563eb"

        h.append(f"""
        <tr>
            <td style='padding:8px;border:1px solid #e5e7eb;'>
                <span style='color:{color};font-weight:600;'>{_html_escape(contact_email_display or "Unknown")}</span>
            </td>
            <td style='padding:8px;border:1px solid #e5e7eb;font-size:12px;'>{_html_escape(contact_src)}</td>
            <td style='padding:8px;border:1px solid #e5e7eb;text-align:right;'>{len(items)}</td>
            <td style='padding:8px;border:1px solid #e5e7eb;text-align:right;'>${contact_cost:.2f}</td>
        </tr>
        """)

    h.append("</table>")

    # Detailed resource tables grouped by contact
    for contact_key in sorted(by_contact.keys(), key=lambda k: -len(by_contact[k])):
        items = by_contact[contact_key]
        contact_email_display = items[0].get("contact_email", "unknown")
        contact_src = items[0].get("contact_source", "unknown")
        group_cost = sum(float(f.get("estimated_monthly_cost") or 0) for f in items)

        h.append(
            f"<h3 style='font-size:14px;margin:16px 0 8px;'>"
            f"{_html_escape(contact_email_display or 'No Contact')} "
            f"<span style='color:#6b7280;font-weight:400;'>({_html_escape(contact_src)}) "
            f"&mdash; {len(items)} resources, ${group_cost:.2f}/mo</span></h3>"
        )

        h.append("""
        <table style='width:100%;border-collapse:collapse;margin:4px 0 12px;font-size:12px;'>
        <tr style='background:#f9fafb;'>
            <th style='text-align:left;padding:6px;border:1px solid #e5e7eb;'>Resource</th>
            <th style='text-align:left;padding:6px;border:1px solid #e5e7eb;'>Type</th>
            <th style='text-align:left;padding:6px;border:1px solid #e5e7eb;'>Region</th>
            <th style='text-align:right;padding:6px;border:1px solid #e5e7eb;'>$/mo</th>
            <th style='text-align:left;padding:6px;border:1px solid #e5e7eb;'>Issue</th>
        </tr>
        """)

        for f in sorted(items, key=lambda x: -(float(x.get("estimated_monthly_cost") or 0))):
            name = _html_escape(f.get("resource_name", "") or _extract_short_id(f.get("resource_arn", "")))
            rtype = _html_escape(f.get("resource_type", ""))
            region = _html_escape(f.get("region", ""))
            cost = float(f.get("estimated_monthly_cost") or 0)
            issue = _html_escape(f.get("issue", ""))

            h.append(f"""
            <tr>
                <td style='padding:6px;border:1px solid #e5e7eb;font-family:monospace;font-size:11px;'>{name}</td>
                <td style='padding:6px;border:1px solid #e5e7eb;'>{rtype}</td>
                <td style='padding:6px;border:1px solid #e5e7eb;'>{region}</td>
                <td style='padding:6px;border:1px solid #e5e7eb;text-align:right;'>${cost:.2f}</td>
                <td style='padding:6px;border:1px solid #e5e7eb;font-size:11px;'>{issue}</td>
            </tr>
            """)

        h.append("</table>")

    # Footer
    h.append(
        f"<p style='color:#6b7280;font-size:12px;margin-top:16px;'>"
        f"Sent to: {_html_escape(recipient_email)}<br>"
        f"Deadline: {deadline_str}</p>"
    )
    h.append("</div></div></body></html>")

    return "\n".join(h)


def _enforcement_body(
    results,
    resolved,
    protected,
    dry_run,
    project,
    environment,
    batch_id,
    savings_monthly=0,
    protected_cost=0,
    persona_html="",
):
    project = _html_escape(project)
    environment = _html_escape(environment)
    batch_id = _html_escape(batch_id)
    dashboard_url = _safe_url(DASHBOARD_URL)
    h = []
    h.append("<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>")
    h.append("<div style='font-family:system-ui,-apple-system,sans-serif;max-width:800px;margin:0 auto;'>")

    mode_label = "DRY RUN" if dry_run else "LIVE"
    color = "#f59e0b" if dry_run else "#16a34a"

    torn_down = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    if dry_run:
        headline = "Hunterkiller Enforcement Report (DRY RUN)"
    elif torn_down and not failed:
        headline = "Hunterkiller — Sweep Complete"
    elif torn_down and failed:
        headline = "Hunterkiller — Sweep Complete (with complications)"
    elif not torn_down and resolved:
        headline = "Hunterkiller — All Clear"
    else:
        headline = "Hunterkiller Enforcement Report"

    h.append(f"""
    <div style='background:{color};color:#fff;padding:16px 24px;border-radius:8px 8px 0 0;'>
        <h1 style='margin:0;font-size:20px;'>{headline}</h1>
        <p style='margin:4px 0 0;opacity:0.9;font-size:14px;'>{project} / {environment} | Batch: {batch_id}</p>
    </div>
    <div style='border:1px solid #e5e7eb;border-top:none;padding:20px 24px;border-radius:0 0 8px 8px;'>
    """)

    # Persona-voiced summary (LLM-generated, inserted at top of body)
    if persona_html:
        h.append(persona_html)

    # Cost impact cards
    if savings_monthly > 0 or protected_cost > 0:
        savings_label = "Est. Savings" if dry_run else "Savings"
        h.append("<div style='display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;'>")
        if savings_monthly > 0:
            h.append(
                f"<div style='background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;"
                f"padding:12px 16px;flex:1;min-width:140px;'>"
                f"<div style='font-size:12px;color:#166534;'>{savings_label}</div>"
                f"<div style='font-size:22px;font-weight:700;color:#16a34a;'>"
                f"${savings_monthly:.0f}<span style='font-size:13px;font-weight:400;'>/mo</span></div></div>"
            )
        if protected_cost > 0:
            h.append(
                f"<div style='background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;"
                f"padding:12px 16px;flex:1;min-width:140px;'>"
                f"<div style='font-size:12px;color:#1e40af;'>Protected Cost</div>"
                f"<div style='font-size:22px;font-weight:700;color:#3b82f6;'>"
                f"${protected_cost:.0f}<span style='font-size:13px;font-weight:400;'>/mo</span></div></div>"
            )
        h.append("</div>")

    # Summary badges
    h.append("<div style='display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;'>")
    if torn_down:
        label = "Would Remove" if dry_run else "Removed"
        h.append(
            f"<span style='background:#dc2626;color:#fff;padding:4px 12px;"
            f"border-radius:12px;font-size:13px;'>{len(torn_down)} {label}</span>"
        )
    if resolved:
        h.append(
            f"<span style='background:#16a34a;color:#fff;padding:4px 12px;"
            f"border-radius:12px;font-size:13px;'>{len(resolved)} Resolved</span>"
        )
    if protected:
        h.append(
            f"<span style='background:#3b82f6;color:#fff;padding:4px 12px;"
            f"border-radius:12px;font-size:13px;'>{len(protected)} Protected</span>"
        )
    if failed:
        h.append(
            f"<span style='background:#ef4444;color:#fff;padding:4px 12px;"
            f"border-radius:12px;font-size:13px;'>{len(failed)} Errors</span>"
        )
    h.append("</div>")

    # Torn down / would remove
    if torn_down:
        label = "Would Remove (Dry Run)" if dry_run else "Cleared Out"
        h.append(f"<h2 style='margin:16px 0 8px;font-size:16px;'>{label}</h2>")
        h.append(_resource_table(torn_down))

    # Resolved
    if resolved:
        h.append("<h2 style='margin:16px 0 8px;font-size:16px;color:#16a34a;'>Came Into Compliance</h2>")
        h.append(
            "<p style='color:#6b7280;font-size:13px;'>These resources sorted themselves out "
            "during the grace period. Good.</p>"
        )
        h.append(_resource_table(resolved))

    # Protected
    if protected:
        h.append("<h2 style='margin:16px 0 8px;font-size:16px;color:#3b82f6;'>Protected</h2>")
        h.append("<p style='color:#6b7280;font-size:13px;'>Off limits. Production or explicitly protected.</p>")
        h.append(_resource_table(protected))

    # Errors
    if failed:
        h.append("<h2 style='margin:16px 0 8px;font-size:16px;color:#ef4444;'>Errors</h2>")
        h.append("<p style='color:#6b7280;font-size:13px;'>These put up a fight.</p>")
        for r in failed:
            arn = _html_escape(r.get("resource_arn", ""))
            msg = _html_escape(r.get("message", ""))
            h.append(
                f"<div style='background:#fef2f2;padding:8px 12px;margin:4px 0;"
                f"border-radius:4px;font-size:12px;font-family:monospace;'>"
                f"<strong>{arn}</strong><br>{msg}</div>"
            )

    # Post-teardown rescan note
    h.append(
        "<div style='background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;"
        "padding:12px 16px;margin:16px 0;'>"
        "A fresh compliance scan is running. The dashboard will reflect "
        "the new state shortly.</div>"
    )

    dashboard_link = ""
    if dashboard_url:
        dashboard_link = f' | <a href="{dashboard_url}" style="color:#2563eb;">Dashboard</a>'

    h.append(
        f"<p style='color:#6b7280;font-size:12px;margin-top:16px;'>"
        f"Batch: {batch_id} | Mode: {mode_label}{dashboard_link}</p>"
    )
    h.append("</div></div></body></html>")
    return "\n".join(h)


def _resource_table(items):
    """Render a simple resource table for email."""
    h = []
    h.append("""
    <table style='width:100%;border-collapse:collapse;font-size:13px;'>
    <tr style='background:#f9fafb;'>
        <th style='text-align:left;padding:6px 8px;border:1px solid #e5e7eb;'>Resource</th>
        <th style='text-align:left;padding:6px 8px;border:1px solid #e5e7eb;'>Type</th>
        <th style='text-align:left;padding:6px 8px;border:1px solid #e5e7eb;'>Region</th>
        <th style='text-align:left;padding:6px 8px;border:1px solid #e5e7eb;'>Status</th>
    </tr>
    """)

    for item in items:
        name = _html_escape(item.get("resource_name", "") or _extract_short_id(item.get("resource_arn", "")))
        rtype = _html_escape(item.get("resource_type", ""))
        region = _html_escape(item.get("region", ""))
        msg = _html_escape(item.get("message", item.get("status", "")))

        h.append(f"""
        <tr>
            <td style='padding:6px 8px;border:1px solid #e5e7eb;font-family:monospace;font-size:11px;'>{name}</td>
            <td style='padding:6px 8px;border:1px solid #e5e7eb;'>{rtype}</td>
            <td style='padding:6px 8px;border:1px solid #e5e7eb;'>{region}</td>
            <td style='padding:6px 8px;border:1px solid #e5e7eb;font-size:11px;'>{msg}</td>
        </tr>
        """)

    h.append("</table>")
    return "\n".join(h)


def _html_escape(text):
    """HTML escaping using stdlib for complete character coverage."""
    if not text:
        return ""
    return html.escape(str(text), quote=True)


def _safe_url(url):
    """Return an http(s) URL escaped for an href attribute; anything else becomes an empty string."""
    url = str(url or "").strip()
    if not re.match(r"https?://", url, re.IGNORECASE):
        return ""
    return html.escape(url, quote=True)


def _header_text(value):
    """Collapse CR, LF and other control characters so a value cannot add a mail header."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()


_ADDRESS = re.compile(r"[^\s@,;:<>()\[\]\"\\\x00-\x1f\x7f]+@[^\s@,;:<>()\[\]\"\\\x00-\x1f\x7f]+")


def _clean_recipients(addresses):
    """Keep only values that are one plain address; a value with CR/LF, a list or a display name is dropped."""
    clean = []
    for address in addresses:
        candidate = str(address or "").strip()
        if _ADDRESS.fullmatch(candidate):
            clean.append(candidate)
        elif candidate:
            print(f"  Dropped invalid recipient address: {candidate!r}")
    return clean


def _extract_short_id(arn):
    """Extract a short identifier from an ARN for display."""
    if not arn:
        return ""
    parts = arn.split("/")
    if len(parts) > 1:
        return parts[-1]
    parts = arn.split(":")
    if len(parts) > 5:
        return parts[-1]
    return arn[-40:]  # Last 40 chars as fallback

"""
CURE page — detailed remediation view for a non-compliant resource.

Shows Terraform location, findings, volume tracing, and actionable CLI commands.
"""

import html
import os

from compliance_schema import repo_slug
from data_core import PROJECT, REGION, REPOSITORY_URL
from views_base import (
    _SEV_BADGE,
    _STATE_BADGE,
    ENVIRONMENT,
    SEV_COLORS,
    TYPE_ICONS,
    _badge,
    _console_url,
    _e,
    _get_contact,
    _get_missing_required_tags,
    _layout,
    _safe_url,
)


def render_cure(findings, tf_info, resource_arn, user_email="", volume_parent=None, import_hint=None, volume_live=None):
    """Render the CURE page — detailed remediation plan for a non-compliant resource."""
    if not findings:
        content = _render_stale_cure(resource_arn)
        return _layout("Cure", content, user_email)

    f = findings[0]
    name = f.get("resource_name", "") or resource_arn.split(":")[-1][:40]
    rtype = f.get("resource_type", "")
    icon = TYPE_ICONS.get(rtype, "")
    region = f.get("region") or REGION
    tags = f.get("tags", {})
    managed = tags.get("ManagedBy", "").lower() == "terraform"
    contact = _get_contact(f) or "untagged"
    cost = f.get("estimated_monthly_cost")
    cost_str = f"${float(cost):.2f}/mo" if cost else "N/A"
    service = tags.get("Service", "")
    purpose = tags.get("Purpose", "")
    repo_url = tags.get("Repository", "")

    console = _console_url(resource_arn, region)
    console_link = (
        f'<a href="{_e(console)}" target="_blank" rel="noopener" class="btn btn-secondary" style="font-size:11px;">Open in AWS Console</a>'
        if console
        else ""
    )

    # Service/Purpose/Repository context line
    context_line = ""
    if service or purpose or repo_url:
        parts = []
        if service:
            parts.append(_badge(f"Service: {_e(service)}", "blue"))
        if repo_url:
            if repo_url.startswith("https://"):
                repo_name = repo_url.split("/")[-1] if "/" in repo_url else repo_url
                parts.append(
                    f'<a href="{_e(repo_url)}" target="_blank" rel="noopener" class="badge badge-green" style="text-decoration:none;">Repo: {_e(repo_name)}</a>'
                )
            else:
                parts.append(_badge(f"Repo: {_e(repo_url)}", "green"))
        if purpose:
            parts.append(f'<span style="color:#94a3b8; font-size:12px;">{_e(purpose)}</span>')
        context_line = f'<div style="display:flex; gap:8px; margin-bottom:12px; align-items:center; flex-wrap:wrap;">{" ".join(parts)}</div>'

    # Resource header card
    content = f"""
    <div style="margin-bottom:16px;">
      <a href="/findings" class="btn btn-secondary" style="font-size:11px;">&larr; Back to Findings</a>
    </div>
    <h1>{icon} {_e(name)}</h1>
    {context_line}
    <div style="display:flex; gap:8px; margin:8px 0 16px; flex-wrap:wrap; align-items:center;">
      {_badge(_e(rtype), "blue")}
      {_badge("TF-MANAGED", "blue") if managed else _badge("CLICK-OPS", "red")}
      <span style="color:#4b5563; font-size:12px;">{_e(region)}</span>
      <span class="cost cost-bad">{cost_str}</span>
      <span style="color:#6b7280; font-size:12px;">Contact: {_e(contact)}</span>
      {console_link}
    </div>
    <div class="card" style="margin-bottom:16px; padding:8px 12px;">
      <div class="label">ARN</div>
      <div class="mono" style="word-break:break-all; font-size:11px;">{_e(resource_arn)}</div>
    </div>"""

    # Volume context — live EC2 info + TF state tracing
    if volume_live or volume_parent:
        content += _render_volume_context(volume_live, volume_parent, resource_arn)

    # Current tags
    if tags:
        tag_rows = "".join(
            f'<tr><td style="color:#3b82f6;">{_e(k)}</td><td>{_e(v)}</td></tr>' for k, v in sorted(tags.items())
        )
        content += f"""
        <h2>CURRENT TAGS</h2>
        <table style="max-width:600px;">
          <thead><tr><th>Key</th><th>Value</th></tr></thead>
          <tbody>{tag_rows}</tbody>
        </table>"""

    # Findings for this resource
    content += f"<h2>FINDINGS ({len(findings)})</h2>"
    for finding in findings:
        sev = finding.get("severity", "")
        sev_color = SEV_COLORS.get(sev, "#6b7280")
        category = finding.get("category", "")
        issue = finding.get("issue", "")
        recommendation = finding.get("recommendation", "")

        content += f"""
        <div class="card" style="margin-bottom:8px; border-left:3px solid {sev_color};">
          <div style="display:flex; gap:8px; align-items:center; margin-bottom:4px;">
            {_badge(_e(sev), _SEV_BADGE.get(sev, "gray"))}
            <span style="color:#94a3b8; font-size:12px;">{_e(category.replace("_", " ").title())}</span>
          </div>
          <div style="color:#e2e8f0; font-size:13px;">{_e(issue)}</div>
          {'<div style="color:#6b7280; font-size:12px; margin-top:4px; font-style:italic;">' + _e(recommendation) + "</div>" if recommendation else ""}
        </div>"""

    # TF info and remediation
    content += "<h2>REMEDIATION</h2>"

    if tf_info.get("found"):
        tf_info.get("module_path", "")
        tf_address = tf_info.get("tf_address", "")
        file_path = tf_info.get("file_path", "")
        repo_url = _safe_url(tf_info.get("repo_url", ""))
        top_module = tf_info.get("top_module", "")

        content += f"""
        <div class="card" style="border-left:3px solid #3b82f6; margin-bottom:16px;">
          <div class="label">TERRAFORM LOCATION</div>
          <div style="margin:8px 0;">
            <div style="color:#e2e8f0;"><strong>Address:</strong> <code style="color:#3b82f6;">{_e(tf_address)}</code></div>
            <div style="color:#e2e8f0;"><strong>Module:</strong> <code style="color:#3b82f6;">{_e(top_module)}</code></div>
            {'<div style="color:#e2e8f0;"><strong>File:</strong> <a href="' + _e(repo_url) + '" target="_blank" rel="noopener" style="color:#3b82f6;">' + _e(file_path) + "</a></div>" if file_path else ""}
          </div>
        </div>"""

        # Generate fix code snippets based on issues
        missing_tags = _get_missing_required_tags(tags)
        schedule_issue = any(
            "schedule" in f.get("category", "").lower() or "schedule" in f.get("issue", "").lower() for f in findings
        )

        if missing_tags or schedule_issue:
            fix_tags = {}
            for tag in missing_tags:
                if tag == "Project":
                    fix_tags["Project"] = "var.project"
                elif tag == "Environment":
                    fix_tags["Environment"] = "var.environment"
                elif tag == "ManagedBy":
                    fix_tags["ManagedBy"] = '"terraform"'
                elif tag == "Lifecycle":
                    fix_tags["Lifecycle"] = '"persistent"  # or "temporary" with ExpiresAt'
                elif tag == "Repository":
                    fix_tags["Repository"] = '"https://github.com/YOUR-ORG/YOUR-REPO"'

            if schedule_issue:
                fix_tags["Schedule"] = '"on-demand"  # or "business-hours", "always-on"'

            tag_lines = "\n".join(f"    {k:14s} = {v}" for k, v in fix_tags.items())

            content += f"""
            <div class="card" style="border-left:3px solid #22c55e; margin-bottom:16px;">
              <div class="label" style="color:#22c55e;">RECOMMENDED FIX</div>
              <p style="color:#94a3b8; font-size:13px; margin:8px 0;">
                Add the following tags to the module call in
                <code style="color:#3b82f6;">{_e(file_path or "the Terraform config")}</code>:
              </p>
              <pre style="background:#0d0d14; border:1px solid #1e1e2e; border-radius:4px;
                          padding:12px; font-size:12px; color:#e2e8f0; overflow-x:auto;
                          white-space:pre; line-height:1.5;">
<span style="color:#6b7280;">  # In the module "{_e(top_module.replace("module.", ""))}" block, add to tags:</span>
  tags = {{
{tag_lines}
  }}</pre>
              <p style="color:#94a3b8; font-size:12px; margin-top:8px;">
                Then run: <code style="color:#f59e0b;">terraform plan</code> to verify,
                <code style="color:#22c55e;">terraform apply</code> to fix.
              </p>
            </div>"""

        # If resource has drift issues
        drift_findings = [f for f in findings if "drift" in f.get("category", "").lower()]
        if drift_findings:
            content += f"""
            <div class="card" style="border-left:3px solid #f59e0b; margin-bottom:16px;">
              <div class="label" style="color:#f59e0b;">DRIFT DETECTED</div>
              <p style="color:#94a3b8; font-size:13px; margin:8px 0;">
                This resource exists in AWS but may not match the Terraform state.
                Run drift check:
              </p>
              <pre style="background:#0d0d14; border:1px solid #1e1e2e; border-radius:4px;
                          padding:12px; font-size:12px; color:#e2e8f0; overflow-x:auto;
                          white-space:pre; line-height:1.5;">
<span style="color:#6b7280;"># Check current state</span>
terraform state show '{_e(tf_address)}'

<span style="color:#6b7280;"># Refresh and plan</span>
terraform plan -target='{_e(tf_address)}'</pre>
            </div>"""

        # Destroy option for TF-managed resources
        content += f"""
        <div class="card" style="border-left:3px solid #ef4444; margin-bottom:16px;">
          <div class="label" style="color:#ef4444;">IF NO LONGER NEEDED &mdash; DESTROY VIA TERRAFORM</div>
          <pre style="background:#0d0d14; border:1px solid #1e1e2e; border-radius:4px;
                      padding:12px; font-size:12px; color:#e2e8f0; overflow-x:auto;
                      white-space:pre; line-height:1.5;">
<span style="color:#6b7280;"># Preview what will be destroyed</span>
terraform plan -destroy -target='{_e(tf_address)}'

<span style="color:#6b7280;"># If the plan looks correct, destroy</span>
terraform apply -destroy -target='{_e(tf_address)}'</pre>
          <p style="color:#ef4444; font-size:11px; margin-top:6px;">
            &#x26A0; Destructive. Verify the resource is unused before destroying.
          </p>
        </div>"""

    elif not managed:
        # Suspected click-ops resource — generate actionable CLI commands
        content += f"""
        <div class="card" style="border-left:3px solid #ef4444; margin-bottom:16px;">
          <div class="label" style="color:#ef4444;">SUSPECTED CLICK-OPS</div>
          <p style="color:#94a3b8; font-size:13px; margin:8px 0;">
            This resource is not found in the active Terraform state (<code style="color:#f59e0b;">{_e(repo_slug(REPOSITORY_URL))}</code>).
            It may have been created manually, via the AWS console/CLI, or by another Terraform repo or automation tool.
          </p>
        </div>"""

        # Option A: Destroy (primary — encouraged)
        destroy_cmd = _generate_destroy_command(resource_arn, rtype, region)
        if destroy_cmd:
            savings_note = f' Saves <strong style="color:#22c55e;">{cost_str}</strong>.' if cost else ""
            content += f"""
            <div class="card" style="border-left:3px solid #ef4444; margin-bottom:16px;">
              <div class="label" style="color:#ef4444;">OPTION A &mdash; DELETE THIS RESOURCE</div>
              <p style="color:#94a3b8; font-size:13px; margin:8px 0;">
                If this resource is not needed, the cleanest fix is to delete it.{savings_note}
              </p>
              <pre style="background:#0d0d14; border:1px solid #1e1e2e; border-radius:4px;
                          padding:12px; font-size:11px; color:#e2e8f0; overflow-x:auto;
                          white-space:pre; line-height:1.5; margin-top:8px;">{_e(destroy_cmd)}</pre>
              <p style="color:#ef4444; font-size:11px; margin-top:6px;">
                &#x26A0; This is destructive and cannot be undone. Verify the resource is unused first.
              </p>
            </div>"""

        # Option B: Import into Terraform (secondary — if worth keeping)
        if import_hint:
            content += f"""
            <div class="card" style="border-left:3px solid #3b82f6; margin-bottom:16px;">
              <div class="label" style="color:#3b82f6;">OPTION B &mdash; ADOPT INTO TERRAFORM</div>
              <p style="color:#94a3b8; font-size:13px; margin:8px 0;">
                If this resource is needed long-term, import it into Terraform so it&rsquo;s tracked and tagged properly.
              </p>
              <pre style="background:#0d0d14; border:1px solid #1e1e2e; border-radius:4px;
                          padding:12px; font-size:11px; color:#e2e8f0; overflow-x:auto;
                          white-space:pre; line-height:1.5; margin-top:8px;">
<span style="color:#6b7280;"># Import this resource into Terraform state</span>
{_e(import_hint.get("command", ""))}</pre>
              <p style="color:#94a3b8; font-size:12px; margin-top:4px;">
                Replace <code>RESOURCE_NAME</code> with your chosen Terraform resource name.
              </p>
            </div>"""

        # Option C: Tag manually (last resort)
        tag_cmd = _generate_tag_command(resource_arn, region)
        if tag_cmd:
            content += f"""
            <div class="card" style="border-left:3px solid #4b5563; margin-bottom:16px;">
              <div class="label" style="color:#4b5563;">OPTION C &mdash; TAG IN PLACE (band-aid)</div>
              <p style="color:#6b7280; font-size:12px; margin:8px 0;">
                If you can&rsquo;t delete or import right now, at least tag the resource so it&rsquo;s tracked.
                This silences the finding but does not address the root cause.
              </p>
              <pre style="background:#0d0d14; border:1px solid #1e1e2e; border-radius:4px;
                          padding:12px; font-size:11px; color:#e2e8f0; overflow-x:auto;
                          white-space:pre; line-height:1.5; margin-top:8px;">{_e(tag_cmd)}</pre>
            </div>"""
    else:
        # TF-managed but state lookup failed — stale tag or another repo's TF
        content += f"""
        <div class="card" style="border-left:3px solid #f59e0b; margin-bottom:16px;">
          <div class="label" style="color:#f59e0b;">TERRAFORM STATE LOOKUP</div>
          <p style="color:#94a3b8; font-size:13px; margin:8px 0;">
            Resource has <code>ManagedBy=terraform</code> tag but could not be located in the
            <code>{_e(repo_slug(REPOSITORY_URL))}</code> Terraform state. It may be managed by another TF repo,
            or the tag may be stale.
            {_e(tf_info.get("reason", ""))}
          </p>
        </div>"""

        # Still offer destroy as primary option — if not in our TF, clean it up
        destroy_cmd = _generate_destroy_command(resource_arn, rtype, region)
        if destroy_cmd:
            savings_note = f' Saves <strong style="color:#22c55e;">{cost_str}</strong>.' if cost else ""
            content += f"""
            <div class="card" style="border-left:3px solid #ef4444; margin-bottom:16px;">
              <div class="label" style="color:#ef4444;">IF NOT NEEDED &mdash; DELETE THIS RESOURCE</div>
              <p style="color:#94a3b8; font-size:13px; margin:8px 0;">
                If this resource is not managed by another Terraform repo and is no longer needed,
                delete it.{savings_note}
              </p>
              <pre style="background:#0d0d14; border:1px solid #1e1e2e; border-radius:4px;
                          padding:12px; font-size:11px; color:#e2e8f0; overflow-x:auto;
                          white-space:pre; line-height:1.5; margin-top:8px;">{_e(destroy_cmd)}</pre>
              <p style="color:#ef4444; font-size:11px; margin-top:6px;">
                &#x26A0; Destructive. Verify the resource is unused and not managed by another TF repo first.
              </p>
            </div>"""

        # Also offer tagging fix since ManagedBy tag is stale
        tag_cmd = _generate_tag_command(resource_arn, region)
        if tag_cmd:
            content += f"""
            <div class="card" style="border-left:3px solid #4b5563; margin-bottom:16px;">
              <div class="label" style="color:#4b5563;">ALTERNATIVE &mdash; UPDATE TAGS (band-aid)</div>
              <p style="color:#6b7280; font-size:12px; margin:8px 0;">
                If the resource is needed but truly not in any Terraform state, update the
                <code>ManagedBy</code> tag to <code>manual</code> and add missing tags.
              </p>
              <pre style="background:#0d0d14; border:1px solid #1e1e2e; border-radius:4px;
                          padding:12px; font-size:11px; color:#e2e8f0; overflow-x:auto;
                          white-space:pre; line-height:1.5; margin-top:8px;">{_e(tag_cmd)}</pre>
            </div>"""

    # Actions
    arn_encoded = html.escape(resource_arn, quote=True)
    content += f"""
    <h2>ACTIONS</h2>
    <div style="display:flex; gap:8px; flex-wrap:wrap;">
      <form method="post" action="/override" style="display:inline;">
        <input type="hidden" name="resource_arn" value="{arn_encoded}">
        <input type="hidden" name="resource_type" value="{_e(rtype)}">
        <input type="hidden" name="resource_name" value="{_e(name)}">
        <input type="hidden" name="override_type" value="false_positive">
        <input type="hidden" name="reason" value="">
        <button type="submit" class="btn btn-primary"
          onclick="var r=prompt('Reason for marking as false positive:'); if(!r)return false; this.form.reason.value=r;">
          Mark as False Positive</button>
      </form>
      <form method="post" action="/override" style="display:inline;">
        <input type="hidden" name="resource_arn" value="{arn_encoded}">
        <input type="hidden" name="resource_type" value="{_e(rtype)}">
        <input type="hidden" name="resource_name" value="{_e(name)}">
        <input type="hidden" name="override_type" value="exempt">
        <input type="hidden" name="reason" value="">
        <button type="submit" class="btn btn-warn"
          onclick="var r=prompt('Reason for exemption:'); if(!r)return false; this.form.reason.value=r;">
          Exempt</button>
      </form>
    </div>"""

    return _layout("Cure", content, user_email)


_TRANSIENT_TYPES = {
    "ssm:session": "SSM sessions are ephemeral &mdash; they exist only while active and disappear once terminated.",
    "ecs:task": "ECS tasks are transient &mdash; they exist only while running and are replaced on each deployment.",
    "lambda:invocation": "Lambda invocations are one-shot events and do not persist.",
}


def _parse_arn_parts(arn):
    """Extract human-readable info from an ARN string."""
    # arn:aws:service:region:account:resource-type/resource-id
    parts = arn.split(":")
    if len(parts) < 6:
        return {}, arn
    service = parts[2]
    resource = ":".join(parts[5:])

    # Derive a resource_type key like "ssm:session" or "ec2:instance"
    rtype_key = ""
    resource_id = resource
    if "/" in resource:
        prefix, _, resource_id = resource.partition("/")
        rtype_key = f"{service}:{prefix}"
    elif ":" in resource:
        prefix, _, resource_id = resource.partition(":")
        rtype_key = f"{service}:{prefix}"
    else:
        rtype_key = service

    return {
        "service": service,
        "region": parts[3] or "us-east-1",
        "account": parts[4],
        "resource_type": rtype_key,
        "resource_id": resource_id,
    }, arn


def _render_stale_cure(resource_arn):
    """Render an informative page when a CURE link points to a resource no longer in the scan."""
    info, _ = _parse_arn_parts(resource_arn)
    rtype = info.get("resource_type", "")
    region = info.get("region") or REGION
    info.get("resource_id", resource_arn.split(":")[-1])
    service = info.get("service", "")

    # Friendly type name
    type_label = _e(rtype.replace("_", " ").title()).replace(":", " &rsaquo; ") if rtype else "Unknown"
    icon = TYPE_ICONS.get(rtype, TYPE_ICONS.get(service, ""))

    # Check if this is a known transient type
    transient_note = _TRANSIENT_TYPES.get(rtype, "")

    console = _console_url(resource_arn, region)
    console_link = (
        (
            f'<a href="{_e(console)}" target="_blank" rel="noopener" '
            f'class="btn btn-secondary" style="font-size:11px;">Open in AWS Console</a>'
        )
        if console
        else ""
    )

    content = f"""
    <div style="margin-bottom:16px;">
      <a href="/findings" class="btn btn-secondary" style="font-size:11px;">&larr; Back to Findings</a>
    </div>
    <h1>{icon} Resource No Longer In Scan</h1>
    <div class="card" style="border-left:3px solid #f59e0b; margin-bottom:16px;">
      <div class="label" style="color:#f59e0b;">STALE CURE LINK</div>
      <p style="color:#e2e8f0; font-size:13px; margin:8px 0;">
        This resource was flagged in a previous scan but is <strong>no longer present</strong>
        in the current compliance report. This typically means:
      </p>
      <ul style="color:#94a3b8; font-size:13px; margin:8px 0 8px 20px; line-height:1.8;">
        <li>The resource was <strong style="color:#22c55e;">deleted or cleaned up</strong> since the last scan</li>
        <li>The resource is <strong style="color:#3b82f6;">transient</strong> (e.g., SSM sessions, ECS tasks) and has ended</li>
        <li>The compliance findings were <strong style="color:#22c55e;">resolved</strong> (tags fixed, drift corrected)</li>
      </ul>
      {f'<p style="color:#f59e0b; font-size:12px; margin-top:8px;">{transient_note}</p>' if transient_note else ""}
    </div>
    <div class="card" style="margin-bottom:16px; padding:8px 12px;">
      <div class="label">ARN</div>
      <div class="mono" style="word-break:break-all; font-size:11px;">{_e(resource_arn)}</div>
    </div>
    <div style="display:flex; gap:8px; margin:8px 0 16px; flex-wrap:wrap; align-items:center;">
      {_badge(type_label, "blue")}
      <span style="color:#4b5563; font-size:12px;">{_e(region)}</span>
      {console_link}
    </div>
    <p style="color:#6b7280; font-size:12px;">
      If you expected this resource to still have findings, try
      <a href="/findings" style="color:#3b82f6;">refreshing the findings list</a>.
      The scanner runs nightly; findings reflect the most recent scan.
    </p>"""
    return content


def _render_volume_context(volume_live, volume_parent, resource_arn):
    """Render the volume context card showing parent instance info."""
    content = ""

    # Determine instance info from live data (preferred) or TF state fallback
    inst = (volume_live or {}).get("instance")
    vol_state = (volume_live or {}).get("volume_state", "")
    vol_size = (volume_live or {}).get("volume_size_gb", 0)
    vol_type = (volume_live or {}).get("volume_type", "")
    attached = (volume_live or {}).get("attached", False)
    dev_name = (volume_live or {}).get("device_name", "")

    # TF state info
    tf_addr = (volume_parent or {}).get("tf_address", "")
    parent_file = (volume_parent or {}).get("file_path", "")
    is_root = (volume_parent or {}).get("is_root_volume", False)
    if not dev_name:
        dev_name = (volume_parent or {}).get("device_name", "")

    # Volume metadata line
    vol_meta = []
    if vol_size:
        vol_meta.append(f"{_e(vol_size)} GB")
    if vol_type:
        vol_meta.append(_e(vol_type))
    if vol_state:
        vol_meta.append(_e(vol_state))
    vol_meta_str = " &middot; ".join(vol_meta)

    if attached and inst:
        # Instance is known — show rich context
        iid = inst.get("instance_id", "")
        inst_name = inst.get("name", "")
        inst_state = inst.get("state", "")
        inst_type = inst.get("instance_type", "")
        inst_tags = inst.get("tags", {})

        # Badges
        root_badge = f" {_badge('ROOT VOLUME', 'purple')}" if is_root else ""
        state_badge = f" {_badge(_e(inst_state.upper()), _STATE_BADGE.get(inst_state, 'gray'))}" if inst_state else ""

        # Instance display name
        display_name = _e(inst_name) if inst_name else _e(iid)
        if inst_name:
            display_name = f'{_e(inst_name)} <span style="color:#6b7280;">({_e(iid)})</span>'

        content += f"""
        <div class="card" style="border-left:3px solid #a78bfa; margin-bottom:16px;">
          <div class="label" style="color:#a78bfa;">ATTACHED TO EC2 INSTANCE</div>
          <div style="margin:8px 0; font-size:13px;">
            <div style="color:#e2e8f0; margin-bottom:4px;">
              <strong>{display_name}</strong>{state_badge}{root_badge}
            </div>"""

        if inst_type:
            content += f'<div style="color:#94a3b8; font-size:12px;">{_e(inst_type)}</div>'

        if dev_name:
            content += f'<div style="color:#94a3b8; font-size:12px;">Device: {_e(dev_name)}</div>'

        if vol_meta_str:
            content += f'<div style="color:#94a3b8; font-size:12px;">Volume: {vol_meta_str}</div>'

        # Show useful instance tags (Service, Purpose, etc.)
        context_tags = {
            k: v
            for k, v in inst_tags.items()
            if k in ("Service", "Purpose", "Project", "Environment", "Lifecycle", "Contact", "Owner")
        }
        if context_tags:
            tag_badges = " ".join(
                _badge(f"{_e(k)}: {_e(v)}", "blue", "badge-sm") for k, v in sorted(context_tags.items())
            )
            content += f'<div style="margin-top:6px;">{tag_badges}</div>'

        # TF location if known
        if tf_addr:
            content += f"""
            <div style="margin-top:8px; padding-top:8px; border-top:1px solid #1e1e2e;">
              <div style="color:#94a3b8; font-size:11px;">TF Address: <code style="color:#3b82f6;">{_e(tf_addr)}</code></div>
              {'<div style="color:#94a3b8; font-size:11px;">File: <code style="color:#3b82f6;">' + _e(parent_file) + "</code></div>" if parent_file else ""}
            </div>"""

        content += """
          </div>
          <p style="color:#94a3b8; font-size:12px;">"""

        if inst_state == "terminated":
            content += 'The parent instance has been <strong style="color:#ef4444;">terminated</strong>. This volume is orphaned and likely safe to delete.'
        elif is_root:
            content += "This is a root volume managed by the parent instance. Fix tags on the instance, not the volume."
        else:
            content += (
                "This EBS volume is attached to the instance above. Tags should be managed on the parent resource."
            )

        content += """
          </p>
        </div>"""

    elif not attached and volume_live:
        # Volume exists but is not attached — orphaned
        content += f"""
        <div class="card" style="border-left:3px solid #ef4444; margin-bottom:16px;">
          <div class="label" style="color:#ef4444;">UNATTACHED VOLUME</div>
          <div style="margin:8px 0; font-size:13px; color:#94a3b8;">
            {"Volume: " + vol_meta_str + "<br>" if vol_meta_str else ""}
            This volume is not attached to any instance. It may be orphaned or retained from a terminated instance.
          </div>
          <p style="color:#ef4444; font-size:12px; font-weight:600;">
            Consider deleting this volume to stop incurring storage charges.
          </p>
        </div>"""

    elif volume_parent:
        # Only TF state info (no live data) — fallback
        iid = volume_parent.get("instance_id", "")
        root_badge = f" {_badge('ROOT VOLUME', 'purple')}" if is_root else ""

        content += f"""
        <div class="card" style="border-left:3px solid #a78bfa; margin-bottom:16px;">
          <div class="label" style="color:#a78bfa;">ATTACHED TO EC2 INSTANCE (from TF state)</div>
          <div style="margin:8px 0; font-size:13px;">
            <div style="color:#e2e8f0;"><strong>Instance:</strong> <code style="color:#a78bfa;">{_e(iid)}</code>{root_badge}</div>
            {'<div style="color:#94a3b8; font-size:12px;">Device: ' + _e(dev_name) + "</div>" if dev_name else ""}
            {'<div style="color:#94a3b8; font-size:12px;">TF Address: <code style="color:#3b82f6;">' + _e(tf_addr) + "</code></div>" if tf_addr else ""}
            {'<div style="color:#94a3b8; font-size:12px;">File: <code style="color:#3b82f6;">' + _e(parent_file) + "</code></div>" if parent_file else ""}
          </div>
          <p style="color:#94a3b8; font-size:12px;">
            {"This is a root volume managed by the parent EC2 instance. Fix tags on the instance, not the volume." if is_root else "This EBS volume is attached to the instance above."}
          </p>
        </div>"""

    return content


def _generate_tag_command(resource_arn, region):
    """Generate an AWS CLI tagging command for a suspected click-ops resource."""
    resource_id = ""
    if "/" in resource_arn:
        resource_id = resource_arn.split("/")[-1]
    elif ":secret:" in resource_arn:
        resource_id = resource_arn  # Secrets use full ARN

    if not resource_id:
        return ""

    # EC2 resources use ec2 create-tags
    if ":ec2:" in resource_arn:
        return (
            f"aws ec2 create-tags --region {region} \\\n"
            f"  --resources {resource_id} \\\n"
            f"  --tags \\\n"
            f"    Key=Project,Value={PROJECT} \\\n"
            f"    Key=Environment,Value={ENVIRONMENT} \\\n"
            f"    Key=ManagedBy,Value=manual \\\n"
            f"    Key=Lifecycle,Value=persistent"
        )

    # For everything else, use resourcegroupstaggingapi
    return (
        f"aws resourcegroupstaggingapi tag-resources --region {region} \\\n"
        f"  --resource-arn-list {resource_arn} \\\n"
        f"  --tags \\\n"
        f"    Project={PROJECT} \\\n"
        f"    Environment={ENVIRONMENT} \\\n"
        f"    ManagedBy=manual \\\n"
        f"    Lifecycle=persistent"
    )


def _generate_destroy_command(resource_arn, resource_type, region):
    """Generate an AWS CLI command to delete a resource."""
    resource_id = ""
    if "/" in resource_arn:
        resource_id = resource_arn.split("/")[-1]
    elif ":secret:" in resource_arn:
        resource_id = resource_arn

    cli = os.environ.get("CLI_PREFIX") or "aws"

    # EC2 instances
    if resource_type == "ec2:instance" or ":instance/" in resource_arn:
        return (
            f"# First stop the instance, then terminate\n"
            f"{cli} ec2 stop-instances --region {region} \\\n"
            f"  --instance-ids {resource_id}\n\n"
            f"# Verify it's stopped, then terminate\n"
            f"{cli} ec2 terminate-instances --region {region} \\\n"
            f"  --instance-ids {resource_id}"
        )

    # EBS volumes
    if resource_type == "ec2:volume" or ":volume/" in resource_arn:
        return (
            f"# Detach first if attached (--force if instance is stopped)\n"
            f"{cli} ec2 detach-volume --region {region} \\\n"
            f"  --volume-id {resource_id} --force\n\n"
            f"# Then delete the volume\n"
            f"{cli} ec2 delete-volume --region {region} \\\n"
            f"  --volume-id {resource_id}"
        )

    # Security groups
    if resource_type == "ec2:security-group" or ":security-group/" in resource_arn:
        return (
            f"# Remove all inbound/outbound rules first, then delete\n"
            f"# (will fail if still in use by other resources)\n"
            f"{cli} ec2 delete-security-group --region {region} \\\n"
            f"  --group-id {resource_id}"
        )

    # RDS instances
    if ":rds:" in resource_arn and resource_type in ("rds:instance", "rds:db"):
        db_id = resource_arn.split(":")[-1]
        return (
            f"# Create a final snapshot, then delete\n"
            f"{cli} rds delete-db-instance --region {region} \\\n"
            f"  --db-instance-identifier {db_id} \\\n"
            f"  --final-db-snapshot-identifier {db_id}-final-snapshot \\\n"
            f"  --no-delete-automated-backups"
        )

    # ECS services
    if resource_type == "ecs:service" or (":ecs:" in resource_arn and ":service/" in resource_arn):
        parts = resource_arn.split("/")
        if len(parts) >= 3:
            cluster, service = parts[-2], parts[-1]
            return (
                f"# Scale to zero, then delete\n"
                f"{cli} ecs update-service --region {region} \\\n"
                f"  --cluster {cluster} --service {service} \\\n"
                f"  --desired-count 0\n\n"
                f"{cli} ecs delete-service --region {region} \\\n"
                f"  --cluster {cluster} --service {service} --force"
            )

    # Lambda functions
    if ":lambda:" in resource_arn:
        fn_name = resource_arn.split(":")[-1]
        return f"{cli} lambda delete-function --region {region} \\\n  --function-name {fn_name}"

    # S3 buckets
    if ":s3:::" in resource_arn:
        bucket = resource_arn.split(":::")[-1]
        return (
            f"# Empty the bucket first (including versions), then delete\n"
            f"{cli} s3 rm s3://{bucket} --recursive\n\n"
            f"# If versioned, also delete old versions\n"
            f"{cli} s3api list-object-versions --region {region} \\\n"
            f"  --bucket {bucket} --output json \\\n"
            f'  | python3 -c "import sys,json; \\\n'
            f"    d=json.load(sys.stdin); \\\n"
            f"    [print(v['Key'],v['VersionId']) \\\n"
            f"     for v in d.get('Versions',[])+d.get('DeleteMarkers',[])]\"\n\n"
            f"{cli} s3 rb s3://{bucket}"
        )

    # Secrets Manager
    if ":secretsmanager:" in resource_arn:
        secret_id = resource_arn.split(":secret:")[-1] if ":secret:" in resource_arn else resource_id
        return (
            f"# Schedule deletion (7-day recovery window by default)\n"
            f"{cli} secretsmanager delete-secret --region {region} \\\n"
            f"  --secret-id {secret_id} \\\n"
            f"  --recovery-window-in-days 7"
        )

    # ElastiCache
    if ":elasticache:" in resource_arn:
        cluster_id = resource_arn.split(":")[-1]
        return f"{cli} elasticache delete-cache-cluster --region {region} \\\n  --cache-cluster-id {cluster_id}"

    # DynamoDB tables
    if ":dynamodb:" in resource_arn and "/table/" in resource_arn:
        table_name = resource_arn.split("/")[-1]
        return f"{cli} dynamodb delete-table --region {region} \\\n  --table-name {table_name}"

    # EFS filesystems
    if ":efs:" in resource_arn:
        fs_id = resource_arn.split("/")[-1]
        return (
            f"# Delete mount targets first, then the filesystem\n"
            f"{cli} efs describe-mount-targets --region {region} \\\n"
            f"  --file-system-id {fs_id} \\\n"
            f"  --query 'MountTargets[].MountTargetId' --output text\n\n"
            f"# For each mount target:\n"
            f"# {cli} efs delete-mount-target --region {region} --mount-target-id <id>\n\n"
            f"{cli} efs delete-file-system --region {region} \\\n"
            f"  --file-system-id {fs_id}"
        )

    # Load balancers
    if ":elasticloadbalancing:" in resource_arn or ":elbv2:" in resource_arn:
        return (
            f"# Delete listeners first, then the load balancer\n"
            f"{cli} elbv2 delete-load-balancer --region {region} \\\n"
            f"  --load-balancer-arn {resource_arn}"
        )

    # SNS topics
    if ":sns:" in resource_arn:
        return f"{cli} sns delete-topic --region {region} \\\n  --topic-arn {resource_arn}"

    # SQS queues
    if ":sqs:" in resource_arn:
        return f"{cli} sqs delete-queue --region {region} \\\n  --queue-url {resource_arn}"

    # CloudWatch log groups
    if ":log-group:" in resource_arn:
        lg_name = resource_arn.split(":")[-1].rstrip("*")
        return f"{cli} logs delete-log-group --region {region} \\\n  --log-group-name {lg_name}"

    # SSM parameters
    if ":ssm:" in resource_arn and ":parameter/" in resource_arn:
        param_name = resource_arn.split(":parameter/")[-1] if ":parameter/" in resource_arn else resource_id
        return f"{cli} ssm delete-parameter --region {region} \\\n  --name {param_name}"

    # Generic fallback — resource type not recognized
    return ""

"""
Repos page — multi-repo IaC registry overview.

Shows all registered repositories with their IaC type, resource count,
compliance rate, and estimated monthly cost. Each repo links to a
filtered tree view of its resources. Includes platform config and
onboarding checklist (merged from the former satellites page).
"""

import json
import os

import boto3
from compliance_schema import repo_slug
from data import get_compliance_report
from data_core import PROJECT, REPOSITORY_URL, TF_STATE_BUCKET
from views_base import NAV_CSS, _badge, _e, _rate_color, _safe_url, render_nav

# IaC type -> display badge color
_IAC_COLORS = {
    "terraform": "purple",
    "serverless": "orange",
    "pulumi": "blue",
    "cdk": "green",
    "cloudformation": "gray",
    "sam": "teal",
    "copilot": "cyan",
    "manual": "red",
}

_platform_config_cache = None

# Row that counts findings whose resource names no owning repository. They are
# never attributed to the platform repository.
UNCLAIMED = "Unclaimed"


def _load_platform_config():
    """Load platform config from SSM (cached for Lambda lifetime)."""
    global _platform_config_cache
    if _platform_config_cache is not None:
        return _platform_config_cache

    project = PROJECT
    environment = os.environ.get("ENVIRONMENT", "dev")
    param_name = f"/{project}/{environment}/platform-config"
    try:
        ssm = boto3.client("ssm")
        resp = ssm.get_parameter(Name=param_name)
        _platform_config_cache = json.loads(resp["Parameter"]["Value"])
    except Exception as e:
        print(f"Warning: could not load platform config from SSM ({param_name}): {e}")
        _platform_config_cache = {}
    return _platform_config_cache


def render_repos(user_email=""):
    """Repos page — list all registered IaC repositories."""
    report = get_compliance_report()
    registry = report.get("iac_registry", {}) if report else {}
    entries = registry.get("entries", [])
    total_external = registry.get("total_external_managed", 0)

    # Calculate compliance rate per repo from findings
    findings = []
    for cat in ("tag_compliance", "drift_detection", "schedule_compliance", "lifecycle_compliance"):
        findings.extend((report.get(cat, {}).get("findings", [])) if report else [])

    repo_finding_counts = {}
    for f in findings:
        slug = f.get("managed_by_repo_slug", "") or UNCLAIMED
        repo_finding_counts[slug] = repo_finding_counts.get(slug, 0) + 1

    # Determine lifecycle + nearest ExpiresAt per repo from discovered resource tags
    repo_lifecycle = {}  # slug -> "temporary" | "persistent" | ""
    repo_expires_at = {}  # slug -> nearest ISO datetime string
    for r in report.get("discovered_resources", []) if report else []:
        tags = r.get("tags") or {}
        repo_tag = tags.get("Repository", "")
        lc = tags.get("Lifecycle", "")
        expires = tags.get("ExpiresAt", "")
        if not repo_tag:
            continue
        for entry in entries:
            slug = entry.get("repo_slug", "")
            repo_url = entry.get("repo_url", "")
            if (repo_url and repo_tag == repo_url) or repo_tag.rstrip("/").endswith("/" + slug):
                if lc == "temporary":
                    repo_lifecycle[slug] = "temporary"
                elif lc and slug not in repo_lifecycle:
                    repo_lifecycle[slug] = lc
                if expires:
                    prev = repo_expires_at.get(slug, "")
                    if not prev or expires < prev:
                        repo_expires_at[slug] = expires
                break

    # Build summary cards
    total_repos = len(entries)
    total_resources = sum(e.get("resource_count", 0) for e in entries)

    summary_html = f"""
    <div class="cards">
      <div class="card"><div class="card-label">Registered Repos</div>
        <div class="card-value">{total_repos}</div></div>
      <div class="card"><div class="card-label">Total Managed Resources</div>
        <div class="card-value">{total_resources}</div></div>
      <div class="card"><div class="card-label">External (non-TF) Managed</div>
        <div class="card-value">{total_external}</div></div>
    </div>
    """

    # Build repos table
    unclaimed_count = repo_finding_counts.get(UNCLAIMED, 0)
    if entries or unclaimed_count:
        rows = ""
        for entry in sorted(entries, key=lambda e: e.get("resource_count", 0), reverse=True):
            slug = entry.get("repo_slug", "")
            repo_url = _safe_url(entry.get("repo_url", ""))
            iac_type = entry.get("iac_type", "unknown")
            resource_count = entry.get("resource_count", 0)
            last_deploy = entry.get("last_deploy", "—")
            finding_count = repo_finding_counts.get(slug, 0)

            if resource_count > 0:
                compliance_rate = max(0, (resource_count - finding_count) / resource_count)
            else:
                compliance_rate = 1.0

            rate_pct = f"{compliance_rate:.0%}"
            rate_color = _rate_color(compliance_rate * 100)

            iac_color = _IAC_COLORS.get(iac_type, "gray")
            iac_badge = _badge(_e(iac_type), color=iac_color)

            # Repo name links to detail page; GitHub is a secondary icon
            import urllib.parse as _up

            repo_base = f"/repos/{_up.quote(slug, safe='')}"
            name_html = (
                f'<a href="{repo_base}" style="color:#60a5fa; text-decoration:none; font-weight:600;">{_e(slug)}</a>'
            )
            if repo_url:
                name_html += f' <a href="{_e(repo_url)}" target="_blank" style="color:#6b7280; text-decoration:none; font-size:11px;" title="GitHub repo">&#8599;</a>'

            rows += f"""
            <tr>
              <td>{name_html}</td>
              <td>{iac_badge}</td>
              <td style="text-align:right">{resource_count}</td>
              <td style="text-align:right">{finding_count}</td>
              <td style="text-align:right; color:{rate_color}; font-weight:600">{rate_pct}</td>
              <td style="color:#94a3b8; font-size:12px">{_e(last_deploy[:19] if len(last_deploy) > 19 else last_deploy)}</td>
              <td>{_lifecycle_badge(repo_lifecycle.get(slug, ""), repo_expires_at.get(slug, ""))}</td>
              <td style="white-space:nowrap">
                <a href="{repo_base}/architecture" class="view-pill" title="Architecture">arch</a>
                <a href="{repo_base}/tree" class="view-pill" title="Resource tree">tree</a>
                <a href="{repo_base}/network" class="view-pill" title="Network graph">net</a>
              </td>
            </tr>
            """

        if unclaimed_count:
            rows += f"""
            <tr class="unclaimed">
              <td title="Findings on resources that name no owning repository">{UNCLAIMED}</td>
              <td>&mdash;</td>
              <td style="text-align:right">&mdash;</td>
              <td style="text-align:right">{unclaimed_count}</td>
              <td style="text-align:right">&mdash;</td>
              <td>&mdash;</td>
              <td>&mdash;</td>
              <td></td>
            </tr>
            """

        table_html = f"""
        <table class="table">
          <thead>
            <tr>
              <th>Repository</th>
              <th>IaC Type</th>
              <th style="text-align:right">Resources</th>
              <th style="text-align:right">Findings</th>
              <th style="text-align:right">Compliance</th>
              <th>Last Deploy</th>
              <th>Lifecycle</th>
              <th>Views</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        """
    else:
        table_html = """
        <div class="alert alert-info">
          No IaC repositories registered yet. The compliance scanner will populate
          this page once repos register via SSM Parameter Store under the
          <code>/&lt;project&gt;/&lt;environment&gt;/iac-registry/</code> prefix.
        </div>
        """

    # Unregistered CloudFormation stacks
    unregistered = report.get("unregistered_cfn_stacks", []) if report else []
    unreg_html = ""
    if unregistered:
        unreg_rows = ""
        for stack in unregistered:
            unreg_rows += f"""
            <tr>
              <td>{_e(stack.get("stack_name", ""))}</td>
              <td>{_badge(_e(stack.get("iac_type", "cloudformation")), color=_IAC_COLORS.get(stack.get("iac_type", ""), "gray"))}</td>
              <td style="text-align:right">{_e(str(stack.get("resource_count", 0)))}</td>
              <td style="color:#94a3b8; font-size:12px">{_e(stack.get("region", ""))}</td>
            </tr>
            """
        unreg_html = f"""
        <h2 style="margin-top:32px">UNREGISTERED CLOUDFORMATION STACKS</h2>
        <p style="color:#94a3b8; font-size:13px; margin-bottom:12px">
          These stacks were discovered in the account but are not registered in the IaC registry.
          Register them to get proper attribution on the dashboard.
        </p>
        <table class="table">
          <thead><tr><th>Stack Name</th><th>Type</th><th style="text-align:right">Resources</th><th>Region</th></tr></thead>
          <tbody>{unreg_rows}</tbody>
        </table>
        """

    # ── Platform Config ────────────────────────────────────────────────────
    config = _load_platform_config()
    config_html = _render_platform_config(config)

    # ── Onboarding Checklist ───────────────────────────────────────────────
    state_bucket = config.get("state_bucket") or TF_STATE_BUCKET
    guide_url = config.get("compliance_guide_url", "")
    onboarding_html = _render_onboarding(
        state_bucket, guide_url, config.get("lock_table", ""), config.get("tag_module_source", "")
    )

    nav = render_nav("repos", user_email)
    content = f"""<!DOCTYPE html><html><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Repos — Compliance Dashboard</title>
    <style>{NAV_CSS}{_page_css()}</style>
    </head><body>
    {nav}
    <main class="main">
      <h1>REPOSITORIES</h1>
      <p style="color:#94a3b8; margin-bottom:20px; font-size:13px;">
        All IaC repositories deploying infrastructure to this AWS account.
        Resources are attributed via the IaC registry (SSM Parameter Store).
      </p>
      {summary_html}
      {table_html}
      {unreg_html}
      {config_html}
      {onboarding_html}
    </main>
    </body></html>"""

    return content


def _render_platform_config(config):
    """Render the platform config section (trusted orgs, regions, state bucket, role)."""
    if not config:
        return ""

    trusted_orgs = config.get("trusted_github_orgs", [])
    allowed_regions = config.get("deploy_allowed_regions", [])
    state_bucket = config.get("state_bucket", "—")
    deploy_role = config.get("deploy_role_arn", "—")

    orgs_html = (
        " ".join(f'<span class="chip">{_e(org)}</span>' for org in trusted_orgs)
        or '<span style="color:#94a3b8">none configured</span>'
    )
    regions_html = (
        " ".join(f'<span class="chip">{_e(r)}</span>' for r in allowed_regions)
        or '<span style="color:#94a3b8">none configured</span>'
    )
    role_short = deploy_role.split("/")[-1] if "/" in deploy_role else deploy_role

    return f"""
    <h2 style="margin-top:32px">PLATFORM CONFIG</h2>
    <p class="subtitle">Central configuration for satellite repos, read from the <code>platform-config</code> parameter that <code>{_e(repo_slug(REPOSITORY_URL))}</code> publishes.</p>
    <div class="config-grid">
      <div class="config-card">
        <div class="config-label">Trusted GitHub Orgs</div>
        <div class="config-value">{orgs_html}</div>
        <div class="config-hint">Repos in these orgs can assume the deploy role (main branch only)</div>
      </div>
      <div class="config-card">
        <div class="config-label">Allowed AWS Regions</div>
        <div class="config-value">{regions_html}</div>
        <div class="config-hint">Deploy role is restricted to these regions</div>
      </div>
      <div class="config-card">
        <div class="config-label">Terraform State Bucket</div>
        <div class="config-value"><code>{_e(state_bucket)}</code></div>
        <div class="config-hint">Satellite repos store state under <code>&lt;repo&gt;/dev/terraform.tfstate</code></div>
      </div>
      <div class="config-card">
        <div class="config-label">Deploy Role</div>
        <div class="config-value"><code title="{_e(deploy_role)}">{_e(role_short)}</code></div>
        <div class="config-hint">OIDC role assumed by GitHub Actions</div>
      </div>
    </div>
    """


def _render_onboarding(state_bucket, guide_url="", lock_table="", tag_module_source=""):
    """Render the onboarding checklist for new satellite repos."""
    environment = os.environ.get("ENVIRONMENT", "")
    # A value that is not known shows as a placeholder, never as an empty path segment.
    project_seg = _e(PROJECT) if PROJECT else "&lt;your-project&gt;"
    env_seg = _e(environment) if environment else "&lt;your-environment&gt;"
    bucket_seg = _e(state_bucket) if state_bucket and state_bucket != "—" else "&lt;your-state-bucket&gt;"
    source_html = f"Source from <code>{_e(tag_module_source)}</code>." if tag_module_source else ""
    lock_html = f"\n             Lock table: <code>{_e(lock_table)}</code>." if lock_table else ""
    guide_url = _safe_url(guide_url)
    guide_link = f' See the <a href="{_e(guide_url)}" target="_blank">full onboarding guide</a>.' if guide_url else ""

    return f"""
    <h2 style="margin-top:32px">ONBOARDING A NEW REPO</h2>
    <p class="subtitle">What a new satellite repo needs to do.{guide_link}</p>
    <div class="checklist">
      <div class="check-item">
        <span class="check-num">1</span>
        <div>
          <strong>Use the compliance-tags module</strong>
          <p>{source_html}
             All resources get the 5 required tags via <code>default_tags</code>.</p>
        </div>
      </div>
      <div class="check-item">
        <span class="check-num">2</span>
        <div>
          <strong>Register in the IaC registry</strong>
          <p>Use the <code>iac-registry</code> module (Terraform) or write an SSM parameter
             at <code>/{project_seg}/{env_seg}/iac-registry/&lt;repo-slug&gt;</code> (CI/CD).</p>
        </div>
      </div>
      <div class="check-item">
        <span class="check-num">3</span>
        <div>
          <strong>Store Terraform state in the shared bucket</strong>
          <p>Backend: <code>s3://{bucket_seg}/&lt;repo&gt;/&lt;environment&gt;/terraform.tfstate</code>.{lock_html}</p>
        </div>
      </div>
      <div class="check-item">
        <span class="check-num">4</span>
        <div>
          <strong>Set up GitHub Actions OIDC</strong>
          <p>Use <code>aws-actions/configure-aws-credentials</code> with the deploy role ARN.
             Any repo in a trusted org on the main branch can assume the role.</p>
        </div>
      </div>
    </div>
    """


def _lifecycle_badge(lifecycle, expires_at=""):
    """Render a lifecycle badge with optional teardown date for temporary stacks."""
    if lifecycle == "temporary":
        badge = '<span style="color:#f59e0b; font-size:11px; font-weight:600;">temporary</span>'
        if expires_at:
            badge += (
                f'<br><span style="color:#94a3b8; font-size:10px;" '
                f'title="Full stack terraform destroy at {_e(expires_at)} (all resources removed)">'
                f"teardown {_format_expiry(expires_at)}</span>"
            )
        return badge
    if lifecycle == "persistent":
        return '<span style="color:#22c55e; font-size:11px;">persistent</span>'
    return '<span style="color:#4b5563; font-size:11px;">&mdash;</span>'


def _format_expiry(iso_str):
    """Format an ExpiresAt ISO string as a relative/short display."""
    from datetime import UTC, datetime

    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        delta = dt - now
        if delta.total_seconds() < 0:
            return "overdue"
        days = delta.days
        if days == 0:
            hours = int(delta.total_seconds() // 3600)
            if hours == 0:
                return "< 1h"
            return f"in {hours}h"
        if days == 1:
            return "tomorrow"
        if days < 7:
            return f"in {days}d"
        return dt.strftime("%b %d")
    except (ValueError, TypeError):
        return _e(iso_str[:10])


def _page_css():
    """CSS for the repos page."""
    return """
    body { margin:0; background:#0f172a; color:#e2e8f0; font-family:'Inter','Segoe UI',system-ui,sans-serif; }
    .main { max-width:1200px; margin:0 auto; padding:24px; }
    h1 { font-size:18px; font-weight:700; letter-spacing:2px; color:#f1f5f9; margin-bottom:4px; }
    h2 { font-size:14px; font-weight:700; letter-spacing:1.5px; color:#f1f5f9; margin-bottom:8px; margin-top:24px; }
    h3 { font-size:13px; font-weight:600; color:#f1f5f9; margin:0 0 8px 0; }
    .subtitle { color:#94a3b8; font-size:13px; margin-bottom:12px; }
    .cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin-bottom:24px; }
    .card { background:#1e293b; border:1px solid #334155; border-radius:8px; padding:16px; }
    .card-label { font-size:11px; text-transform:uppercase; letter-spacing:1px; color:#94a3b8; margin-bottom:4px; }
    .card-value { font-size:28px; font-weight:700; color:#f1f5f9; }
    .table { width:100%; border-collapse:collapse; font-size:13px; }
    .table th { text-align:left; padding:8px 12px; background:#1e293b; color:#94a3b8;
                font-size:11px; text-transform:uppercase; letter-spacing:0.5px; border-bottom:1px solid #334155; }
    .table td { padding:8px 12px; border-bottom:1px solid #1e293b; }
    .table tr:hover { background:#1e293b; }
    .badge { display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
    .badge-purple { background:#7c3aed22; color:#a78bfa; border:1px solid #7c3aed44; }
    .badge-orange { background:#f9731622; color:#fb923c; border:1px solid #f9731644; }
    .badge-blue { background:#3b82f622; color:#60a5fa; border:1px solid #3b82f644; }
    .badge-green { background:#22c55e22; color:#4ade80; border:1px solid #22c55e44; }
    .badge-gray { background:#6b728022; color:#94a3b8; border:1px solid #6b728044; }
    .badge-red { background:#ef444422; color:#f87171; border:1px solid #ef444444; }
    .badge-teal { background:#14b8a622; color:#2dd4bf; border:1px solid #14b8a644; }
    .badge-cyan { background:#06b6d422; color:#22d3ee; border:1px solid #06b6d444; }
    .alert { padding:16px; border-radius:8px; margin-bottom:16px; font-size:13px; }
    .alert-info { background:#1e3a5f; border:1px solid #2563eb44; color:#93c5fd; }
    code { background:#334155; padding:2px 6px; border-radius:4px; font-size:12px; }
    a { color:#60a5fa; }
    .view-pill {
      display:inline-block; padding:2px 8px; border-radius:4px; font-size:10px; font-weight:600;
      text-decoration:none; color:#94a3b8; background:#1e293b; border:1px solid #334155;
      transition: all 0.15s;
    }
    .view-pill:hover { color:#60a5fa; border-color:#3b82f644; background:#3b82f611; }
    .config-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:12px; margin-bottom:24px; }
    .config-card { background:#1e293b; border:1px solid #334155; border-radius:8px; padding:16px; }
    .config-label { font-size:11px; text-transform:uppercase; letter-spacing:1px; color:#94a3b8; margin-bottom:6px; }
    .config-value { font-size:14px; color:#f1f5f9; margin-bottom:4px; }
    .config-hint { font-size:11px; color:#64748b; }
    .chip { display:inline-block; padding:2px 10px; border-radius:12px; font-size:12px; font-weight:500;
            background:#334155; color:#e2e8f0; border:1px solid #475569; margin-right:4px; }
    .checklist { display:flex; flex-direction:column; gap:12px; }
    .check-item { display:flex; gap:12px; background:#1e293b; border:1px solid #334155; border-radius:8px; padding:14px; }
    .check-item p { font-size:12px; color:#94a3b8; margin:4px 0 0 0; }
    .check-item strong { font-size:13px; color:#f1f5f9; }
    .check-num { display:flex; align-items:center; justify-content:center; width:28px; height:28px; min-width:28px;
                 border-radius:50%; background:#334155; color:#e2e8f0; font-size:13px; font-weight:700; }
    """

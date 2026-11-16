"""
Overview page — explains the site for first-time visitors.

The built-in body describes this tooling only. A deployment replaces it with
its own ``overview.html`` (see site_config).
"""

import site_config
from data_core import PROJECT, REGION
from views_base import ENVIRONMENT, _badge, _e, _layout


def render_overview(user_email=""):
    """Render the platform overview — what everything is and how it fits together."""
    env = _e(ENVIRONMENT)

    custom = site_config.overview_html()
    if custom is not None:
        return _layout("Overview", custom, user_email, active_page="overview")

    custom = site_config.overview_html()
    if custom is not None:
        return _layout("Overview", custom, user_email, active_page="overview")

    content = f"""
    <h1>PLATFORM OVERVIEW</h1>
    <p style="color:#94a3b8; margin-bottom:32px; font-size:13px;">
      Everything you need to know about what this infrastructure is, what it runs,
      and how the pieces connect. Start here if you're new.
    </p>

    <!-- ── What is this? ── -->
    <h2>WHAT IS THIS?</h2>
    <div class="card" style="margin-bottom:24px; padding:16px; font-size:13px; color:#94a3b8;">
      <p style="margin-bottom:8px;">
        This is the cloud infrastructure of the <strong style="color:#e2e8f0;">{_e(PROJECT)}</strong>
        project. It runs on AWS
        (<code style="color:#f59e0b;">{_e(REGION)}</code>), serves the
        <strong style="color:#3b82f6;">{env}</strong> environment, and is fully managed
        with <strong style="color:#e2e8f0;">Terraform</strong> (Infrastructure as Code).
      </p>
      <p>
        <strong style="color:#e2e8f0;">This dashboard</strong> is the governance command center.
        The scanner reads the entire AWS account on a schedule; the dashboard tracks compliance and can enforce policy.
        The page you're reading now explains what's being governed.
      </p>
    </div>

    <!-- ── Services ── -->
    <h2>SERVICES</h2>
    <p style="color:#94a3b8; font-size:13px; margin-bottom:24px;">
      See <a href="/services" style="color:#3b82f6;">Services</a> for the live catalog of what is deployed,
      discovered from the Terraform state, and <a href="/architecture" style="color:#3b82f6;">Architecture</a>
      for how the pieces connect.
    </p>

    <!-- ── Governance & Compliance ── -->
    <h2>GOVERNANCE &amp; COMPLIANCE</h2>
    <p style="color:#94a3b8; font-size:13px; margin-bottom:12px;">
      Automated scanning, reporting, and enforcement keep the AWS account clean.
    </p>
    <div style="display:grid; gap:12px; margin-bottom:24px;">
      <div class="card" style="border-left:3px solid #3b82f6; padding:12px 16px;">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
          <strong style="color:#e2e8f0; font-size:14px;">Compliance Scanner</strong>
          {_badge("Lambda &middot; scheduled", "blue")}
        </div>
        <p style="color:#94a3b8; font-size:13px;">
          Discovers <strong style="color:#e2e8f0;">every resource</strong> in the AWS account
          via Cost Explorer, Resource Groups Tagging API, and service-specific APIs.
          Runs four checks: tag compliance (required tags), Terraform drift detection (click-ops finder),
          schedule compliance (idle resources), and lifecycle compliance (expired resources).
          Produces JSON, Markdown, and styled HTML reports stored in S3, plus an email summary via SES.
        </p>
      </div>
      <div class="card" style="border-left:3px solid #ef4444; padding:12px 16px;">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
          <strong style="color:#e2e8f0; font-size:14px;">Dashboard</strong>
          {_badge("this site", "red")}
        </div>
        <p style="color:#94a3b8; font-size:13px;">
          Interactive command center for the compliance data. Provides findings tables,
          per-service breakdowns, cost burn analysis, security posture, architecture visualizations,
          Terraform module trees, network topology maps, and context-aware remediation (CURE).
          Sign-in is handled in front of it by an OIDC proxy or the load balancer, with an optional viewer gate.
        </p>
      </div>
      <div class="card" style="border-left:3px solid #f59e0b; padding:12px 16px;">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
          <strong style="color:#e2e8f0; font-size:14px;">Hunterkiller</strong>
          {_badge("Lambda &middot; enforcement", "amber")}
        </div>
        <p style="color:#94a3b8; font-size:13px;">
          Automated enforcement engine. Reads compliance findings, emails resource owners a 24-hour warning,
          then tears down unresolved non-compliant resources. Three-layer safety model: notifier/executor split,
          DynamoDB approval gate, and Terraform-gated permissions (ABAC tag conditions).
        </p>
      </div>
    </div>

    <!-- ── Key Decisions ── -->
    <h2>KEY DESIGN DECISIONS</h2>
    <div style="display:grid; gap:12px; margin-bottom:24px;">
      <div class="card" style="padding:12px 16px;">
        <strong style="color:#e2e8f0;">Why Hunterkiller has three safety layers</strong>
        <p style="color:#94a3b8; font-size:13px; margin-top:4px;">
          Automated resource destruction is dangerous. The notifier/executor split means the notifier
          has zero destructive IAM permissions. The DynamoDB approval gate requires explicit approval before
          any teardown. Terraform-gated ABAC tag conditions prevent the executor from touching resources
          outside its scope.
        </p>
      </div>
    </div>

    <div style="margin-top:24px;">
      <a href="/" class="btn btn-secondary">&larr; Dashboard</a>
      <a href="/guide" class="btn btn-primary" style="margin-left:8px;">Dashboard Guide &rarr;</a>
    </div>"""

    return _layout("Overview", content, user_email, active_page="overview")

"""
Static documentation pages — Guide and Docs.

These are reference pages with no dynamic data, just inline HTML content.
"""

from data_core import PROJECT
from views_base import ENVIRONMENT, _badge, _e, _layout


def render_guide(user_email=""):
    """Render the __BRAND__ guide — annotated explainer of the dashboard."""
    content = """
    <h1>__BRAND__ GUIDE</h1>
    <p style="color:#94a3b8; margin-bottom:24px; font-size:13px;">
      How to read and use the compliance command center. Each section below explains a part of the dashboard UI.
    </p>

    <h2>NAVIGATION BAR</h2>
    <div class="card" style="margin-bottom:16px; padding:16px;">
      <div style="display:flex; gap:8px; margin-bottom:16px; padding:12px; background:#0d0d14; border-radius:4px; flex-wrap:wrap; align-items:center;">
        <span style="color:#ef4444; font-weight:700; font-size:14px; margin-right:8px;">__BRAND__</span>
        <span style="padding:4px 12px; border-bottom:2px solid #ef4444; color:#ef4444; font-size:12px;">Dashboard</span>
        <span style="padding:4px 12px; color:#6b7280; font-size:12px;">Changes</span>
        <span style="padding:4px 12px; color:#6b7280; font-size:12px; border:1px solid #2a2a3a; border-radius:4px;">Observability &#x25be;</span>
        <span style="padding:4px 12px; color:#6b7280; font-size:12px; border:1px solid #2a2a3a; border-radius:4px;">Viz &#x25be;</span>
        <span style="padding:4px 12px; color:#6b7280; font-size:12px; border:1px solid #2a2a3a; border-radius:4px;">Docs &#x25be;</span>
      </div>
      <div style="display:grid; grid-template-columns:140px 1fr; gap:8px 16px; font-size:13px;">
        <div style="color:#3b82f6; font-weight:600;">Dashboard</div>
        <div style="color:#94a3b8;">Home page. Summary cards: resource counts, severity breakdown, pending destroys, active overrides, compliance status. Download buttons for the latest HTML and JSON reports. Start here for situational awareness.</div>
        <div style="color:#f59e0b; font-weight:600;">Changes</div>
        <div style="color:#94a3b8;">24-hour delta view. Shows new, resolved, and changed findings since the previous scan. Quickly see what's different today.</div>

        <div style="color:#a78bfa; font-weight:600; grid-column:1/-1; margin-top:8px; font-size:11px; text-transform:uppercase; letter-spacing:0.1em;">Observability</div>
        <div style="color:#3b82f6; font-weight:600;">Services</div>
        <div style="color:#94a3b8;">Per-service breakdown. Groups resources by the Service tag and shows compliance status, cost, and resource counts for each logical service.</div>
        <div style="color:#a78bfa; font-weight:600;">Security</div>
        <div style="color:#94a3b8;">Security-focused view. IAM analysis, public exposure, encryption status, and security group findings.</div>
        <div style="color:#ef4444; font-weight:600;">Burn</div>
        <div style="color:#94a3b8;">Cost burn analysis. Per-service spend visualization, resource type cost breakdown, regional spend comparison (estimated vs actual from Cost Explorer), and non-compliant burn rates.</div>

        <div style="color:#a78bfa; font-weight:600; grid-column:1/-1; margin-top:8px; font-size:11px; text-transform:uppercase; letter-spacing:0.1em;">Viz</div>
        <div style="color:#94a3b8; font-weight:600;">Tree</div>
        <div style="color:#94a3b8;">Terraform module tree. Hierarchical view of the IaC structure showing modules, resources, and their relationships from the Terraform state.</div>
        <div style="color:#22c55e; font-weight:600;">Network</div>
        <div style="color:#94a3b8;">Network topology. D3 force-directed graph showing VPC, subnets, security groups, and connectivity. Toggle TF-only to filter to IaC-managed resources.</div>
        <div style="color:#3b82f6; font-weight:600;">Architecture</div>
        <div style="color:#94a3b8;">Infrastructure architecture diagrams. Holistic view groups services by network zone with cost badges. Click any service for a detailed view showing sub-module breakdown, resource costs, service connections, and cost insight panel.</div>

        <div style="color:#a78bfa; font-weight:600; grid-column:1/-1; margin-top:8px; font-size:11px; text-transform:uppercase; letter-spacing:0.1em;">Docs</div>
        <div style="color:#94a3b8; font-weight:600;">Guide</div>
        <div style="color:#94a3b8;">This page. Explains each UI element.</div>
        <div style="color:#94a3b8; font-weight:600;">Reference</div>
        <div style="color:#94a3b8;">Compliance rules reference: what tags are required, what the scanner checks, and how enforcement works.</div>
      </div>
    </div>

    <h2>BADGES</h2>
    <div class="card" style="margin-bottom:16px; padding:16px;">
      <div style="display:grid; grid-template-columns:auto 1fr; gap:12px 16px; align-items:center; font-size:13px;">
        {_badge("TF-MANAGED", "blue")}
        <span style="color:#94a3b8;">Resource is tracked in Terraform state. Fix compliance in the Terraform config, then <code style="color:#f59e0b;">terraform apply</code>.</span>
        {_badge("CLICK-OPS", "red")}
        <span style="color:#94a3b8;">Suspected manual creation — not found in the active Terraform state. May also come from another TF repo or AWS automation. Import into TF or tag manually.</span>
        {_badge("HIGH", "red")}
        <span style="color:#94a3b8;">Critical: missing required tags, likely unmanaged, or lifecycle violation. Address promptly.</span>
        {_badge("MEDIUM", "amber")}
        <span style="color:#94a3b8;">Moderate: possibly auto-created by AWS, missing optional tags, or schedule compliance gap.</span>
        {_badge("LOW", "green")}
        <span style="color:#94a3b8;">Minor/informational. Most tags present, minor scheduling issue.</span>
      </div>
    </div>

    <h2>FILTER BAR</h2>
    <div class="card" style="margin-bottom:16px; padding:16px;">
      <div style="display:flex; gap:6px; margin-bottom:16px; padding:12px; background:#0d0d14; border-radius:4px; flex-wrap:wrap; align-items:center;">
        <span style="padding:3px 8px; background:rgba(34,197,94,0.15); color:#22c55e; border-radius:4px; font-size:11px;">COMPLIANT</span>
        <span style="padding:3px 8px; background:rgba(239,68,68,0.15); color:#ef4444; border-radius:4px; font-size:11px;">NON-COMPLIANT</span>
        <span style="padding:3px 8px; background:#1e1e2e; color:#94a3b8; border-radius:4px; font-size:11px;">BOTH</span>
        <span style="border-left:1px solid #2a2a3a; height:18px; margin:0 4px;"></span>
        <span style="padding:3px 8px; background:#1e1e2e; color:#94a3b8; border-radius:4px; font-size:11px;">HIGH</span>
        <span style="padding:3px 8px; background:#1e1e2e; color:#94a3b8; border-radius:4px; font-size:11px;">MEDIUM</span>
        <span style="padding:3px 8px; background:#1e1e2e; color:#94a3b8; border-radius:4px; font-size:11px;">LOW</span>
        <span style="border-left:1px solid #2a2a3a; height:18px; margin:0 4px;"></span>
        <span style="padding:3px 8px; background:#1e1e2e; color:#94a3b8; border-radius:4px; font-size:11px;">TABLE</span>
        <span style="padding:3px 8px; background:#1e1e2e; color:#94a3b8; border-radius:4px; font-size:11px;">BY CONTACT</span>
        <span style="padding:3px 8px; background:#1e1e2e; color:#94a3b8; border-radius:4px; font-size:11px;">ANALYSIS</span>
      </div>
      <div style="font-size:13px; color:#94a3b8;">
        <p style="margin-bottom:8px;"><strong style="color:#e2e8f0;">Left group</strong> (compliance status): Switch between compliant-only, non-compliant-only, or both views.</p>
        <p style="margin-bottom:8px;"><strong style="color:#e2e8f0;">Middle group</strong> (severity): Filter non-compliant findings by severity level.</p>
        <p><strong style="color:#e2e8f0;">Right group</strong> (view mode): TABLE (flat list), BY CONTACT (grouped by resource owner), ANALYSIS (pivot tables by type/region/category/age). Findings page only.</p>
      </div>
    </div>

    <h2>ACTIONS</h2>
    <div class="card" style="margin-bottom:16px; padding:16px;">
      <div style="display:grid; grid-template-columns:auto 1fr; gap:12px 16px; align-items:center; font-size:13px;">
        <span class="btn btn-primary" style="font-size:10px; cursor:default; padding:3px 8px;">CURE</span>
        <span style="color:#94a3b8;">Opens remediation page for a TF-managed resource. Shows Terraform location, missing tags, and generated fix code.</span>
        <span class="btn btn-secondary" style="font-size:10px; cursor:default; padding:3px 8px;">DETAIL</span>
        <span style="color:#94a3b8;">Opens detail page for a suspected click-ops resource. Shows findings, pre-filled <code>aws</code> CLI tagging commands, and <code>terraform import</code> hints.</span>
        <span class="btn btn-secondary" style="font-size:10px; cursor:default; padding:3px 8px;">Exempt</span>
        <span style="color:#94a3b8;">Quick-mark a resource as false positive. Prompts for a reason. Exempt resources are excluded from findings. Manageable on the Overrides page.</span>
        <span class="btn btn-primary" style="font-size:10px; cursor:default; padding:3px 8px;">Abort</span>
        <span style="color:#94a3b8;"><em>Pending page only.</em> Cancel automated teardown. Resource stays in AWS. Admin-only.</span>
        <span class="btn btn-warn" style="font-size:10px; cursor:default; padding:3px 8px;">+24h</span>
        <span style="color:#94a3b8;"><em>Pending page only.</em> Extend grace period by 24 hours. Admin-only.</span>
        <span class="btn btn-danger" style="font-size:10px; cursor:default; padding:3px 8px;">Destroy Now</span>
        <span style="color:#94a3b8;"><em>Pending page only.</em> Immediately trigger resource destruction. Requires confirmation. Admin-only.</span>
      </div>
    </div>

    <h2>COMPLIANCE STATUS</h2>
    <div class="card" style="margin-bottom:16px; padding:16px;">
      <p style="color:#94a3b8; font-size:13px; margin-bottom:12px;">Shown on the Dashboard page. Based on the number of HIGH severity findings.</p>
      <div style="display:grid; grid-template-columns:auto 1fr; gap:8px 16px; align-items:center; font-size:13px;">
        <span style="color:#22c55e; font-weight:700;">GOOD</span>
        <span style="color:#94a3b8;">Fewer than 20 HIGH findings. Account well-managed.</span>
        <span style="color:#f59e0b; font-weight:700;">FAIR</span>
        <span style="color:#94a3b8;">20-100 HIGH findings. Remediation recommended.</span>
        <span style="color:#ef4444; font-weight:700;">POOR</span>
        <span style="color:#94a3b8;">Over 100 HIGH findings. Immediate attention required.</span>
      </div>
    </div>

    <h2>VIEWER vs ADMIN</h2>
    <div class="card" style="margin-bottom:16px; padding:16px; font-size:13px; color:#94a3b8;">
      <p style="margin-bottom:12px;">
        The dashboard has two access levels: <strong style="color:#3b82f6;">Viewer</strong> and <strong style="color:#22c55e;">Admin</strong>.
        Most of the dashboard is fully accessible to viewers. Admin privileges are only required for
        actions that <em>change</em> infrastructure state or access raw report data.
      </p>
      <p style="margin-bottom:4px; color:#e2e8f0; font-weight:600;">Everyone (viewers and admins) can:</p>
      <ul style="margin:0 0 16px 20px; line-height:1.8;">
        <li>Browse all compliance findings, filter by severity, view by contact, and run pivot analysis</li>
        <li>Open <strong style="color:#e2e8f0;">CURE</strong> remediation pages and <strong style="color:#e2e8f0;">DETAIL</strong> pages for any resource</li>
        <li>View pending destroys, overrides, protected resources, and 24-hour changes</li>
        <li>Explore cost burn analysis, security posture, and all architecture visualizations</li>
        <li>Navigate the Terraform module tree, network topology, and service catalog</li>
        <li>Read this guide, the compliance rules reference, and the platform overview</li>
      </ul>
      <p style="margin-bottom:4px; color:#e2e8f0; font-weight:600;">Only admins can:</p>
      <ul style="margin:0 0 16px 20px; line-height:1.8;">
        <li><strong style="color:#e2e8f0;">Trigger scans and enforcement</strong> &mdash; the Run Scan, Enforce HIGH, and Enforce ALL buttons on the home page</li>
        <li><strong style="color:#e2e8f0;">Download reports</strong> &mdash; the HTML and JSON compliance and hunterkiller reports</li>
        <li><strong style="color:#e2e8f0;">Manage pending destroys</strong> &mdash; Abort, Extend (+24h), Approve, Revoke, and Destroy Now actions on the Pending page (viewers see the pending items but action buttons are disabled)</li>
        <li><strong style="color:#e2e8f0;">Manage overrides</strong> &mdash; add or remove false positive / exemption overrides (viewers see existing overrides but the form is hidden)</li>
        <li><strong style="color:#e2e8f0;">Manage access</strong> &mdash; the Access Management page and invite link generation are admin-only routes</li>
        <li><strong style="color:#e2e8f0;">View unmanaged resources</strong> &mdash; the "Unmanaged Resources" section on the Services page is hidden from viewers</li>
        <li><strong style="color:#e2e8f0;">View ECR scan console links</strong> &mdash; container names on the Security page link to the AWS Console for admins; viewers see the same data as plain text</li>
      </ul>
      <p>
        Admin-only features are marked with a subtle green shield (<span style="color:#22c55e; opacity:0.6;">&#x1F6E1;</span>)
        visible only to the admin viewing the page. If you don't see shield icons, you're a viewer &mdash;
        and you already have access to everything listed in the first group above.
      </p>
    </div>

    <h2>HOW IT WORKS</h2>
    <div class="card" style="margin-bottom:16px; padding:16px; font-size:13px; color:#94a3b8;">
      <div style="display:grid; grid-template-columns:32px 1fr; gap:8px 12px; align-items:start;">
        <span style="color:#3b82f6; font-weight:700; font-size:16px; text-align:center;">1</span>
        <div><strong style="color:#e2e8f0;">Nightly Scan</strong> — A Lambda runs at 3 AM UTC. Discovers ALL resources in the AWS account via Cost Explorer, Resource Groups Tagging API, and service-specific APIs.</div>
        <span style="color:#3b82f6; font-weight:700; font-size:16px; text-align:center;">2</span>
        <div><strong style="color:#e2e8f0;">Four Compliance Checks</strong> — Tag compliance (required tags), Terraform drift detection (suspected click-ops finder), schedule compliance (idle resources), lifecycle compliance (expired resources).</div>
        <span style="color:#3b82f6; font-weight:700; font-size:16px; text-align:center;">3</span>
        <div><strong style="color:#e2e8f0;">Reports</strong> — JSON, Markdown, and styled HTML reports stored in S3. Styled HTML email sent via SES with charts, 24-hour delta, teardown commands, and cost breakdowns. The same HTML report is downloadable from the Dashboard page.</div>
        <span style="color:#3b82f6; font-weight:700; font-size:16px; text-align:center;">4</span>
        <div><strong style="color:#e2e8f0;">This Dashboard</strong> — Reads the latest S3 report and presents findings with context-aware remediation. Cross-references Terraform state to identify module locations and generate fix code. Download buttons on the Dashboard page provide the latest HTML report (with charts and delta) and raw JSON.</div>
        <span style="color:#3b82f6; font-weight:700; font-size:16px; text-align:center;">5</span>
        <div><strong style="color:#e2e8f0;">Enforcement</strong> — The Hunterkiller reads findings, emails resource owners a 24-hour warning, then tears down unresolved non-compliant resources. Overrides and escape hatches provide safety valves. Currently in <strong style="color:#a78bfa;">DRY RUN</strong> mode (logs only, no actual destruction).</div>
      </div>
    </div>

    <div style="margin-top:24px;">
      <a href="/" class="btn btn-secondary">&larr; Dashboard</a>
      <a href="/docs" class="btn btn-primary" style="margin-left:8px;">Compliance Rules &rarr;</a>
    </div>"""

    return _layout("Guide", content, user_email)


def render_docs(user_email=""):
    """Render the compliance rules documentation page."""
    env = ENVIRONMENT

    content = f"""
    <h1>COMPLIANCE RULES</h1>
    <p style="color:#94a3b8; margin-bottom:24px; font-size:13px;">
      What the scanner checks, how findings are classified, and what tags are required.
      This is the reference for the {_e(PROJECT)} AWS account compliance policy.
    </p>

    <h2>REQUIRED TAGS</h2>
    <p style="color:#94a3b8; font-size:13px; margin-bottom:12px;">
      Every AWS resource must have these five tags. Resources missing any are flagged as <strong style="color:#ef4444;">HIGH</strong> severity.
      The first four are applied automatically via the Terraform provider <code style="color:#3b82f6;">default_tags</code> block.
      Repository is also set in default_tags. Resources missing these tags are likely not managed by the active Terraform state.
    </p>
    <table style="margin-bottom:24px;">
      <thead><tr><th>Tag</th><th>Required Value</th><th>Source</th><th>Purpose</th></tr></thead>
      <tbody>
        <tr><td style="color:#3b82f6; font-weight:600;">Project</td><td>{_e(PROJECT)}</td><td>default_tags</td><td>Cost allocation group</td></tr>
        <tr><td style="color:#3b82f6; font-weight:600;">Environment</td><td>{_e(env)}</td><td>default_tags</td><td>Deployment stage</td></tr>
        <tr><td style="color:#3b82f6; font-weight:600;">ManagedBy</td><td>terraform</td><td>default_tags</td><td>Identifies IaC-managed resources</td></tr>
        <tr><td style="color:#3b82f6; font-weight:600;">Lifecycle</td><td>persistent / temporary</td><td>default_tags</td><td>Resource permanence intent</td></tr>
        <tr><td style="color:#3b82f6; font-weight:600;">Repository</td><td>https://github.com/&hellip;</td><td>default_tags</td><td>Source repo URL (https:// or git@ scheme). Scanner validates format.</td></tr>
      </tbody>
    </table>

    <h2>RECOMMENDED TAGS</h2>
    <p style="color:#94a3b8; font-size:13px; margin-bottom:12px;">
      Not required but significantly improve compliance forensics, remediation quality, and cost attribution.
      Missing these may generate <strong style="color:#22c55e;">LOW</strong> severity informational findings.
    </p>
    <table style="margin-bottom:24px;">
      <thead><tr><th>Tag</th><th>Example</th><th>Source</th><th>Purpose</th></tr></thead>
      <tbody>
        <tr><td style="color:#3b82f6;">Service</td><td>wiki, identity, compliance</td><td>per-module tags</td><td>Which logical service owns this resource</td></tr>
        <tr><td style="color:#3b82f6;">Subcomponent</td><td>database, ecs-service, cache</td><td>per-module tags</td><td>What role within the service</td></tr>
        <tr><td style="color:#3b82f6;">Purpose</td><td>Team chat and communication</td><td>per-module tags</td><td>Plain-English description for triage</td></tr>
        <tr><td style="color:#3b82f6;">Contact</td><td>admin@example.com</td><td>per-resource</td><td>Owner to notify before teardown</td></tr>
        <tr><td style="color:#3b82f6;">Schedule</td><td>always-on, business-hours</td><td>per-resource</td><td>When the resource should be running</td></tr>
        <tr><td style="color:#a78bfa;">ArchZone</td><td>edge, apps, identity, serverless</td><td>per-module tags</td><td>Architecture visualization zone. Overrides auto-detected placement in diagrams.</td></tr>
        <tr><td style="color:#a78bfa;">ArchRole</td><td>load-balancer, database, compute</td><td>per-module tags</td><td>Architecture visualization role. Controls node shape in diagrams.</td></tr>
        <tr><td style="color:#a78bfa;">ArchVisible</td><td>true, false</td><td>per-module tags</td><td>Set to false to hide a resource from architecture diagrams.</td></tr>
      </tbody>
    </table>

    <h2>FOUR SCANNER MODULES</h2>
    <div style="display:grid; gap:12px; margin-bottom:24px;">
      <div class="card" style="border-left:3px solid #ef4444; padding:12px 16px;">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
          <strong style="color:#e2e8f0; font-size:14px;">1. Tag Compliance</strong>
          {_badge("HIGH / MEDIUM", "red")}
        </div>
        <p style="color:#94a3b8; font-size:13px;">
          Checks ALL discovered resources for the five required tags (Project, Environment, ManagedBy, Lifecycle, Repository).
          Missing tags are HIGH severity. Repository must be a valid URL (https:// or git@ scheme).
          Also surfaces missing recommended tags (Service, Purpose) as informational findings.
        </p>
      </div>
      <div class="card" style="border-left:3px solid #f59e0b; padding:12px 16px;">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
          <strong style="color:#e2e8f0; font-size:14px;">2. Drift Detection</strong>
          {_badge("HIGH / MEDIUM / LOW", "amber")}
        </div>
        <p style="color:#94a3b8; font-size:13px;">
          Reads Terraform state from S3, compares against discovered resources. Flags resources that exist in AWS but
          are NOT in the active Terraform state. These are flagged as suspected click-ops, though they may
          also come from another TF repo or AWS automation.
          Click-Ops Likelihood: HIGH (likely created manually outside Terraform), MEDIUM (possibly auto-created but worth reviewing), LOW (likely AWS-managed, filtered from view).
        </p>
      </div>
      <div class="card" style="border-left:3px solid #3b82f6; padding:12px 16px;">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
          <strong style="color:#e2e8f0; font-size:14px;">3. Schedule Compliance</strong>
          {_badge("MEDIUM", "blue")}
        </div>
        <p style="color:#94a3b8; font-size:13px;">
          Flags long-running resources (EC2, ECS, RDS) that lack Schedule tags and could be stopped during off-hours.
          Resources with <code style="color:#3b82f6;">Lifecycle=persistent</code> are respected.
        </p>
      </div>
      <div class="card" style="border-left:3px solid #a78bfa; padding:12px 16px;">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
          <strong style="color:#e2e8f0; font-size:14px;">4. Lifecycle Compliance</strong>
          {_badge("HIGH / MEDIUM", "purple")}
        </div>
        <p style="color:#94a3b8; font-size:13px;">
          Flags temporary resources past their <code style="color:#3b82f6;">ExpiresAt</code> date, and resources
          missing a Lifecycle tag entirely. Ensures nothing lives forever without intent.
        </p>
      </div>
    </div>

    <h2>DRIFT DETECTION: FALSE POSITIVE CONTROLS</h2>
    <p style="color:#94a3b8; font-size:13px; margin-bottom:12px;">
      Three layers prevent noise from auto-created AWS resources:
    </p>
    <table style="margin-bottom:24px;">
      <thead><tr><th>Layer</th><th>What</th><th>Examples</th></tr></thead>
      <tbody>
        <tr>
          <td style="color:#e2e8f0;">Hardcoded</td>
          <td style="color:#94a3b8;">Auto-created resources excluded by regex pattern</td>
          <td style="color:#6b7280; font-size:12px;">ENIs from ECS/Lambda/NAT, service-linked IAM roles, default VPC resources, ECS task definitions</td>
        </tr>
        <tr>
          <td style="color:#e2e8f0;">Pattern-based</td>
          <td style="color:#94a3b8;">Configurable regex patterns in Terraform variables</td>
          <td style="color:#6b7280; font-size:12px;">ARN prefix patterns for known auto-created resources</td>
        </tr>
        <tr>
          <td style="color:#e2e8f0;">Explicit</td>
          <td style="color:#94a3b8;">Specific ARNs excluded via Terraform variable or Overrides page</td>
          <td style="color:#6b7280; font-size:12px;">TF state bucket, DynamoDB lock table, bootstrap resources</td>
        </tr>
      </tbody>
    </table>

    <h2>ENFORCEMENT LIFECYCLE</h2>
    <div class="card" style="margin-bottom:24px; padding:16px; font-size:13px; color:#94a3b8;">
      <div style="display:grid; grid-template-columns:32px 1fr; gap:8px 12px; align-items:start;">
        <span style="color:#3b82f6; font-weight:700; font-size:16px; text-align:center;">1</span>
        <div><strong style="color:#e2e8f0;">Scanner finds non-compliant resource</strong> — Nightly scan discovers a resource violating compliance rules.</div>
        <span style="color:#f59e0b; font-weight:700; font-size:16px; text-align:center;">2</span>
        <div><strong style="color:#e2e8f0;">Hunterkiller issues warning</strong> — Resource owner (Contact tag or fallback) receives email notification. 24-hour grace period starts.</div>
        <span style="color:#f59e0b; font-weight:700; font-size:16px; text-align:center;">3</span>
        <div><strong style="color:#e2e8f0;">Grace period</strong> — Owner can fix the issue, or an admin can Abort/Extend via the Pending page.</div>
        <span style="color:#ef4444; font-weight:700; font-size:16px; text-align:center;">4</span>
        <div><strong style="color:#e2e8f0;">Teardown</strong> — If unresolved after grace period and no override, the resource is destroyed. Currently in <strong style="color:#a78bfa;">DRY RUN</strong> mode (logged but not executed).</div>
      </div>
      <div class="alert alert-info" style="margin-top:12px;">
        <strong>Safety valves:</strong> Overrides (false positive / exempt), Abort button, +24h extension, and DRY RUN mode all prevent accidental destruction.
      </div>
    </div>

    <h2>WHY RECOMMENDED TAGS MATTER</h2>
    <div style="display:grid; gap:12px; margin-bottom:24px;">
      <div class="card" style="padding:12px 16px;">
        <strong style="color:#e2e8f0;">Orphaned Resource Triage</strong>
        <p style="color:#94a3b8; font-size:13px; margin-top:4px;">
          When an EBS volume or security group appears without a Terraform parent, <code style="color:#3b82f6;">Service</code> and <code style="color:#3b82f6;">Purpose</code>
          tell an operator what it belonged to and whether deletion is safe.
        </p>
      </div>
      <div class="card" style="padding:12px 16px;">
        <strong style="color:#e2e8f0;">Remediation Quality</strong>
        <p style="color:#94a3b8; font-size:13px; margin-top:4px;">
          The CURE page uses these tags to generate context-aware CLI commands and terraform import templates.
          With <code style="color:#3b82f6;">Service=wiki</code>, it can point to the module that defines the wiki.
          Without it, only generic advice is possible.
        </p>
      </div>
      <div class="card" style="padding:12px 16px;">
        <strong style="color:#e2e8f0;">Volume Tracing</strong>
        <p style="color:#94a3b8; font-size:13px; margin-top:4px;">
          Root EBS volumes inherit their parent EC2 instance's tags. <code style="color:#3b82f6;">Service</code> on the parent enables
          automatic tracing from a flagged volume back to its Terraform module.
        </p>
      </div>
      <div class="card" style="padding:12px 16px;">
        <strong style="color:#e2e8f0;">Cost Attribution</strong>
        <p style="color:#94a3b8; font-size:13px; margin-top:4px;">
          <code style="color:#3b82f6;">Service</code> enables per-service cost breakdowns — wiki costs vs identity costs vs compliance costs.
        </p>
      </div>
      <div class="card" style="padding:12px 16px;">
        <strong style="color:#e2e8f0;">Architecture Visualization</strong>
        <p style="color:#94a3b8; font-size:13px; margin-top:4px;">
          <code style="color:#a78bfa;">ArchZone</code>, <code style="color:#a78bfa;">ArchRole</code>, and <code style="color:#a78bfa;">ArchVisible</code>
          control how resources appear in auto-generated architecture diagrams (Architecture page).
          ArchZone overrides the auto-detected zone placement; ArchRole controls node shape; ArchVisible=false hides a resource entirely.
        </p>
      </div>
    </div>

    <h2>TAG VALUE RESTRICTIONS</h2>
    <p style="color:#94a3b8; font-size:13px; margin-bottom:12px;">
      AWS tag values only allow: letters, numbers, spaces, and <code style="color:#6b7280;">_ . : / = + - @</code>
    </p>
    <p style="color:#94a3b8; font-size:13px; margin-bottom:24px;">
      <strong style="color:#ef4444;">Not allowed:</strong> commas, parentheses, angle brackets, quotes, semicolons, or other special characters.
    </p>

    <div style="margin-top:24px;">
      <a href="/guide" class="btn btn-secondary">&larr; Guide</a>
      <a href="/" class="btn btn-secondary" style="margin-left:8px;">&larr; Dashboard</a>
    </div>"""

    return _layout("Docs", content, user_email)

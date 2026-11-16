"""
Cost burn analysis page — spend breakdown by service, resource type, and region.

Three data sources:
  - "Estimated" = per-resource predictions from instance-type lookup tables.
    Only covers resources the scanner can attribute (EC2, RDS, ECS, etc.).
  - "Billed by Region" = actual AWS spend from Cost Explorer grouped by region.
  - "Billed by Service" = actual AWS spend from Cost Explorer grouped by AWS
    service name (EC2, S3, CloudWatch, etc.). Enables reconciliation.
"""

from datetime import UTC, datetime

import site_config
from compliance_schema import INACTIVE_STATES, PRICE_TABLE_REGION, RTYPE_TO_CE_SERVICE
from views_base import _e, _layout

# EC2 instance type costs in PRICE_TABLE_REGION — for estimating "when-running" cost
# of stopped/destroyed instances.  Mirrors scanner's _EC2_MONTHLY_COST.
_EC2_MONTHLY_COST = {
    "t3.micro": 10.51,
    "t3.small": 21.02,
    "t3.medium": 42.05,
    "t3.large": 84.10,
    "t3.xlarge": 168.19,
    "t3.2xlarge": 336.38,
    "t3a.micro": 9.49,
    "t3a.small": 18.98,
    "t3a.medium": 37.96,
    "t3a.large": 75.92,
    "t2.micro": 12.26,
    "t2.small": 24.53,
    "t2.medium": 49.06,
    "t2.large": 98.11,
    "m5.large": 100.37,
    "m5.xlarge": 200.75,
    "m5.2xlarge": 401.50,
    "m6i.large": 100.37,
    "m6i.xlarge": 200.75,
    "r5.large": 131.40,
    "r5.xlarge": 262.80,
    "c5.large": 88.32,
    "c5.xlarge": 176.66,
}
_RDS_MONTHLY_COST = {
    "db.t3.micro": 13.14,
    "db.t3.small": 26.28,
    "db.t3.medium": 52.56,
    "db.t4g.micro": 11.68,
    "db.t4g.small": 23.36,
    "db.t4g.medium": 46.72,
    "db.r5.large": 175.20,
    "db.r5.xlarge": 350.40,
    "db.r6g.large": 157.68,
    "db.r6g.xlarge": 315.36,
    "db.m5.large": 136.51,
    "db.m5.xlarge": 273.02,
}

# EBS gp3 pricing per GB/month in PRICE_TABLE_REGION
_EBS_COST_PER_GB = 0.0952

# Fixed monthly costs that Cost Explorer does not report (a support plan, for
# example), supplied by the deployment (site.json): rows of (name, amount, note).
_FIXED_COSTS = [(str(name), float(amount), str(note)) for name, amount, note in site_config.get("fixed_costs")]
_FIXED_COST_TOTAL = sum(c for _, c, _ in _FIXED_COSTS)


def _safe_float(val, default=0.0):
    """Safely convert to float."""
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _when_running_cost(r):
    """Estimate what a resource costs when running, ignoring stopped/terminated state.

    The scanner sets estimated_monthly_cost=0 for stopped EC2 instances, but they
    were incurring real charges earlier this month. This function looks up the
    instance type to estimate what was being charged.
    """
    cost = _safe_float(r.get("estimated_monthly_cost"))
    if cost > 0:
        return cost
    if r.get("region") != PRICE_TABLE_REGION:
        return cost  # the tables below hold no rate for this region
    rtype = r.get("resource_type", "")
    if rtype == "ec2:instance":
        itype = r.get("instance_type", "")
        return _EC2_MONTHLY_COST.get(itype, 30.0)
    if rtype in ("rds:instance", "rds:db"):
        iclass = r.get("instance_class", "")
        base = _RDS_MONTHLY_COST.get(iclass, 25.0)
        return base * 2 if r.get("multi_az") else base
    if rtype in ("ecs:service",):
        return 30.0  # reasonable default for Fargate service
    if rtype in ("ec2:natgateway",):
        return 32.40
    if rtype in ("elasticloadbalancing:loadbalancer",):
        return 16.20
    return cost


def _stopped_cost(r, ebs_by_instance=None):
    """Estimate cost while a resource is stopped.

    Stopped EC2 instances don't incur compute charges but still pay for
    attached EBS volumes and Elastic IPs.  This returns the storage-only
    cost so the display doesn't mislead users into thinking stopped
    instances cost as much as running ones.

    If ebs_by_instance is provided (dict of instance_id -> total EBS GB),
    use actual attached volume sizes; otherwise fall back to 30 GB.
    """
    rtype = r.get("resource_type", "")
    if rtype == "ec2:instance" and r.get("region") == PRICE_TABLE_REGION:
        inst_id = ""
        arn = r.get("arn") or ""
        if "/" in arn:
            inst_id = arn.split("/")[-1]
        ebs_gb = 30.0  # conservative default
        if ebs_by_instance and inst_id in ebs_by_instance:
            ebs_gb = ebs_by_instance[inst_id]
        return ebs_gb * _EBS_COST_PER_GB
    if rtype in ("rds:instance", "rds:db"):
        return _when_running_cost(r) * 0.2
    return _safe_float(r.get("estimated_monthly_cost"))


def _fmt(val):
    """Format a dollar amount."""
    if val is None or val == 0:
        return "$0"
    if val < 1:
        return f"${val:.2f}"
    if val < 10:
        return f"${val:.1f}"
    return f"${val:,.0f}"


def _pct(part, whole):
    """Format a percentage, or return empty string if whole is 0."""
    if not whole:
        return ""
    return f"{(part / whole) * 100:.0f}%"


def _aggregate(report):
    """Compute all spend aggregations from the compliance report."""
    discovered = report.get("discovered_resources", [])
    region_costs = report.get("region_costs", {})
    service_costs = report.get("service_costs", {})

    # Build non-compliant ARN set (deduplicated).
    # Exclude INFO findings — they are informational (e.g. "expires soon")
    # and should not count as non-compliant.  The summary dashboard and
    # findings page already exclude INFO; the burn page must match.
    nc_arns = set()
    for cat in ("drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"):
        for f in report.get(cat, {}).get("findings", []):
            if f.get("severity") == "INFO":
                continue
            arn = f.get("resource_arn", "")
            if arn:
                nc_arns.add(arn)

    # Per-resource aggregation
    total_estimated = 0.0
    nc_spend = 0.0
    untagged_spend = 0.0
    nc_untagged_spend = 0.0
    stopped_resources = []
    by_service = {}
    by_type = {}
    by_region = {}

    for r in discovered:
        spend = _safe_float(r.get("estimated_monthly_cost"))
        total_estimated += spend
        rtype = r.get("resource_type", "unknown")
        region = r.get("region", "global")
        tags = r.get("tags") or {}
        svc = tags.get("Service", "") or ""
        arn = r.get("arn", "")
        state = (r.get("state") or "").lower()
        is_inactive = state in INACTIVE_STATES
        is_nc = arn in nc_arns and not is_inactive

        if state in ("stopped", "terminated", "deleted", "deleting"):
            stopped_resources.append(r)

        # By service
        svc_key = svc if svc else ""
        if svc_key not in by_service:
            by_service[svc_key] = {"spend": 0, "count": 0, "nc_count": 0, "nc_spend": 0}
        by_service[svc_key]["spend"] += spend
        by_service[svc_key]["count"] += 1
        if is_nc:
            by_service[svc_key]["nc_count"] += 1
            by_service[svc_key]["nc_spend"] += spend

        if not svc:
            untagged_spend += spend
            if is_nc:
                nc_untagged_spend += spend

        # By type
        if rtype not in by_type:
            by_type[rtype] = {"spend": 0, "count": 0, "nc_count": 0, "nc_spend": 0}
        by_type[rtype]["spend"] += spend
        by_type[rtype]["count"] += 1
        if is_nc:
            by_type[rtype]["nc_count"] += 1
            by_type[rtype]["nc_spend"] += spend

        # By region
        if region not in by_region:
            by_region[region] = {"estimated": 0, "nc_spend": 0, "count": 0}
        by_region[region]["estimated"] += spend
        by_region[region]["count"] += 1
        if is_nc:
            by_region[region]["nc_spend"] += spend
            nc_spend += spend

    total_billed = sum(region_costs.values())

    return {
        "total_estimated": total_estimated + _FIXED_COST_TOTAL,
        "total_billed": total_billed,
        "nc_spend": nc_spend,
        "untagged_spend": untagged_spend,
        "nc_untagged_spend": nc_untagged_spend,
        "resource_count": len(discovered),
        "by_service": by_service,
        "by_type": by_type,
        "by_region": by_region,
        "region_costs": region_costs,
        "service_costs": service_costs,
        "stopped_resources": stopped_resources,
        "fixed_costs": _FIXED_COST_TOTAL,
    }


def _render_summary(agg, forecast=None, month_spend=None):
    """Render the three-source summary with 2x2 compliance/attribution breakdown."""
    est = agg["total_estimated"]
    nc = agg["nc_spend"]
    untagged = agg["untagged_spend"]
    nc_untagged = agg["nc_untagged_spend"]

    # Use live current-month spend if available, fall back to scanner's trailing 30-day
    if month_spend and month_spend.get("total") is not None:
        billed = month_spend["total"]
        billed_label = month_spend.get("month_label", "this month")
        billed_days = month_spend.get("days_elapsed", 0)
        billed_subtitle = (
            f"{_e(billed_label)} month-to-date ({_e(str(billed_days))} days) &middot; live from Cost Explorer"
        )
    else:
        billed = agg["total_billed"]
        billed_label = ""
        billed_subtitle = "trailing 30 days &middot; from scanner report"

    coverage_pct = _pct(est, billed) if billed else "N/A"

    # Four quadrants (compliance x attribution)
    comp_attr = est - nc - untagged + nc_untagged  # compliant + has Service tag
    comp_unattr = untagged - nc_untagged  # compliant + missing Service tag
    nc_attr = nc - nc_untagged  # non-compliant + has Service tag
    nc_unattr = nc_untagged  # non-compliant + missing Service tag

    # AWS forecast card
    if forecast and forecast.get("forecast_total"):
        fc_amount = forecast["forecast_total"]
        forecast_card = f"""
      <!-- Middle: AWS Forecast -->
      <div class="card" style="border-left:3px solid #a78bfa;">
        <div class="label">AWS Forecast (Full Month)</div>
        <div class="value" style="color:#a78bfa;">{_fmt(fc_amount)}</div>
        <div style="font-size:11px; color:#6b7280; margin-top:4px;">
          full month total &middot; from Cost Explorer forecast
        </div>
        <div style="font-size:12px; color:#94a3b8; margin-top:12px; padding-top:8px; border-top:1px solid #1e1e2e;">
          <strong>Billed MTD + projected remainder.</strong>
          Includes charges already incurred (stopped/destroyed resources) plus
          AWS&#39;s prediction for the rest of the month. Not directly comparable
          to Estimated, which is a forward-looking rate for live resources only.
        </div>
      </div>"""
    else:
        forecast_card = """
      <!-- Middle: AWS Forecast unavailable -->
      <div class="card" style="border-left:3px solid #4b5563;">
        <div class="label">AWS Forecast (Full Month)</div>
        <div class="value" style="color:#4b5563;">N/A</div>
        <div style="font-size:11px; color:#4b5563; margin-top:4px;">
          requires Cost Explorer access
        </div>
        <div style="font-size:12px; color:#4b5563; margin-top:12px; padding-top:8px; border-top:1px solid #1e1e2e;">
          AWS Cost Explorer forecast unavailable. May require sufficient
          billing history or Lambda IAM permissions.
        </div>
      </div>"""

    return f"""
    <div class="summary-cards" style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; margin-bottom:24px;">

      <!-- Left: Billed (month-to-date) -->
      <div class="card" style="border-left:3px solid #e2e8f0;">
        <div class="label">Billed (Month-to-Date)</div>
        <div class="value" style="color:#e2e8f0;">{_fmt(billed)}</div>
        <div style="font-size:11px; color:#6b7280; margin-top:4px;">
          {billed_subtitle}
        </div>
        <div style="font-size:12px; color:#94a3b8; margin-top:12px; padding-top:8px; border-top:1px solid #1e1e2e;">
          Actual charges so far this calendar month. Covers <strong>all</strong>
          AWS services including stopped/destroyed resources that ran earlier
          this month.
        </div>
      </div>

      {forecast_card}

      <!-- Right: Estimated (per-resource model, partial) -->
      <div class="card" style="border-left:3px solid #3b82f6;">
        <div class="label">Estimated (Per-Resource, Partial)</div>
        <div class="value" style="color:#3b82f6;">{_fmt(est)}<span style="font-size:14px; color:#4b5563;">/mo</span></div>
        <div style="font-size:11px; color:#6b7280; margin-top:4px;">
          {agg["resource_count"]} discovered resources &middot;
          covers {coverage_pct} of billed
        </div>
        <div style="font-size:12px; color:#94a3b8; margin-top:12px; padding-top:8px; border-top:1px solid #1e1e2e;">
          A <strong>per-resource model</strong> (instance rates &times; 730h), useful for
          attribution &mdash; which resources cost what. <strong>Not the bill:</strong> it
          excludes data transfer, NAT/gateway processing, Support, and usage-based
          charges. For the real number use <strong>Billed</strong> (actual) or
          <strong>Forecast</strong> (projected).
        </div>
      </div>
    </div>

    <!-- 2x2+total breakdown: compliance (rows) x attribution (columns) -->
    <div style="margin-bottom:4px; font-size:11px; color:#4b5563; text-transform:uppercase; letter-spacing:0.1em;">
      Breakdown of Estimated Spend
    </div>
    <div class="breakdown-grid" style="display:grid; grid-template-columns:auto 1fr 1fr 1fr; gap:0; margin-bottom:16px; border:1px solid #1e1e2e; border-radius:8px; overflow:hidden;">
      <!-- Header row -->
      <div style="padding:8px 12px; background:#0a0a0f;"></div>
      <div style="padding:8px 12px; background:#0a0a0f; text-align:center; font-size:11px; color:#6b7280; text-transform:uppercase; letter-spacing:0.1em; border-left:1px solid #1e1e2e;">
        Attributed <span style="color:#4b5563;">(has Service tag)</span>
      </div>
      <div style="padding:8px 12px; background:#0a0a0f; text-align:center; font-size:11px; color:#f59e0b; text-transform:uppercase; letter-spacing:0.1em; border-left:1px solid #1e1e2e;">
        Unattributed <span style="color:#4b5563;">(no Service tag)</span>
      </div>
      <div style="padding:8px 12px; background:#0a0a0f; text-align:center; font-size:11px; color:#e2e8f0; text-transform:uppercase; letter-spacing:0.1em; border-left:1px solid #1e1e2e;">
        Total
      </div>
      <!-- Compliant row -->
      <div style="padding:12px; background:#0f0f17; font-size:11px; color:#22c55e; text-transform:uppercase; letter-spacing:0.1em; display:flex; align-items:center; border-top:1px solid #1e1e2e;">
        Compliant
      </div>
      <div style="padding:12px 16px; background:#0f0f17; text-align:center; border-left:1px solid #1e1e2e; border-top:1px solid #1e1e2e;">
        <div style="font-size:20px; font-weight:700; color:#22c55e;">{_fmt(comp_attr)}</div>
        <div style="font-size:11px; color:#4b5563;">{_pct(comp_attr, est)} of estimated</div>
      </div>
      <div style="padding:12px 16px; background:#0f0f17; text-align:center; border-left:1px solid #1e1e2e; border-top:1px solid #1e1e2e;">
        <div style="font-size:20px; font-weight:700; color:#a3e635;">{_fmt(comp_unattr)}</div>
        <div style="font-size:11px; color:#4b5563;">{_pct(comp_unattr, est)} of estimated</div>
      </div>
      <div style="padding:12px 16px; background:#0f0f17; text-align:center; border-left:1px solid #1e1e2e; border-top:1px solid #1e1e2e;">
        <div style="font-size:20px; font-weight:700; color:#22c55e;">{_fmt(comp_attr + comp_unattr)}</div>
        <div style="font-size:11px; color:#4b5563;">{_pct(comp_attr + comp_unattr, est)} of estimated</div>
      </div>
      <!-- Non-compliant row -->
      <div style="padding:12px; background:#0f0f17; font-size:11px; color:#ef4444; text-transform:uppercase; letter-spacing:0.1em; display:flex; align-items:center; border-top:1px solid #1e1e2e;">
        Non-Compliant
      </div>
      <div style="padding:12px 16px; background:#0f0f17; text-align:center; border-left:1px solid #1e1e2e; border-top:1px solid #1e1e2e;">
        <div style="font-size:20px; font-weight:700; color:#ef4444;">{_fmt(nc_attr)}</div>
        <div style="font-size:11px; color:#4b5563;">{_pct(nc_attr, est)} of estimated</div>
      </div>
      <div style="padding:12px 16px; background:#0f0f17; text-align:center; border-left:1px solid #1e1e2e; border-top:1px solid #1e1e2e;">
        <div style="font-size:20px; font-weight:700; color:#f97316;">{_fmt(nc_unattr)}</div>
        <div style="font-size:11px; color:#4b5563;">{_pct(nc_unattr, est)} of estimated</div>
      </div>
      <div style="padding:12px 16px; background:#0f0f17; text-align:center; border-left:1px solid #1e1e2e; border-top:1px solid #1e1e2e;">
        <div style="font-size:20px; font-weight:700; color:#ef4444;">{_fmt(nc_attr + nc_unattr)}</div>
        <div style="font-size:11px; color:#4b5563;">{_pct(nc_attr + nc_unattr, est)} of estimated</div>
      </div>
      <!-- Totals row -->
      <div style="padding:12px; background:#0a0a0f; font-size:11px; color:#e2e8f0; text-transform:uppercase; letter-spacing:0.1em; display:flex; align-items:center; border-top:2px solid #2a2a3a; font-weight:700;">
        Total
      </div>
      <div style="padding:12px 16px; background:#0a0a0f; text-align:center; border-left:1px solid #1e1e2e; border-top:2px solid #2a2a3a;">
        <div style="font-size:20px; font-weight:700; color:#e2e8f0;">{_fmt(comp_attr + nc_attr)}</div>
        <div style="font-size:11px; color:#4b5563;">{_pct(comp_attr + nc_attr, est)} of estimated</div>
      </div>
      <div style="padding:12px 16px; background:#0a0a0f; text-align:center; border-left:1px solid #1e1e2e; border-top:2px solid #2a2a3a;">
        <div style="font-size:20px; font-weight:700; color:#f59e0b;">{_fmt(comp_unattr + nc_unattr)}</div>
        <div style="font-size:11px; color:#4b5563;">{_pct(comp_unattr + nc_unattr, est)} of estimated</div>
      </div>
      <div style="padding:12px 16px; background:#0a0a0f; text-align:center; border-left:1px solid #1e1e2e; border-top:2px solid #2a2a3a;">
        <div style="font-size:20px; font-weight:700; color:#3b82f6;">{_fmt(est)}</div>
        <div style="font-size:11px; color:#4b5563;">100%</div>
      </div>
    </div>"""


def _render_service_bars(by_service):
    """Render horizontal bar chart grouped by Service tag."""
    if not by_service:
        return '<div class="empty">No resource data available.</div>'

    items = []
    untagged = None
    for svc, data in by_service.items():
        if not svc:
            untagged = ("untagged", data)
        else:
            items.append((svc, data))
    items.sort(key=lambda x: -x[1]["spend"])
    if untagged:
        items.append(untagged)

    max_spend = max((d["spend"] for _, d in items), default=1) or 1

    rows = []
    for svc, data in items:
        spend = data["spend"]
        nc_spend = data["nc_spend"]
        bar_pct = (spend / max_spend) * 100 if max_spend > 0 else 0
        nc_pct = (nc_spend / spend) * 100 if spend > 0 else 0
        comp_pct = bar_pct - (nc_pct * bar_pct / 100)
        nc_bar_pct = bar_pct - comp_pct

        svc_color = "#f59e0b" if svc == "untagged" else "#e2e8f0"
        count_label = f"{data['count']} resources"
        if data["nc_count"] > 0:
            count_label += f" ({data['nc_count']} non-compliant)"

        rows.append(f"""
      <div class="burn-row">
        <div class="burn-label">
          <span style="color:{svc_color}; font-weight:600;">{_e(svc)}</span>
          <span style="color:#4b5563; font-size:11px;">{count_label}</span>
        </div>
        <div class="burn-bar-container">
          <div class="burn-bar-comp" style="width:{comp_pct:.1f}%;"></div>
          <div class="burn-bar-nc" style="width:{nc_bar_pct:.1f}%;"></div>
        </div>
        <div class="burn-amount">{_fmt(spend)}/mo</div>
      </div>""")

    return f"""
    <div style="margin-bottom:8px; display:flex; gap:16px; font-size:11px;">
      <span><span style="display:inline-block; width:10px; height:10px; background:rgba(59,130,246,0.4); border-radius:2px;"></span> Compliant</span>
      <span><span style="display:inline-block; width:10px; height:10px; background:rgba(239,68,68,0.6); border-radius:2px;"></span> Non-Compliant</span>
    </div>
    {"".join(rows)}"""


def _render_type_table(by_type):
    """Render top resource types by estimated spend."""
    items = sorted(by_type.items(), key=lambda x: -x[1]["spend"])[:15]
    if not items:
        return '<div class="empty">No resource data available.</div>'

    rows = []
    for rtype, data in items:
        nc_style = ' style="color:#ef4444;"' if data["nc_spend"] > 0 else ""
        rows.append(f"""
        <tr>
          <td style="font-weight:600;">{_e(rtype)}</td>
          <td style="color:#3b82f6;">{_fmt(data["spend"])}/mo</td>
          <td>{data["count"]}</td>
          <td{nc_style}>{_fmt(data["nc_spend"])}/mo</td>
        </tr>""")

    return f"""
    <table>
      <thead><tr>
        <th>Resource Type</th><th>Estimated Spend</th>
        <th>Resources</th><th>Non-Compliant Spend</th>
      </tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""


def _render_region_table(by_region, region_costs):
    """Render spend breakdown by region with billed vs estimated."""
    all_regions = set(by_region.keys()) | set(region_costs.keys())
    rows_data = []
    for region in all_regions:
        billed = region_costs.get(region, 0)
        est = by_region.get(region, {}).get("estimated", 0)
        nc = by_region.get(region, {}).get("nc_spend", 0)
        count = by_region.get(region, {}).get("count", 0)
        rows_data.append((region, billed, est, nc, count))

    rows_data.sort(key=lambda x: -x[1])

    if not rows_data:
        return '<div class="empty">No region data available.</div>'

    rows = []
    for region, billed, est, nc, count in rows_data:
        nc_style = ' style="color:#ef4444;"' if nc > 0 else ""
        rows.append(f"""
        <tr>
          <td style="font-weight:600;">{_e(region)}</td>
          <td>{_fmt(billed)}</td>
          <td style="color:#3b82f6;">{_fmt(est)}/mo</td>
          <td{nc_style}>{_fmt(nc)}/mo</td>
          <td>{count}</td>
        </tr>""")

    return f"""
    <table>
      <thead><tr>
        <th>Region</th><th>&#9664; Billed (Past 30 Days)</th><th>Estimated (Forward Rate) &#9654;</th>
        <th>Non-Compliant</th><th>Resources</th>
      </tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""


# Map scanner resource_type to Cost Explorer service names.
# Uses ACTUAL resource_type keys from the scanner (not assumed ones).
# Resource type → CE service mapping imported from compliance_schema.RTYPE_TO_CE_SERVICE


def _render_billed_services(service_costs, by_type):
    """Render billed spend by AWS service with estimated comparison."""
    if not service_costs:
        return """<div class="empty">No per-service billing data available.
        Re-run the scanner to populate this data.</div>"""

    # Build estimated spend per CE service name from resource types
    est_by_ce = {}
    for rtype, data in by_type.items():
        ce_svc = RTYPE_TO_CE_SERVICE.get(rtype)
        # Some scanner types use resource names as suffixes (s3:bucket-name, sns:topic-name)
        if not ce_svc:
            if rtype.startswith("s3:"):
                ce_svc = "Amazon Simple Storage Service"
            elif rtype.startswith("sns:"):
                ce_svc = "Amazon Simple Notification Service"
            elif rtype.startswith("sqs:"):
                ce_svc = "Amazon Simple Queue Service"
            elif rtype.startswith("apigateway:"):
                ce_svc = "Amazon API Gateway"
        if ce_svc:
            est_by_ce[ce_svc] = est_by_ce.get(ce_svc, 0) + data["spend"]

    # Merge billed and estimated
    all_services = set(service_costs.keys()) | set(est_by_ce.keys())
    rows_data = []
    for svc in all_services:
        billed = service_costs.get(svc, 0)
        estimated = est_by_ce.get(svc, 0)
        rows_data.append((svc, billed, estimated))

    rows_data.sort(key=lambda x: -x[1])

    if not rows_data:
        return '<div class="empty">No service spend data available.</div>'

    # Split into main (>$0.50 billed or estimated) and low-spend
    main_rows_data = [(s, b, e) for s, b, e in rows_data if b > 0.50 or e > 0.50]
    low_rows_data = [(s, b, e) for s, b, e in rows_data if b <= 0.50 and e <= 0.50]

    total_billed = sum(b for _, b, _ in rows_data)
    total_est = sum(e for _, _, e in rows_data)

    def _build_svc_rows(data):
        rows = []
        for svc, billed, estimated in data:
            short = _shorten_svc(svc)
            has_est = estimated > 0
            est_display = (
                f'<span style="color:#3b82f6;">{_fmt(estimated)}</span>'
                if has_est
                else (
                    '<span class="hint" style="color:#4b5563; font-size:10px;" '
                    'data-tip="The scanner has no pricing model for this service. '
                    "It's usage-based (per-request, per-event, or per-token) so "
                    "cost can't be derived from resource count alone. "
                    'Check the Billed column for actual charges."'
                    ">no model</span>"
                )
            )
            rows.append(f"""
        <tr>
          <td style="font-weight:600;">{_e(short)}</td>
          <td>{_fmt(billed)}</td>
          <td>{est_display}</td>
        </tr>""")
        return rows

    rows = _build_svc_rows(main_rows_data)

    # Totals row
    rows.append(f"""
        <tr style="border-top:2px solid #2a2a3a; font-weight:700;">
          <td>Total (all services)</td>
          <td>{_fmt(total_billed)}</td>
          <td style="color:#3b82f6;">{_fmt(total_est)}</td>
        </tr>""")

    # Collapsible low-spend section
    low_section = ""
    if low_rows_data:
        low_rows = _build_svc_rows(low_rows_data)
        low_total_b = sum(b for _, b, _ in low_rows_data)
        low_total_e = sum(e for _, _, e in low_rows_data)
        low_label = f"{len(low_rows_data)} service{'s' if len(low_rows_data) != 1 else ''} under $0.50"
        low_section = f"""
    <details style="margin-top:8px;">
      <summary style="font-size:11px; color:#6b7280; cursor:pointer;">
        {_e(low_label)} &mdash; {_fmt(low_total_b)} billed, {_fmt(low_total_e)} estimated
      </summary>
      <table style="margin-top:6px;">
        <thead><tr>
          <th>AWS Service</th><th>&#9664; Billed</th><th>Estimated &#9654;</th>
        </tr></thead>
        <tbody>{"".join(low_rows)}</tbody>
      </table>
    </details>"""

    return f"""
    <p style="color:#6b7280; font-size:12px; margin-bottom:12px;">
      These two columns measure <strong>different things over different time horizons</strong>
      and should not be compared directly.
      <strong>&#9664; Billed</strong> is backward-looking: real AWS charges from Cost Explorer
      over a trailing 30-day window, including charges from resources since stopped or destroyed.
      <strong>Estimated &#9654;</strong> is forward-looking: what currently live resources would
      cost per month based on instance types and service pricing &mdash; it excludes destroyed
      resources and usage-based charges (data transfer, log ingestion, API calls).
      &ldquo;no model&rdquo; means the scanner has no pricing model for that service.
    </p>
    <table>
      <thead><tr>
        <th>AWS Service</th><th>&#9664; Billed (Past 30 Days)</th><th>Estimated (Forward Rate) &#9654;</th>
      </tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
    {low_section}"""


def _shorten_svc(name):
    """Shorten verbose Cost Explorer service names."""
    replacements = {
        "Amazon Elastic Compute Cloud - Compute": "EC2 Compute",
        "Amazon Relational Database Service": "RDS",
        "Amazon Elastic Container Service": "ECS",
        "Amazon Simple Storage Service": "S3",
        "Amazon Simple Notification Service": "SNS",
        "Amazon Simple Queue Service": "SQS",
        "Amazon EC2 Container Registry (ECR)": "ECR",
        "Amazon Elastic File System": "EFS",
        "AWS Key Management Service": "KMS",
        "AWS Secrets Manager": "Secrets Manager",
        "AWS Certificate Manager": "ACM",
        "Amazon API Gateway": "API Gateway",
        "AWS Identity and Access Management": "IAM",
        "AWS Systems Manager": "Systems Manager",
        "Amazon Virtual Private Cloud": "VPC",
        "Amazon CloudFront": "CloudFront",
        "Amazon DynamoDB": "DynamoDB",
        "Amazon ElastiCache": "ElastiCache",
        "Amazon Route 53": "Route 53",
        "AmazonCloudWatch": "CloudWatch",
        "AWS Lambda": "Lambda",
        "AWS CloudTrail": "CloudTrail",
        "Amazon GuardDuty": "GuardDuty",
        "Elastic Load Balancing": "ELB",
        "Amazon Elastic Load Balancing": "ELB",
        "Amazon Elastic Container Service for Kubernetes": "EKS",
        "Amazon Registrar": "Registrar",
        "Amazon DocumentDB (with MongoDB compatibility)": "DocumentDB",
        "EC2 - Other": "EC2 Other (EBS/EIP)",
        "AWS Cost Explorer": "Cost Explorer",
        "AWS WAF": "WAF",
        "Amazon SES": "SES",
        "AWS Config": "Config",
        "Tax": "Tax",
    }
    return replacements.get(name, name)


def _find_destroyed_resources(current_report, previous_report):
    """Find resources in previous report that are gone from current report.

    Enriches each destroyed resource with termination_date from CloudTrail
    data (if available in current report's termination_events).
    """
    if not previous_report:
        return []
    current_arns = {r.get("arn") for r in current_report.get("discovered_resources", [])}

    # Build ARN → termination date lookup from CloudTrail events
    term_dates = {}
    for evt in current_report.get("termination_events", []):
        arn = evt.get("arn", "")
        if arn:
            term_dates[arn] = evt.get("event_time", "")

    destroyed = []
    for r in previous_report.get("discovered_resources", []):
        arn = r.get("arn", "")
        if arn and arn not in current_arns:
            r = dict(r)  # don't mutate the cached baseline
            if arn in term_dates:
                r["terminated_at"] = term_dates[arn]
            destroyed.append(r)
    return destroyed


def _render_inactive_resources(stopped, destroyed, month_spend=None, all_resources=None):
    """Render section explaining billed MTD spend from inactive/destroyed resources.

    Framing: these resources already incurred charges this month. This explains
    part of the gap between Billed (MTD actuals) and Estimated (live resources only).
    """
    if not stopped and not destroyed:
        return """<div style="color:#6b7280; font-size:12px; padding:12px 0;">
        No stopped or recently destroyed resources detected. The gap between
        Billed and Estimated is primarily from usage-based charges (data transfer,
        CloudWatch, S3 operations) that aren&#39;t tied to individual resources.</div>"""

    # Build EBS-by-instance lookup from discovered resources so we can
    # show accurate storage costs for stopped instances
    ebs_by_instance = {}
    for r in all_resources or []:
        if r.get("resource_type") == "ec2:volume":
            inst_id = r.get("attached_to", "")
            if inst_id:
                ebs_by_instance[inst_id] = ebs_by_instance.get(inst_id, 0) + _safe_float(r.get("size_gb"))

    # Approximate days elapsed this month for proportional cost
    days_in_month = 30  # approximate
    days_elapsed = 19  # default
    if month_spend:
        days_elapsed = month_spend.get("days_elapsed", 19)

    sections = []

    # Stopped resources — still in inventory, paying storage but not compute
    if stopped:
        stopped_storage_monthly = sum(_stopped_cost(r, ebs_by_instance) for r in stopped)
        stopped_storage_mtd = stopped_storage_monthly * days_elapsed / days_in_month
        by_type = {}
        for r in stopped:
            rtype = r.get("resource_type", "unknown")
            if rtype not in by_type:
                by_type[rtype] = {"count": 0, "stopped_monthly": 0, "running_monthly": 0, "names": []}
            by_type[rtype]["count"] += 1
            by_type[rtype]["stopped_monthly"] += _stopped_cost(r, ebs_by_instance)
            by_type[rtype]["running_monthly"] += _when_running_cost(r)
            name = r.get("name", "") or r.get("arn", "").split("/")[-1]
            itype = r.get("instance_type", "")
            label = f"{name} ({itype})" if name and itype else name
            if label:
                by_type[rtype]["names"].append(label)

        rows = []
        for rtype, data in sorted(by_type.items(), key=lambda x: -x[1]["stopped_monthly"]):
            mtd = data["stopped_monthly"] * days_elapsed / days_in_month
            names_str = ", ".join(data["names"][:3])
            if len(data["names"]) > 3:
                names_str += f" (+{len(data['names']) - 3} more)"
            rows.append(f"""
            <tr>
              <td style="font-weight:600;">{_e(rtype)}</td>
              <td>{data["count"]}</td>
              <td style="color:#f59e0b;">{_fmt(mtd)}</td>
              <td style="color:#6b7280;">{_fmt(data["stopped_monthly"])}/mo</td>
              <td style="color:#4b5563; font-size:11px;" title="Cost if instance were running">{_fmt(data["running_monthly"])}/mo</td>
              <td style="color:#6b7280; font-size:11px;">{_e(names_str)}</td>
            </tr>""")

        sections.append(f"""
        <div style="margin-bottom:16px;">
          <div style="font-size:12px; color:#f59e0b; font-weight:600; margin-bottom:8px;">
            Stopped Resources &mdash; ~{_fmt(stopped_storage_mtd)} storage cost on this month&#39;s bill
          </div>
          <p style="color:#6b7280; font-size:11px; margin-bottom:8px;">
            Stopped instances don&#39;t incur compute charges but still pay for
            attached EBS storage. The &ldquo;While Stopped&rdquo; rate is the
            ongoing storage cost; &ldquo;If Running&rdquo; is what they would
            cost at full compute.
          </p>
          <table>
            <thead><tr><th>Type</th><th>Count</th><th>Storage MTD</th><th>While Stopped</th><th>If Running</th><th>Resources</th></tr></thead>
            <tbody>{"".join(rows)}</tbody>
          </table>
        </div>""")

    # Destroyed resources — no longer in inventory, but already billed this month
    if destroyed:
        # Calculate per-resource prorated cost using actual termination dates
        # from CloudTrail when available, otherwise fall back to midpoint estimate
        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        destroyed_mtd = 0.0
        by_type = {}
        for r in destroyed:
            rtype = r.get("resource_type", "unknown")
            monthly = _when_running_cost(r)
            # Use actual termination date if available from CloudTrail
            term_at = r.get("terminated_at", "")
            if term_at:
                try:
                    term_dt = datetime.fromisoformat(term_at)
                    alive_days = max((term_dt - month_start).days, 1)
                except (ValueError, TypeError):
                    alive_days = max(days_elapsed // 2, 1)
            else:
                alive_days = max(days_elapsed // 2, 1)
            resource_mtd = monthly * alive_days / days_in_month
            destroyed_mtd += resource_mtd
            if rtype not in by_type:
                by_type[rtype] = {"count": 0, "monthly": 0, "mtd": 0}
            by_type[rtype]["count"] += 1
            by_type[rtype]["monthly"] += monthly
            by_type[rtype]["mtd"] += resource_mtd

        has_cloudtrail = any(r.get("terminated_at") for r in destroyed)
        source_note = (
            "Based on CloudTrail termination dates." if has_cloudtrail else "Approximated (no CloudTrail data)."
        )

        rows = []
        for rtype, data in sorted(by_type.items(), key=lambda x: -x[1]["mtd"]):
            rows.append(f"""
            <tr>
              <td style="font-weight:600;">{_e(rtype)}</td>
              <td>{data["count"]}</td>
              <td style="color:#ef4444;">{_fmt(data["mtd"])}</td>
              <td style="color:#6b7280;">{_fmt(data["monthly"])}/mo</td>
            </tr>""")

        sections.append(f"""
        <div style="margin-bottom:16px;">
          <div style="font-size:12px; color:#ef4444; font-weight:600; margin-bottom:8px;">
            Destroyed This Month &mdash; ~{_fmt(destroyed_mtd)} already on this month&#39;s bill
          </div>
          <p style="color:#6b7280; font-size:11px; margin-bottom:8px;">
            These {len(destroyed)} resources were running earlier this month but have since
            been terminated. Their charges are already on the AWS bill but they no longer
            appear in Estimated (which reflects the current inventory).
            {source_note}
          </p>
          <table>
            <thead><tr><th>Type</th><th>Count</th><th>Estimated MTD</th><th>Rate</th></tr></thead>
            <tbody>{"".join(rows)}</tbody>
          </table>
        </div>""")

    # Total inactive billing impact
    total_inactive_mtd = 0
    if stopped:
        total_inactive_mtd += sum(_stopped_cost(r, ebs_by_instance) for r in stopped) * days_elapsed / days_in_month
    if destroyed:
        total_inactive_mtd += destroyed_mtd

    if total_inactive_mtd > 0:
        sections.append(f"""
        <div style="padding:8px 14px; background:#0a0a0f; border-radius:6px; border:1px solid #1e1e2e; margin-top:4px;">
          <span style="font-size:12px; color:#94a3b8;">
            Estimated inactive resource billing impact:
            <strong style="color:#f59e0b;">{_fmt(total_inactive_mtd)}</strong> of this month&#39;s bill
          </span>
        </div>""")

    return "\n".join(sections)


def _render_ghost_services(service_costs, by_type):
    """Render callout for billed services with no discovered resources.

    These are AWS services that appear on the bill but have zero matching
    resources in the scanner inventory — indicating destroyed infrastructure,
    unscanned regions, or services the scanner doesn't cover.
    """
    if not service_costs:
        return ""

    # Build set of CE service names that have at least one discovered resource
    covered_ce_services = set()
    for rtype in by_type:
        ce_svc = RTYPE_TO_CE_SERVICE.get(rtype)
        if not ce_svc:
            if rtype.startswith("s3:"):
                ce_svc = "Amazon Simple Storage Service"
            elif rtype.startswith("sns:"):
                ce_svc = "Amazon Simple Notification Service"
            elif rtype.startswith("sqs:"):
                ce_svc = "Amazon Simple Queue Service"
            elif rtype.startswith("apigateway:"):
                ce_svc = "Amazon API Gateway"
        if ce_svc:
            covered_ce_services.add(ce_svc)

    # Services that are purely usage-based and never have "resources" —
    # don't flag these as ghost services, they're expected
    _USAGE_ONLY = {
        "AWS Data Transfer",
        "Tax",
        "AWS Marketplace",
        "AWS Cost Explorer",
        "AWS Config",
        "AWS CloudTrail",
        "AWS WAF",
        "Amazon GuardDuty",
        "Amazon Inspector",
        "AWS Identity and Access Management",
        "Amazon Bedrock",
    }

    ghosts = []
    for svc, amount in service_costs.items():
        if amount < 1.0:
            continue
        if svc in covered_ce_services:
            continue
        if svc in _USAGE_ONLY or svc.startswith("AWS Support"):
            continue
        ghosts.append((svc, amount))

    if not ghosts:
        return ""

    ghosts.sort(key=lambda x: -x[1])
    total_ghost = sum(a for _, a in ghosts)

    rows = []
    for svc, amount in ghosts:
        short = _shorten_svc(svc)
        rows.append(f"""
          <tr>
            <td style="font-weight:600;">{_e(short)}</td>
            <td style="color:#ef4444; font-weight:600;">{_fmt(amount)}</td>
            <td style="color:#6b7280; font-size:11px;">{_e(svc)}</td>
          </tr>""")

    return f"""
    <div style="margin-bottom:20px; padding:14px; background:rgba(239,68,68,0.04); border:1px solid rgba(239,68,68,0.2); border-radius:8px;">
      <div style="font-size:12px; color:#ef4444; font-weight:700; margin-bottom:6px;">
        Billed Services With No Discovered Resources &mdash; {_fmt(total_ghost)}
      </div>
      <p style="color:#94a3b8; font-size:11px; margin-bottom:10px;">
        These AWS services appear on the bill but the scanner found <strong>zero</strong>
        matching resources. This typically means the resources were destroyed before the
        scanner could observe them, or they exist in a region the scanner does not cover.
        This spend is invisible to the Estimated and Posture sections.
      </p>
      <table>
        <thead><tr><th>Service</th><th>&#9664; Billed (Past 30 Days)</th><th>Full Name</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </div>"""


def _render_fixed_costs():
    """Render the fixed costs callout (costs not visible in Cost Explorer)."""
    if not _FIXED_COSTS:
        return ""
    rows = []
    for name, amount, note in _FIXED_COSTS:
        rows.append(f"""
          <tr>
            <td style="font-weight:600;">{_e(name)}</td>
            <td style="font-weight:600;">{_fmt(amount)}/mo</td>
            <td style="color:#6b7280; font-size:11px;">{_e(note)}</td>
          </tr>""")

    return f"""
    <div style="margin-bottom:20px; padding:14px; background:rgba(245,158,11,0.04); border:1px solid rgba(245,158,11,0.2); border-radius:8px;">
      <div style="font-size:12px; color:#f59e0b; font-weight:700; margin-bottom:6px;">
        Fixed Costs (Not in Cost Explorer) &mdash; {_fmt(_FIXED_COST_TOTAL)}/mo
      </div>
      <div style="font-size:11px; color:#6b7280; margin-bottom:8px;">
        These costs are included in Estimated but do not appear in the Cost Explorer
        SERVICE dimension, so they are missing from Billed totals above.
      </div>
      <table>
        <thead><tr><th>Item</th><th>Monthly Cost</th><th>Note</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </div>"""


def _envelope_estimates(resources, service_costs):
    """Compute back-of-the-envelope estimates for usage-based cost categories.

    Returns a dict keyed by category name.  Each value is a dict with:
      infra  — what we found (e.g. "15 buckets, 42GB stored")
      math   — the arithmetic shown to the user
      est    — rough monthly $ estimate (float)
      billed — actual Cost Explorer amount or None
    """

    # ---- helpers ----
    def _by_type(prefix):
        return [r for r in resources if r.get("resource_type", "").startswith(prefix)]

    def _count(prefix):
        return len(_by_type(prefix))

    def _ce(name):
        """Look up Cost Explorer service by name (case-insensitive substring)."""
        for k, v in service_costs.items():
            if name.lower() in k.lower():
                return v
        return None

    est = {}

    # -- Data Transfer --
    nat_n = _count("ec2:natgateway")
    ecs_n = _count("ecs:service")
    ec2_n = len([r for r in _by_type("ec2:instance") if (r.get("state") or "").lower() == "running"])
    endpoints = nat_n + ecs_n + ec2_n
    # Heuristic: ~1GB egress/endpoint/mo for small infra, $0.09/GB in PRICE_TABLE_REGION
    dt_est = endpoints * 1.0 * 0.09
    est["data_transfer"] = {
        "infra": f"{nat_n} NAT gw, {ecs_n} ECS svc, {ec2_n} running EC2",
        "math": f"{endpoints} endpoints &times; ~1GB/mo &times; $0.09/GB",
        "est": dt_est,
        "billed": _ce("Data Transfer"),
    }

    # -- S3 Request Costs --
    s3 = _by_type("s3:")
    total_gb = sum(r.get("size_bytes") or 0 for r in s3) / (1024**3)
    # Heuristic: ~1K PUTs + 10K GETs per GB stored per month
    put_cost = total_gb * 1000 * 0.005 / 1000
    get_cost = total_gb * 10000 * 0.0004 / 1000
    est["s3_requests"] = {
        "infra": f"{len(s3)} buckets, {total_gb:.1f}GB stored",
        "math": f"~{total_gb * 1:.0f}K PUTs ($0.005/1K) + ~{total_gb * 10:.0f}K GETs ($0.0004/1K)",
        "est": put_cost + get_cost,
        "billed": None,  # S3 requests are bundled into S3 total — can't split
    }

    # -- CloudWatch Metrics & Alarms --
    # No alarm discovery; estimate from resource count (each resource ~5 basic metrics free,
    # custom metrics $0.30/metric/mo, first 10 free)
    total_resources = len(resources)
    # Heuristic: ~1 custom metric per 10 resources, $0.30 each
    custom_metrics = max(0, total_resources // 10)
    cw_metrics_est = custom_metrics * 0.30
    est["cw_metrics"] = {
        "infra": f"{total_resources} discovered resources",
        "math": f"~{custom_metrics} custom metrics &times; $0.30/metric/mo",
        "est": cw_metrics_est,
        "billed": _ce("CloudWatch") or _ce("AmazonCloudWatch"),
    }

    # -- CloudWatch Log Ingestion --
    logs = _by_type("logs:log-group")
    total_stored_gb = sum(r.get("stored_bytes") or 0 for r in logs) / (1024**3)
    # Heuristic: assume 30-day retention average, so daily ingestion ~ stored/30
    # Monthly ingestion ~ stored_gb (stored/30 * 30)
    ingestion_gb = total_stored_gb  # simplification: 1 month of logs ~ stored amount
    ingestion_cost = ingestion_gb * 0.57
    est["cw_log_ingestion"] = {
        "infra": f"{len(logs)} log groups, {total_stored_gb:.1f}GB stored",
        "math": f"~{ingestion_gb:.1f}GB ingested/mo &times; $0.57/GB",
        "est": ingestion_cost,
        "billed": None,  # bundled into CloudWatch — shown in cw_metrics
    }

    # -- Lambda Invocations --
    lambda_n = _count("lambda:function")
    eb_n = _count("events:rule")
    # Heuristic: scheduled rules fire ~4320/mo (every 10min) or ~720/mo (hourly),
    # average ~2000 invocations/rule/mo; each invocation ~200ms @ 128MB
    est_invocations = eb_n * 2000
    inv_cost = est_invocations * 0.20 / 1_000_000  # $0.20/1M requests
    duration_cost = est_invocations * 0.2 * 0.0000166667 * 1  # 200ms * 128MB cost
    est["lambda"] = {
        "infra": f"{lambda_n} functions, {eb_n} EventBridge rules",
        "math": f"~{est_invocations:,} invocations/mo &times; $0.20/1M + duration",
        "est": inv_cost + duration_cost,
        "billed": _ce("Lambda") or _ce("AWS Lambda"),
    }

    # -- API Gateway --
    api_n = _count("apigateway:")
    # Heuristic: ~100K requests/API/mo for internal dashboards/APIs
    api_reqs = api_n * 100_000
    api_cost = api_reqs * 1.17 / 1_000_000
    est["api_gateway"] = {
        "infra": f"{api_n} APIs",
        "math": f"~{api_reqs // 1000}K requests/mo &times; $1.17/1M",
        "est": api_cost,
        "billed": _ce("API Gateway"),
    }

    # -- DynamoDB Throughput --
    ddb_n = _count("dynamodb:table")
    # On-demand: $1.25/1M WCUs, $0.25/1M RCUs
    # Heuristic: ~10K writes + 50K reads per table per month (light usage)
    ddb_write = ddb_n * 10_000 * 1.25 / 1_000_000
    ddb_read = ddb_n * 50_000 * 0.25 / 1_000_000
    est["dynamodb"] = {
        "infra": f"{ddb_n} tables (on-demand)",
        "math": f"~{ddb_n * 10}K WCUs ($1.25/1M) + ~{ddb_n * 50}K RCUs ($0.25/1M)",
        "est": ddb_write + ddb_read,
        "billed": _ce("DynamoDB"),
    }

    # -- GuardDuty / CloudTrail / Config --
    # Event-volume pricing scales with account activity
    gd_billed = _ce("GuardDuty")
    ct_billed = _ce("CloudTrail")
    cfg_billed = _ce("Config") or _ce("AWS Config")
    security_billed = sum(v for v in (gd_billed, ct_billed, cfg_billed) if v)
    est["security_services"] = {
        "infra": f"{total_resources} resources under observation",
        "math": "Event-volume pricing; scales with API calls and resource count",
        "est": None,  # too speculative
        "billed": security_billed or None,
    }

    # -- Bedrock --
    # Haiku 4.5: ~$0.001/1K input + $0.005/1K output tokens
    # HK generates ~300 output tokens per enforcement run
    hk_n = len([r for r in _by_type("lambda:function") if "hunterkiller" in (r.get("name") or "").lower()])
    # Heuristic: 1 enforcement run/day = ~30/mo, 300 output + 200 input tokens each
    runs_mo = 30
    bedrock_cost = runs_mo * (0.20 * 0.001 / 1000 + 0.30 * 0.005 / 1000) if hk_n else 0
    est["bedrock"] = {
        "infra": f"{hk_n} Hunterkiller function{'s' if hk_n != 1 else ''}",
        "math": f"~{runs_mo} runs/mo &times; ~500 tokens &times; Haiku pricing"
        if hk_n
        else "No Hunterkiller functions discovered",
        "est": bedrock_cost,
        "billed": _ce("Bedrock"),
    }

    # -- SES --
    # $0.10 per 1K emails
    ses_senders = len([r for r in _by_type("ecs:service")])
    # Heuristic: ~100 emails/service/mo (alerts, notifications)
    ses_emails = ses_senders * 100
    ses_cost = ses_emails * 0.10 / 1000
    est["ses"] = {
        "infra": f"{ses_senders} ECS services (potential senders)",
        "math": f"~{ses_emails} emails/mo &times; $0.10/1K",
        "est": ses_cost,
        "billed": _ce("Simple Email Service") or _ce("SES"),
    }

    return est


def _render_uncalculated_costs(resources=None, service_costs=None):
    """Render explainer for cost categories not included in estimates."""
    resources = resources or []
    service_costs = service_costs or {}
    env = _envelope_estimates(resources, service_costs)

    # Category definitions: (key, label, why, where)
    categories = [
        (
            "data_transfer",
            "Data Transfer",
            "Egress charges depend on destination (internet, cross-region, cross-AZ). No resource to discover.",
            "Billed under &ldquo;AWS Data Transfer&rdquo; or embedded in EC2/S3 line items",
        ),
        (
            "s3_requests",
            "S3 Request Costs",
            "Per-GET/PUT pricing varies by access pattern. Storage size is estimated; request volume is not.",
            "Billed under &ldquo;S3&rdquo; alongside storage",
        ),
        (
            "cw_metrics",
            "CloudWatch Metrics &amp; Alarms",
            "Custom metrics ($0.30/metric/mo), dashboards, and alarm evaluations. Scales with observability depth.",
            "Billed under &ldquo;CloudWatch&rdquo;",
        ),
        (
            "cw_log_ingestion",
            "CloudWatch Log Ingestion",
            "$0.57/GB ingested. Storage is estimated, but ingestion rate is not modeled.",
            "Billed under &ldquo;CloudWatch&rdquo;",
        ),
        (
            "lambda",
            "Lambda Invocations",
            "$0.20/1M requests + duration. Small for a dashboard; grows with traffic.",
            "Billed under &ldquo;Lambda&rdquo;",
        ),
        (
            "api_gateway",
            "API Gateway Requests",
            "$1.17/1M requests. Usage-dependent, no static resource to price.",
            "Billed under &ldquo;API Gateway&rdquo;",
        ),
        (
            "dynamodb",
            "DynamoDB Throughput",
            "On-demand: $1.25/1M WCUs, $0.25/1M RCUs. Table existence is estimated at $0.50/mo minimum.",
            "Billed under &ldquo;DynamoDB&rdquo;",
        ),
        (
            "security_services",
            "GuardDuty / CloudTrail / Config",
            "Event-volume pricing. Scales with account activity, not resource count.",
            "Billed under respective service names",
        ),
        (
            "bedrock",
            "Bedrock Model Invocations",
            "Per-token pricing (input/output). Used by Hunterkiller for persona-voiced "
            "enforcement emails (Claude Haiku 4.5).",
            "Billed under &ldquo;Amazon Bedrock&rdquo;",
        ),
        (
            "ses",
            "SES Email Sending",
            "$0.10/1K emails. Volume-dependent.",
            "Billed under &ldquo;SES&rdquo;",
        ),
    ]

    rows = []
    for key, label, why, where in categories:
        e = env.get(key, {})
        infra = _e(e.get("infra", ""))
        math = e.get("math", "")
        est_val = e.get("est")
        billed = e.get("billed")

        # Main row
        rows.append(f"""
        <tr>
          <td style="font-weight:600;">{label}</td>
          <td style="color:#6b7280;">{why}</td>
          <td style="color:#6b7280;">{where}</td>
        </tr>""")

        # Napkin math sub-row
        est_str = _fmt(est_val) if est_val and est_val > 0.001 else "&lt;$0.01"
        if billed and billed > 0:
            billed_str = f'<span style="color:#e2e8f0;">{_fmt(billed)}</span>'
        else:
            billed_str = (
                '<span class="hint" style="color:#22c55e; font-size:9px;" '
                'data-tip="This service has not appeared on the AWS bill. '
                "Usage is within the free tier, rounds to $0.00, or "
                "the service hasn't been used this billing period.\""
                ">$0 (not on bill)</span>"
            )
        if est_val is None:
            est_str = (
                '<span class="hint" style="color:#4b5563; font-size:9px;" '
                'data-tip="This cost depends entirely on event volume, API call '
                "frequency, or account activity — not on resource count. "
                "Even a rough estimate would be misleading. "
                'Check the Billed column for actual charges."'
                ">too variable</span>"
            )

        rows.append(f"""
        <tr style="background:rgba(75,85,99,0.06);">
          <td style="padding-left:20px; font-size:10px; color:#6b7280;">
            <span style="color:#9ca3af;">Your infra:</span> {infra}
          </td>
          <td style="font-size:10px; color:#6b7280;">
            <span style="color:#9ca3af;">Napkin Math:</span> {math}
            &asymp; {est_str}/mo
          </td>
          <td style="font-size:10px;">
            <span style="color:#9ca3af;">Billed:</span> {billed_str}
          </td>
        </tr>""")

    return f"""
    <!-- ====== ESTIMATION GAPS ====== -->
    <div style="margin:32px 0 16px; padding:10px 14px; background:linear-gradient(90deg, rgba(75,85,99,0.08), transparent); border-left:3px solid #4b5563; border-radius:0 6px 6px 0;">
      <div style="font-size:11px; color:#9ca3af; text-transform:uppercase; letter-spacing:0.12em; font-weight:700;">What&#39;s Not in the Estimates</div>
      <div style="font-size:11px; color:#6b7280; margin-top:2px;">Cost categories the scanner cannot model per-resource. These appear in Billed but not Estimated.</div>
    </div>

    <p style="color:#6b7280; font-size:12px; margin-bottom:16px;">
      The Estimated column covers resources with deterministic pricing: compute instances,
      storage volumes, load balancers, databases, and storage services (S3, EFS, ECR,
      CloudWatch Logs). The following categories are <strong>usage-dependent</strong> and
      not tied to a discoverable resource &mdash; they show up on the AWS bill but cannot
      be attributed to individual resources by the scanner.
      The <span style="background:rgba(75,85,99,0.15); padding:1px 4px; border-radius:2px; font-size:10px;">shaded sub-rows</span>
      show napkin math derived from your discovered infrastructure &mdash;
      these are heuristic, not precise.
    </p>

    <table>
      <thead><tr>
        <th>Category</th><th>Why Not Estimated</th><th>Where It Shows Up</th>
      </tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>

    <p style="color:#4b5563; font-size:11px; margin-top:12px;">
      These gaps explain most of the difference between Estimated and Billed.
      The &ldquo;Spend by AWS Service&rdquo; table above shows billed amounts
      for these services &mdash; compare the &#9664; Billed column (which includes
      everything) against the Estimated &#9654; column (which only covers what the
      scanner can model) to see the magnitude of each gap.
    </p>"""


def render_burn(report, user_email="", forecast=None, month_spend=None, previous_report=None):
    """Render the spend analysis page."""
    if not report or not report.get("discovered_resources"):
        content = """
        <h1>SPEND ANALYSIS</h1>
        <div class="empty">No compliance report data available.
        Run the scanner to generate spend data.</div>"""
        return _layout("Burn", content, user_email, active_page="burn")

    agg = _aggregate(report)
    destroyed = _find_destroyed_resources(report, previous_report)

    content = f"""
    <h1>SPEND ANALYSIS</h1>
    <p style="color:#94a3b8; margin-bottom:20px; font-size:13px;">
      This page presents two complementary views of spend.
      <strong style="color:#e2e8f0;">Billing</strong> (what we&#39;ve spent, where it&#39;s headed)
      uses actual AWS charges and forecasts &mdash; these include everything that happened
      this month, including resources since stopped or destroyed.
      <strong style="color:#3b82f6;">Posture</strong> (what we&#39;re spending now, how responsible
      are we) uses per-resource estimates of the current live inventory &mdash; only active
      resources, only services with pricing models.
      These are different lenses on the same infrastructure. Comparing them directly
      requires accounting for the differences called out in each section.
    </p>

    {_render_summary(agg, forecast, month_spend)}

    <!-- ====== BILLING PERSPECTIVE ====== -->
    <div style="margin:32px 0 16px; padding:10px 14px; background:linear-gradient(90deg, rgba(226,232,240,0.06), transparent); border-left:3px solid #e2e8f0; border-radius:0 6px 6px 0;">
      <div style="font-size:11px; color:#e2e8f0; text-transform:uppercase; letter-spacing:0.12em; font-weight:700;">Billing &mdash; What We&#39;ve Spent</div>
      <div style="font-size:11px; color:#6b7280; margin-top:2px;">Backward-looking actuals from Cost Explorer. Includes all charges incurred this month.</div>
    </div>

    {_render_ghost_services(agg["service_costs"], agg["by_type"])}

    {_render_fixed_costs()}

    <h2>INACTIVE RESOURCES ON THIS MONTH&#39;S BILL</h2>
    <p style="color:#4b5563; font-size:11px; margin:-8px 0 8px;">
      Resources stopped or destroyed this month already incurred charges. They appear
      on the AWS bill and forecast but <strong>not</strong> in Estimated (which only
      counts live resources). This is the primary reason the two perspectives differ.
    </p>
    {_render_inactive_resources(agg["stopped_resources"], destroyed, month_spend, report.get("discovered_resources", []))}

    <h2>SPEND BY AWS SERVICE</h2>
    <p style="color:#4b5563; font-size:11px; margin:-8px 0 8px;">
      Two lenses on the same infrastructure, shown side by side for reference.
      &#9664; <strong>Billed</strong> looks backward (actual charges from the past 30 days).
      <strong>Estimated</strong> &#9654; looks forward (predicted monthly rate for live resources).
      These measure different things and are not directly comparable.
    </p>
    {_render_billed_services(agg["service_costs"], agg["by_type"])}

    <h2>SPEND BY REGION</h2>
    <p style="color:#4b5563; font-size:11px; margin:-8px 0 8px;">
      Same two lenses, broken down by AWS region.
      &#9664; Billed is backward-looking (past 30 days).
      Estimated &#9654; is forward-looking (live resources only).
    </p>
    {_render_region_table(agg["by_region"], agg["region_costs"])}

    <!-- ====== POSTURE PERSPECTIVE ====== -->
    <div style="margin:32px 0 16px; padding:10px 14px; background:linear-gradient(90deg, rgba(59,130,246,0.06), transparent); border-left:3px solid #3b82f6; border-radius:0 6px 6px 0;">
      <div style="font-size:11px; color:#3b82f6; text-transform:uppercase; letter-spacing:0.12em; font-weight:700;">Posture &mdash; Current Burn Rate</div>
      <div style="font-size:11px; color:#6b7280; margin-top:2px;">Forward-looking estimates of live resources. Reflects what we&#39;d pay next month if nothing changed.</div>
    </div>

    <h2>ESTIMATED SPEND BY SERVICE</h2>
    <p style="color:#4b5563; font-size:11px; margin:-8px 0 8px;">
      Grouped by Service tag. Only includes currently running resources with
      known pricing models.
    </p>
    <style>
      .burn-row {{
        display: grid; grid-template-columns: 200px 1fr 100px;
        align-items: center; padding: 8px 0;
        border-bottom: 1px solid #141420;
      }}
      .burn-label {{ display: flex; flex-direction: column; gap: 2px; }}
      .burn-bar-container {{
        display: flex; height: 22px; background: #0a0a0f;
        border-radius: 4px; overflow: hidden;
      }}
      .burn-bar-comp {{
        background: rgba(59,130,246,0.4); height: 100%;
        transition: width 0.3s;
      }}
      .burn-bar-nc {{
        background: rgba(239,68,68,0.6); height: 100%;
        transition: width 0.3s;
      }}
      .burn-amount {{
        text-align: right; font-weight: 600; font-size: 13px; color: #e2e8f0;
      }}
    </style>
    {_render_service_bars(agg["by_service"])}

    <h2>ESTIMATED SPEND BY RESOURCE TYPE</h2>
    {_render_type_table(agg["by_type"])}

    {_render_uncalculated_costs(report.get("discovered_resources", []), agg["service_costs"])}
    """

    return _layout("Burn", content, user_email, active_page="burn")

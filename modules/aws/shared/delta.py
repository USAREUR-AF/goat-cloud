"""
Compliance delta computation — compare two reports to find what changed.

Pure dict comparison, no AWS dependencies. Used by both the compliance
dashboard (/changes page) and the scanner (email delta section).
"""

from compliance_schema import FINDING_CATEGORIES, INACTIVE_STATES, SEVERITY_ORDER

_FINDING_CATEGORIES = tuple(FINDING_CATEGORIES)


def _extract_findings(report):
    """Extract all findings from a report, keyed by ARN for set comparison.

    INFO-severity findings (e.g. expiring-soon warnings) are excluded because
    they are informational and do not represent non-compliance.  This matches
    the filtering applied by get_all_findings(include_info=False) on the
    /findings page.
    """
    by_arn = {}
    if not report:
        return by_arn
    for category in _FINDING_CATEGORIES:
        for f in report.get(category, {}).get("findings", []):
            arn = f.get("resource_arn", "")
            if not arn:
                continue
            if f.get("severity") == "INFO":
                continue
            if arn not in by_arn:
                by_arn[arn] = f
            else:
                # Merge categories for multi-finding resources
                existing = by_arn[arn]
                cats = existing.get("_categories", [existing.get("category", "")])
                new_cat = f.get("category", "")
                if new_cat and new_cat not in cats:
                    cats.append(new_cat)
                existing["_categories"] = cats
                # Keep highest severity
                if SEVERITY_ORDER.get(f.get("severity"), 3) < SEVERITY_ORDER.get(existing.get("severity"), 3):
                    existing["severity"] = f["severity"]
    return by_arn


def _extract_discovered_arns(report):
    """Extract all discovered resource ARNs from a report."""
    if not report:
        return set()
    arns = set()
    for r in report.get("discovered_resources", []):
        arn = r.get("arn", "") or r.get("resource_arn", "")
        if arn:
            arns.add(arn)
    return arns


def _finding_cost(finding):
    """Extract monthly cost from a finding.

    Returns float for known costs, 0 for None (safe for summation).
    """
    raw = finding.get("estimated_monthly_cost")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.0


def _sum_burn(findings_dict):
    """Sum burn rate for a dict of {arn: finding}, excluding inactive resources."""
    total = 0.0
    for f in findings_dict.values():
        state = (f.get("state") or "").lower()
        if state not in INACTIVE_STATES:
            total += _finding_cost(f)
    return total


def compute_delta(today_report, yesterday_report, include_resolution=False):
    """
    Compare today's and yesterday's compliance reports.

    Returns a dict with:
      - new_findings: list of findings new today (not in yesterday)
      - resolved_findings: list of findings from yesterday now gone
      - persistent_findings: list of findings in both reports
      - summary: counts and burn rate delta

    When include_resolution=True, resolved findings get a 'resolution' field:
      "compliant" if the ARN is still discovered, "deleted" otherwise.
    """
    today_findings = _extract_findings(today_report)
    yesterday_findings = _extract_findings(yesterday_report)

    today_arns = set(today_findings.keys())
    yesterday_arns = set(yesterday_findings.keys())
    today_discovered = _extract_discovered_arns(today_report)
    yesterday_discovered = _extract_discovered_arns(yesterday_report)

    new_arns = today_arns - yesterday_arns
    resolved_arns = yesterday_arns - today_arns
    persistent_arns = today_arns & yesterday_arns

    # Build finding lists with full details
    new_findings = sorted(
        [today_findings[a] for a in new_arns],
        key=lambda f: (-_finding_cost(f), f.get("severity", "LOW")),
    )

    if include_resolution:
        resolved_list = []
        for arn in resolved_arns:
            entry = dict(yesterday_findings[arn])
            entry["resolution"] = "compliant" if arn in today_discovered else "deleted"
            resolved_list.append(entry)
        resolved_findings = sorted(
            resolved_list,
            key=lambda f: (-_finding_cost(f), f.get("severity", "LOW")),
        )
    else:
        resolved_findings = sorted(
            [yesterday_findings[a] for a in resolved_arns],
            key=lambda f: (-_finding_cost(f), f.get("severity", "LOW")),
        )
    persistent_findings = sorted(
        [today_findings[a] for a in persistent_arns],
        key=lambda f: (-_finding_cost(f), f.get("severity", "LOW")),
    )

    # Severity changes within persistent findings
    severity_changes = []
    for arn in persistent_arns:
        old_sev = yesterday_findings[arn].get("severity", "")
        new_sev = today_findings[arn].get("severity", "")
        if old_sev != new_sev:
            entry = dict(today_findings[arn])
            entry["previous_severity"] = old_sev
            severity_changes.append(entry)

    # Burn rate calculations
    today_burn = _sum_burn(today_findings)
    yesterday_burn = _sum_burn(yesterday_findings)
    new_burn = sum(_finding_cost(today_findings[a]) for a in new_arns)
    resolved_burn = sum(_finding_cost(yesterday_findings[a]) for a in resolved_arns)

    # Compliant resource changes (discovered but not in findings)
    today_compliant = today_discovered - today_arns
    yesterday_compliant = yesterday_discovered - yesterday_arns
    new_compliant = today_compliant - yesterday_compliant
    lost_compliant = yesterday_compliant - today_compliant

    return {
        "new_findings": new_findings,
        "resolved_findings": resolved_findings,
        "persistent_findings": persistent_findings,
        "severity_changes": severity_changes,
        "summary": {
            "new_count": len(new_findings),
            "resolved_count": len(resolved_findings),
            "persistent_count": len(persistent_findings),
            "severity_change_count": len(severity_changes),
            "new_burn": round(new_burn, 2),
            "resolved_burn": round(resolved_burn, 2),
            "burn_delta": round(today_burn - yesterday_burn, 2),
            "today_burn": round(today_burn, 2),
            "yesterday_burn": round(yesterday_burn, 2),
            "today_total": len(today_findings),
            "yesterday_total": len(yesterday_findings),
            "new_compliant_count": len(new_compliant),
            "lost_compliant_count": len(lost_compliant),
            "today_discovered": len(today_discovered),
            "yesterday_discovered": len(yesterday_discovered),
        },
    }

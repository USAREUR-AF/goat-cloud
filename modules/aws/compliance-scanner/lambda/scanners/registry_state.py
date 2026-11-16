"""A registered stack whose state cannot be read.

Its resources cannot be told from unmanaged ones in this scan. Rather than
report every resource of the stack as click-ops, the scan reports one MEDIUM
finding on the registry entry and treats the resources whose Repository tag
names the entry's repository as that stack's.
"""

from compliance_contract import same_repository


def unreadable_state_finding(entry, registry_arn, region, reason):
    """The one finding a registered, unreadable state produces."""
    slug = entry.get("repo_slug", "")
    return {
        "category": "drift_detection",
        "severity": "MEDIUM",
        "resource_arn": registry_arn,
        "resource_type": "ssm:parameter",
        "resource_name": entry.get("_ssm_name", slug),
        "region": region,
        "state": None,
        "issue": "Registered in the IaC registry, state unreadable",
        "detail": str(reason)[:300],
        "confidence": "MEDIUM",
        "tags": {},
        "managed_by_repo": entry.get("repo_url", ""),
        "managed_by_repo_slug": slug,
        "recommendation": (
            "Make the registered state readable by the scanner (bucket, key, size and permissions), "
            "or correct the entry's state_location. Until then this stack's resources are not checked for drift."
        ),
    }


def resources_of_entry(resources, entry):
    """ARNs of discovered resources whose Repository tag names the entry's repository."""
    repo_url = entry.get("repo_url", "")
    if not repo_url:
        return []
    return [
        r["arn"]
        for r in resources
        if r.get("arn") and same_repository((r.get("tags") or {}).get("Repository", ""), repo_url)
    ]

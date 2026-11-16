"""
Active Region Discovery via Cost Explorer

Uses billing data to identify which AWS regions have resources with spend,
avoiding the need to walk every region x every service.
"""

from datetime import datetime, timedelta

import boto3
from compliance_schema import require_env

# Home region from Lambda's execution environment (set by the provider's region).
# us-east-1 is always included for global services (CloudFront, IAM, Route53, ACM).
_HOME_REGION = require_env("AWS_REGION")
_ALWAYS_INCLUDE = {_HOME_REGION, "us-east-1"}


def discover_active_regions():
    """
    Query Cost Explorer to find regions with spend in the last 30 days.

    Returns:
        tuple: (active_regions: list[str], all_regions_cost: dict[str, float])
    """
    ce = boto3.client("ce")

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    all_regions_cost = {}

    try:
        response = ce.get_cost_and_usage(
            TimePeriod={"Start": start_date, "End": end_date},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "REGION"}],
        )

        for result in response.get("ResultsByTime", []):
            for group in result.get("Groups", []):
                region = group["Keys"][0]
                cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
                all_regions_cost[region] = all_regions_cost.get(region, 0) + cost

    except Exception as e:
        print(f"Cost Explorer query failed: {e}")
        return sorted(_ALWAYS_INCLUDE), {}

    # Filter to regions with non-trivial spend (> $0.01)
    # Cost Explorer returns pseudo-regions like "NoRegion", "global", ""
    # that aren't valid AWS API regions — filter them out
    active_regions = {r for r, c in all_regions_cost.items() if c > 0.01 and r and _is_valid_region(r)}
    active_regions |= _ALWAYS_INCLUDE

    return sorted(active_regions), all_regions_cost


def _is_valid_region(region):
    """Check if a string looks like a valid AWS region name (e.g., us-east-1, eu-central-1)."""
    # Valid regions match: xx-yyyy-N (e.g., us-east-1, eu-central-1, ap-southeast-2)
    # Reject pseudo-regions from Cost Explorer: "NoRegion", "global", "", etc.
    parts = region.split("-")
    if len(parts) < 3:
        return False
    # Last part should be a number
    try:
        int(parts[-1])
    except ValueError:
        return False
    return True

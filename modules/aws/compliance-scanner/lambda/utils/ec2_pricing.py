"""EC2 on-demand pricing — self-populating rate table, rebuilt weekly from the AWS Pricing API.

The scanner needs a per-instance monthly cost. A hand-maintained rate table goes
stale and misses instance families — the compute-optimized c7i/c6i boxes were
falling through to a flat $30 guess while really costing $150-300+/mo, which is
the bulk of the EC2 bill the heuristic was missing.

This keeps the cheap, fast "hardcoded table" shape but makes the table
*self-populating*: on a cache miss it pulls the real on-demand (Linux/Shared)
rate for an (instance_type, region) from the AWS Pricing API, persists the table
in S3 (the reports bucket), and discards the whole table after 7 days so it
rebuilds fresh from source. Steady-state nightly scans pay zero Pricing API
latency; once a week the rates are refreshed.

Public surface:
  - get_ec2_monthly_cost(instance_type, region, state) -> float | None
  - flush_cache()  # call once after a scan to persist newly-fetched rates
"""

import json
import os
import time

import boto3

_CACHE_BUCKET = os.environ.get("REPORTS_BUCKET", "")
_CACHE_KEY = "pricing-cache/ec2-ondemand-rates.json"
_TTL_SECONDS = 7 * 24 * 3600  # rebuild the whole table weekly
_HOURS_PER_MONTH = 730

# In-process cache for one Lambda invocation. Shape:
#   {"generated_at": <epoch>, "rates": {"<region>|<type>": <hourly_usd> | None}}
# A None rate is cached too, so an un-priceable type isn't re-looked-up every
# scan; the 7-day rebuild self-heals any transient Pricing API failure.
_cache = None
_dirty = False


def _load():
    global _cache
    if _cache is not None:
        return _cache
    if _CACHE_BUCKET:
        try:
            obj = boto3.client("s3").get_object(Bucket=_CACHE_BUCKET, Key=_CACHE_KEY)
            data = json.loads(obj["Body"].read())
            if (time.time() - data.get("generated_at", 0)) < _TTL_SECONDS:
                _cache = data
                return _cache
            print("EC2 pricing: cache older than 7 days — rebuilding from Pricing API")
        except Exception:
            pass  # missing/unreadable cache -> rebuild
    _cache = {"generated_at": time.time(), "rates": {}}
    return _cache


def _fetch_hourly(instance_type, region):
    """On-demand Linux/Shared hourly USD for an instance type in a region, or None.

    The Pricing API endpoint only exists in a few regions; us-east-1 serves global
    catalog data and is filtered down to the target region via the regionCode field.
    """
    try:
        pricing = boto3.client("pricing", region_name="us-east-1")
        resp = pricing.get_products(
            ServiceCode="AmazonEC2",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
                {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
                {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            ],
            MaxResults=20,
        )
        for blob in resp.get("PriceList", []):
            product = json.loads(blob)
            for term in product.get("terms", {}).get("OnDemand", {}).values():
                for dim in term.get("priceDimensions", {}).values():
                    usd = dim.get("pricePerUnit", {}).get("USD")
                    if usd and float(usd) > 0:
                        return float(usd)
    except Exception as e:
        print(f"EC2 pricing: lookup failed for {instance_type}/{region}: {e}")
    return None


def get_ec2_monthly_cost(instance_type, region, state):
    """Estimated monthly on-demand cost for an instance.

    Returns 0 for stopped instances (no compute charge), a float for priced ones,
    or None if the type can't be priced (caller may fall back to a seed table).
    """
    global _dirty
    if state == "stopped":
        return 0
    if not instance_type or not region:
        return None
    cache = _load()
    key = f"{region}|{instance_type}"
    if key not in cache["rates"]:
        cache["rates"][key] = _fetch_hourly(instance_type, region)
        _dirty = True
    rate = cache["rates"][key]
    if rate is None:
        return None
    return round(rate * _HOURS_PER_MONTH, 2)


def flush_cache():
    """Persist the rate table to S3 if new rates were fetched this run."""
    global _dirty
    if _dirty and _cache is not None and _CACHE_BUCKET:
        try:
            boto3.client("s3").put_object(
                Bucket=_CACHE_BUCKET,
                Key=_CACHE_KEY,
                Body=json.dumps(_cache).encode("utf-8"),
                ContentType="application/json",
            )
            print(f"EC2 pricing: cache saved ({len(_cache['rates'])} rates)")
        except Exception as e:
            print(f"EC2 pricing: failed to save cache: {e}")
        _dirty = False

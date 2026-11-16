"""
Data layer — Cost Explorer forecasts, current spend, and performance metrics.
"""

import json
import os
from datetime import UTC, datetime

from data_core import (
    _CACHE_TTL_COST,
    LOCAL_DATA_DIR,
    REPORTS_BUCKET,
    _cache_get,
    _cache_set,
    ce,
    s3,
)


def get_cost_forecast():
    """Get AWS Cost Explorer forecast for the current month.

    Calls ce:GetCostForecast with MONTHLY granularity for the remaining days
    of the current calendar month. Returns dict with forecast_total (full month
    predicted spend) and period, or None if unavailable.

    Cached 15 min — Cost Explorer data changes daily at most.
    """
    if LOCAL_DATA_DIR:
        return None

    cached = _cache_get("cost_forecast")
    if cached is not None:
        return cached

    try:
        now = datetime.now(UTC)

        # Forecast from tomorrow to end of month (API requires future dates)
        from datetime import timedelta

        start = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        # End of current month
        if now.month == 12:
            end = f"{now.year + 1}-01-01"
        else:
            end = f"{now.year}-{now.month + 1:02d}-01"

        # If we're on the last day of the month, forecast for next month
        if start >= end:
            if now.month == 12:
                start = f"{now.year + 1}-01-01"
                end = f"{now.year + 1}-02-01"
            else:
                start = end
                if now.month + 1 == 12:
                    end = f"{now.year + 1}-01-01"
                else:
                    end = f"{now.year}-{now.month + 2:02d}-01"

        response = ce.get_cost_forecast(
            TimePeriod={"Start": start, "End": end},
            Metric="UNBLENDED_COST",
            Granularity="MONTHLY",
        )

        total = response.get("Total", {})
        amount = float(total.get("Amount", 0))

        result = {
            "forecast_total": amount,
            "period_start": start,
            "period_end": end,
        }
        _cache_set("cost_forecast", result, _CACHE_TTL_COST)
        return result
    except Exception as e:
        print(f"Cost Explorer forecast error: {e}")
        return None


def get_current_month_spend():
    """Get actual spend for the current calendar month from Cost Explorer.

    Returns dict with total, month_label, and days_elapsed, or None if
    unavailable (local mode or API error).

    Cached 15 min — Cost Explorer data changes daily at most.
    """
    if LOCAL_DATA_DIR:
        return None

    cached = _cache_get("cost_spend")
    if cached is not None:
        return cached

    try:
        now = datetime.now(UTC)
        start = f"{now.year}-{now.month:02d}-01"
        end = now.strftime("%Y-%m-%d")

        # Cost Explorer requires Start < End, so skip on the 1st
        if start == end:
            return {"total": 0.0, "month_label": now.strftime("%b %Y"), "days_elapsed": 0}

        response = ce.get_cost_and_usage(
            TimePeriod={"Start": start, "End": end},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
        )

        total = 0.0
        for result in response.get("ResultsByTime", []):
            total += float(result["Total"]["UnblendedCost"]["Amount"])

        spend = {
            "total": total,
            "month_label": now.strftime("%b %Y"),
            "days_elapsed": now.day - 1,
        }
        _cache_set("cost_spend", spend, _CACHE_TTL_COST)
        return spend
    except Exception as e:
        print(f"Cost Explorer current month error: {e}")
        return None


# =============================================================================
# E2E Performance Results
# =============================================================================


def _get_perf_dates(limit=30):
    """List available perf result dates in S3, newest first."""
    if LOCAL_DATA_DIR:
        perf_dir = os.path.join(LOCAL_DATA_DIR, "perf")
        if not os.path.isdir(perf_dir):
            return []
        dates = [d for d in os.listdir(perf_dir) if os.path.isdir(os.path.join(perf_dir, d))]
        dates.sort(reverse=True)
        return dates[:limit]

    try:
        response = s3.list_objects_v2(
            Bucket=REPORTS_BUCKET,
            Prefix="perf/",
            Delimiter="/",
        )
        prefixes = [p["Prefix"] for p in response.get("CommonPrefixes", [])]
        dates = []
        for p in prefixes:
            parts = p.strip("/").split("/")
            if len(parts) >= 2:
                dates.append(parts[1])
        dates.sort(reverse=True)
        return dates[:limit]
    except Exception as e:
        print(f"Error listing perf dates: {e}")
        return []


def get_perf_results(date=None):
    """Load E2E perf results JSON from S3 (or local file).

    Returns dict with timestamp, passes (cold/warm), route metrics, and
    summaries — or None if no results available.
    """
    if not date:
        dates = _get_perf_dates(limit=1)
        date = dates[0] if dates else None
    if not date:
        return None

    if LOCAL_DATA_DIR:
        path = os.path.join(LOCAL_DATA_DIR, "perf", date, "perf-results.json")
        try:
            with open(path) as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except Exception as e:
            print(f"Error loading local perf results {path}: {e}")
            return None

    key = f"perf/{date}/perf-results.json"
    try:
        response = s3.get_object(Bucket=REPORTS_BUCKET, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except Exception as e:
        print(f"Error loading perf results s3://{REPORTS_BUCKET}/{key}: {e}")
        return None


def get_perf_trend(days=14):
    """Load last N days of perf results and return trend data.

    Returns list of dicts: {date, avg_ttfb_cold, avg_ttfb_warm, p95_cold,
    p95_warm, failed_count} sorted oldest-first for charting.
    """
    dates = _get_perf_dates(limit=days)
    trend = []
    for date in reversed(dates):  # oldest first
        result = get_perf_results(date)
        if not result:
            continue
        passes = result.get("passes", {})
        cold = passes.get("cold", {}).get("summary", {})
        warm = passes.get("warm", {}).get("summary", {})
        trend.append(
            {
                "date": date,
                "avg_ttfb_cold": cold.get("avg_ttfb_ms", 0),
                "avg_ttfb_warm": warm.get("avg_ttfb_ms", 0),
                "p95_cold": cold.get("p95_ttfb_ms", 0),
                "p95_warm": warm.get("p95_ttfb_ms", 0),
                "failed_count": len(cold.get("failed_routes", [])) + len(warm.get("failed_routes", [])),
            }
        )
    return trend

"""
JSON Report Generator

Produces the machine-readable compliance report for programmatic consumption.
"""

import json
from datetime import datetime


class DateTimeEncoder(json.JSONEncoder):
    """Handle datetime serialization."""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def generate_json_report(results):
    """
    Generate a JSON compliance report.

    Args:
        results: Full results dict from the scanner

    Returns:
        str: JSON string
    """
    return json.dumps(results, indent=2, cls=DateTimeEncoder, default=str)

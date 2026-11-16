"""Read-only scan and report: list a cloud scope, grade it against the resource contract, write a report.

The package needs the standard library and PyYAML. Cloud SDKs are imported
only by the inventory adapters, and only when a scan runs.
"""

REPORT_SCHEMA_VERSION = "2.0"

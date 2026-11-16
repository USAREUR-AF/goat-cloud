"""Tests for compliance-dashboard/lambda/data.py — _reassemble_report + _decimal_to_float."""

from decimal import Decimal

from data import _decimal_to_float, _reassemble_report


class TestDecimalToFloat:
    def test_decimal_to_float(self):
        assert _decimal_to_float(Decimal("3.14")) == 3.14
        assert isinstance(_decimal_to_float(Decimal("3.14")), float)

    def test_nested_dict(self):
        result = _decimal_to_float({"a": Decimal("1.5"), "b": "hello"})
        assert result == {"a": 1.5, "b": "hello"}

    def test_nested_list(self):
        result = _decimal_to_float([Decimal("1"), Decimal("2"), "three"])
        assert result == [1.0, 2.0, "three"]

    def test_deeply_nested(self):
        result = _decimal_to_float({"a": [{"b": Decimal("9.99")}]})
        assert result == {"a": [{"b": 9.99}]}

    def test_non_decimal_passthrough(self):
        assert _decimal_to_float(42) == 42
        assert _decimal_to_float("text") == "text"
        assert _decimal_to_float(None) is None
        assert _decimal_to_float(True) is True


class TestReassembleReport:
    def test_meta_item(self):
        items = [
            {
                "PK": "REPORT#2026-02-20T19:41:56",
                "SK": "META",
                "report_version": "1.0",
                "generated_at": "2026-02-20T19:41:56.123456",
                "account_id": "123456789012",
                "project": "acme",
                "environment": "dev",
                "scan_duration_seconds": Decimal("42"),
                "regions_scanned": ["eu-central-1"],
                "regions_with_resources": ["eu-central-1"],
                "summary": {"total_findings": Decimal("5")},
                "resource_type_counts": {"ec2:instance": Decimal("3")},
                "region_type_counts": {},
                "region_costs": {"eu-central-1": Decimal("100")},
                "service_costs": {},
                "errors": [],
            }
        ]
        report = _reassemble_report(items)
        assert report["project"] == "acme"
        assert report["summary"]["total_findings"] == 5.0
        assert report["region_costs"]["eu-central-1"] == 100.0
        # Ensure all finding categories exist even with no FINDING items
        for cat in ("drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"):
            assert cat in report
            assert report[cat] == {"findings": []}

    def test_finding_batches_sorted_and_merged(self):
        items = [
            {
                "PK": "REPORT#t",
                "SK": "META",
                "summary": {},
                "resource_type_counts": {},
                "region_type_counts": {},
                "region_costs": {},
                "service_costs": {},
                "errors": [],
                "regions_scanned": [],
                "regions_with_resources": [],
            },
            {
                "PK": "REPORT#t",
                "SK": "FINDING#drift#0001",
                "findings": [{"resource_arn": "arn2", "severity": "MEDIUM"}],
                "category_meta": {},
            },
            {
                "PK": "REPORT#t",
                "SK": "FINDING#drift#0000",
                "findings": [{"resource_arn": "arn1", "severity": "HIGH"}],
                "category_meta": {"terraform_managed": Decimal("10"), "unmanaged": Decimal("2")},
            },
        ]
        report = _reassemble_report(items)
        drift = report["drift_detection"]
        assert len(drift["findings"]) == 2
        # Batch 0000 comes first (sorted by batch index)
        assert drift["findings"][0]["resource_arn"] == "arn1"
        assert drift["findings"][1]["resource_arn"] == "arn2"
        # Category meta from first batch preserved
        assert drift["terraform_managed"] == 10.0

    def test_resource_batches_sorted(self):
        items = [
            {
                "PK": "REPORT#t",
                "SK": "META",
                "summary": {},
                "resource_type_counts": {},
                "region_type_counts": {},
                "region_costs": {},
                "service_costs": {},
                "errors": [],
                "regions_scanned": [],
                "regions_with_resources": [],
            },
            {
                "PK": "REPORT#t",
                "SK": "RESOURCE#0001",
                "resources": [{"arn": "arn-b"}],
            },
            {
                "PK": "REPORT#t",
                "SK": "RESOURCE#0000",
                "resources": [{"arn": "arn-a"}],
            },
        ]
        report = _reassemble_report(items)
        assert len(report["discovered_resources"]) == 2
        assert report["discovered_resources"][0]["arn"] == "arn-a"
        assert report["discovered_resources"][1]["arn"] == "arn-b"

    def test_section_items_merged(self):
        items = [
            {
                "PK": "REPORT#t",
                "SK": "META",
                "summary": {},
                "resource_type_counts": {},
                "region_type_counts": {},
                "region_costs": {},
                "service_costs": {},
                "errors": [],
                "regions_scanned": [],
                "regions_with_resources": [],
            },
            {
                "PK": "REPORT#t",
                "SK": "SECTION#multi_state",
                "data": {"multi_state_detection": {"workspaces": Decimal("3")}},
            },
        ]
        report = _reassemble_report(items)
        assert report["multi_state_detection"]["workspaces"] == 3.0

    def test_empty_items_returns_empty_categories(self):
        items = [
            {
                "PK": "REPORT#t",
                "SK": "META",
                "summary": {},
                "resource_type_counts": {},
                "region_type_counts": {},
                "region_costs": {},
                "service_costs": {},
                "errors": [],
                "regions_scanned": [],
                "regions_with_resources": [],
            },
        ]
        report = _reassemble_report(items)
        assert report["discovered_resources"] == []
        for cat in ("drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"):
            assert report[cat] == {"findings": []}

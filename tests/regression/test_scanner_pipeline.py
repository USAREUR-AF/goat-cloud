"""Regression: Scanner write → Dashboard read round-trip via DynamoDB."""

from factories import make_report
from moto import mock_aws


@mock_aws
class TestScannerDashboardRoundTrip:
    """Write reports via scanner, read back via dashboard data layer."""

    def test_round_trip_fidelity(self, moto_env):
        """Written report reads back with matching structure and values."""
        from data import _load_report_from_dynamodb

        report_key = moto_env["report_key"]
        original = moto_env["report"]

        loaded = _load_report_from_dynamodb(report_key)
        assert loaded is not None

        # Summary fields match
        assert loaded["summary"]["total_findings"] == original["summary"]["total_findings"]
        assert loaded["summary"]["total_resources_discovered"] == original["summary"]["total_resources_discovered"]

        # Finding counts per category match
        for cat in ("drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"):
            orig_count = len(original[cat]["findings"])
            loaded_count = len(loaded[cat]["findings"])
            assert loaded_count == orig_count, f"{cat}: {loaded_count} != {orig_count}"

        # Discovered resources count matches
        assert len(loaded["discovered_resources"]) == len(original["discovered_resources"])

        # Numeric fields survive Decimal round-trip
        for key in ("region_costs", "service_costs"):
            for k, v in original[key].items():
                assert abs(loaded[key][k] - v) < 0.01, f"{key}.{k}: {loaded[key][k]} != {v}"

    def test_index_latest_points_to_report(self, moto_env):
        """INDEX/LATEST points to the written report."""
        table = moto_env["report_table"]
        resp = table.get_item(Key={"PK": "INDEX", "SK": "LATEST"})
        assert resp["Item"]["report_key"] == moto_env["report_key"]

    def test_second_report_updates_latest(self, moto_env):
        """Writing a second report updates INDEX/LATEST and INDEX/REPORTS."""
        from reporters.dynamodb_report import write_report_to_dynamodb

        report2 = make_report(num_drift=1)
        ts2 = "2026-02-21T10:00:00.000000"
        write_report_to_dynamodb("test-report-data", ts2, report2)

        table = moto_env["report_table"]

        # LATEST points to second report
        resp = table.get_item(Key={"PK": "INDEX", "SK": "LATEST"})
        assert resp["Item"]["report_key"] == "2026-02-21T10:00:00"

        # REPORTS list has both, newest first
        resp = table.get_item(Key={"PK": "INDEX", "SK": "REPORTS"})
        keys = resp["Item"]["report_keys"]
        assert keys[0] == "2026-02-21T10:00:00"
        assert moto_env["report_key"] in keys

    def test_reports_index_trims_to_90(self, moto_env):
        """INDEX/REPORTS trims to 90 entries when more are written."""
        from reporters.dynamodb_report import _update_reports_index

        table = moto_env["report_table"]

        # Write 95 fake keys
        for i in range(95):
            _update_reports_index(table, f"2026-01-{i:02d}T00:00:00")

        resp = table.get_item(Key={"PK": "INDEX", "SK": "REPORTS"})
        keys = resp["Item"]["report_keys"]
        assert len(keys) <= 90

    def test_get_latest_report_date_from_dynamodb(self, moto_env):
        """get_latest_report_date() reads from DynamoDB INDEX/LATEST."""
        import sys

        # The moto_env fixture loaded data as "data" in sys.modules
        data_mod = sys.modules["data"]
        result = data_mod.get_latest_report_date()
        assert result == moto_env["report_key"]

    def test_get_report_dates_from_dynamodb(self, moto_env):
        """get_report_dates() returns keys from INDEX/REPORTS."""
        import sys

        data_mod = sys.modules["data"]
        dates = data_mod.get_report_dates()
        assert moto_env["report_key"] in dates

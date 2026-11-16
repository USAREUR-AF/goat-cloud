"""Self-monitoring: a scan that stops must not look like a clean account."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import index as scanner_index
import pytest
from data_core import scan_freshness
from views import render_home

MODULES = Path(__file__).resolve().parents[2] / "modules" / "aws"
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
NO_FINDINGS = {"high": 0, "medium": 0, "low": 0, "resources": 5}


def _report(hours_ago, **extra):
    stamp = (NOW - timedelta(hours=hours_ago)).isoformat()
    return {"generated_at": stamp, "last_successful_scan_at": stamp, "scan_complete": True, **extra}


class TestScanFreshness:
    def test_recent_complete_scan_is_ok(self):
        result = scan_freshness(_report(3), now=NOW)
        assert result["status"] == "ok" and round(result["age_hours"]) == 3

    def test_old_scan_is_stale(self):
        assert scan_freshness(_report(27), now=NOW)["status"] == "stale"
        assert scan_freshness(_report(25), now=NOW)["status"] == "ok"

    def test_incomplete_scan_is_an_error_and_keeps_the_older_success_time(self):
        last_ok = (NOW - timedelta(hours=40)).isoformat()
        report = {"generated_at": NOW.isoformat(), "scan_complete": False, "last_successful_scan_at": last_ok}
        result = scan_freshness(report, now=NOW)
        assert result["status"] == "error" and round(result["age_hours"]) == 40

    def test_no_report_is_unknown(self):
        assert scan_freshness({}, now=NOW)["status"] == "unknown"
        assert scan_freshness(None, now=NOW)["status"] == "unknown"

    def test_report_from_before_the_field_existed(self):
        report = {"generated_at": (NOW - timedelta(hours=2)).isoformat()}
        assert scan_freshness(report, now=NOW)["status"] == "ok"

    def test_unreadable_time_is_unknown(self):
        report = {"generated_at": "x", "last_successful_scan_at": "not a time", "scan_complete": True}
        assert scan_freshness(report, now=NOW)["status"] == "unknown"


class TestOverviewStatus:
    def _status(self, freshness):
        page = render_home(NO_FINDINGS, 0, 0, "2026-03-10", scan_freshness=freshness)
        return re.search(r"Compliance Status</div>\s*<div[^>]*>([^<]+)</div>", page).group(1)

    def test_fresh_scan_with_no_findings_is_good(self):
        assert self._status({"status": "ok", "age_hours": 2.0}) == "GOOD"

    @pytest.mark.parametrize(
        ("freshness", "label"),
        [
            ({"status": "stale", "age_hours": 50.0}, "STALE"),
            ({"status": "error", "age_hours": 30.0}, "SCAN FAILED"),
            ({"status": "unknown", "age_hours": None}, "UNKNOWN"),
        ],
    )
    def test_zero_findings_is_not_good_without_a_fresh_complete_scan(self, freshness, label):
        assert self._status(freshness) == label


class TestScannerRecordsOutcome:
    def test_complete_scan_is_its_own_last_success(self):
        results = {"scan_complete": True, "generated_at": "2026-03-10T02:00:00+00:00"}
        assert scanner_index._last_successful_scan_at(results, MagicMock(), "2026-03-10") == results["generated_at"]

    def test_failed_scan_carries_the_previous_success_time(self):
        results = {"scan_complete": False, "generated_at": "2026-03-10T02:00:00+00:00"}
        previous = {"generated_at": "2026-03-09T02:00:00+00:00", "last_successful_scan_at": "2026-03-08T02:00:00+00:00"}
        with (
            patch.object(scanner_index, "REPORTS_BUCKET", "reports"),
            patch.object(scanner_index, "_load_previous_report", return_value=previous),
        ):
            assert (
                scanner_index._last_successful_scan_at(results, MagicMock(), "2026-03-10")
                == "2026-03-08T02:00:00+00:00"
            )

    def test_failed_scan_after_an_older_format_report(self):
        results = {"scan_complete": False, "generated_at": "2026-03-10T02:00:00+00:00"}
        previous = {"generated_at": "2026-03-09T02:00:00+00:00"}
        with (
            patch.object(scanner_index, "REPORTS_BUCKET", "reports"),
            patch.object(scanner_index, "_load_previous_report", return_value=previous),
        ):
            assert (
                scanner_index._last_successful_scan_at(results, MagicMock(), "2026-03-10")
                == "2026-03-09T02:00:00+00:00"
            )

    def test_failed_scan_with_no_history(self):
        results = {"scan_complete": False, "generated_at": "2026-03-10T02:00:00+00:00"}
        with (
            patch.object(scanner_index, "REPORTS_BUCKET", "reports"),
            patch.object(scanner_index, "_load_previous_report", side_effect=RuntimeError("no access")),
        ):
            assert scanner_index._last_successful_scan_at(results, MagicMock(), "2026-03-10") == ""

    @pytest.mark.parametrize(("succeeded", "ok", "failed"), [(True, 1, 0), (False, 0, 1)])
    def test_outcome_metric_line(self, capsys, monkeypatch, succeeded, ok, failed):
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "test-scanner")
        scanner_index._emit_scan_metric(succeeded)
        line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        directive = line["_aws"]["CloudWatchMetrics"][0]
        assert directive["Namespace"] == "ComplianceScanner"
        assert directive["Dimensions"] == [["FunctionName"]]
        assert {m["Name"] for m in directive["Metrics"]} == {"ScanSucceeded", "ScanFailed"}
        assert (line["FunctionName"], line["ScanSucceeded"], line["ScanFailed"]) == ("test-scanner", ok, failed)


class TestAlarmsAreDeclared:
    @pytest.mark.parametrize(
        ("module", "alarms"),
        [
            ("compliance-scanner", ["scanner_errors", "scan_failed", "scan_stale"]),
            ("governance", ["lifecycle_manager_errors"]),
            ("hunterkiller", ["notifier_errors", "executor_errors"]),
            ("stack-reaper", ["reconciler_errors", "reaper_errors"]),
        ],
    )
    def test_each_function_has_an_alarm_behind_the_topic_input(self, module, alarms):
        main_tf = (MODULES / module / "main.tf").read_text()
        variables_tf = (MODULES / module / "variables.tf").read_text()
        assert 'variable "alarm_sns_topic_arn"' in variables_tf
        for name in alarms:
            block = main_tf.split(f'resource "aws_cloudwatch_metric_alarm" "{name}"', 1)[1].split("\nresource ", 1)[0]
            assert 'var.alarm_sns_topic_arn != ""' in block
            assert "alarm_actions = [var.alarm_sns_topic_arn]" in block

    def test_stale_scan_alarm_treats_silence_as_failure(self):
        main_tf = (MODULES / "compliance-scanner" / "main.tf").read_text()
        block = main_tf.split('resource "aws_cloudwatch_metric_alarm" "scan_stale"', 1)[1]
        assert re.search(r'treat_missing_data\s*=\s*"breaching"', block)
        assert re.search(r"evaluation_periods\s*=\s*var\.scan_stale_alarm_hours", block)
        assert re.search(r'metric_name\s*=\s*"ScanSucceeded"', block)
        variables_tf = (MODULES / "compliance-scanner" / "variables.tf").read_text()
        assert re.search(r'variable "scan_stale_alarm_hours" \{[^}]*default\s*=\s*26', variables_tf, re.S)

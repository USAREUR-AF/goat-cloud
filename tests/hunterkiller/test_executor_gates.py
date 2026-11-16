"""Executor gates: what must hold before a teardown is attempted."""

import importlib.util
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from factories import make_hk_record

ARN = "arn:aws:ec2:eu-central-1:123456789012:instance/i-0123456789abcdef0"


@pytest.fixture()
def executor_index():
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "modules", "aws", "hunterkiller", "lambda", "executor_index.py"
    )
    spec = importlib.util.spec_from_file_location("hk_executor_index", os.path.normpath(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _approved(**overrides):
    base = {
        "resource_arn": ARN,
        "status": "warned",
        "approval_status": "approved",
        "approved_by": "admin@example.com",
        "approved_at": datetime.now(UTC).isoformat(),
        "tags": {"Environment": "dev"},
    }
    base.update(overrides)
    return make_hk_record(**base)


def _report(*findings, hours_ago=1):
    generated = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    return {"generated_at": generated, "drift_detection": {"findings": list(findings)}}


def _finding(arn=ARN, severity="HIGH", tags=None):
    return {"resource_arn": arn, "severity": severity, "tags": {"Environment": "dev"} if tags is None else tags}


_DEFAULT = object()


def _run_single(mod, record, report=_DEFAULT, live_tags=None):
    state = MagicMock()
    state.get_record.return_value = record
    report = _report(_finding()) if report is _DEFAULT else report
    with (
        patch.object(mod, "execute_teardowns", return_value=[{"resource_arn": ARN, "success": True}]) as teardown,
        patch.object(mod, "_load_compliance_report", return_value=report),
        patch.object(mod, "_live_tags", return_value=live_tags),
    ):
        resp = mod._handle_enforce_single(state, ARN, "admin@example.com", 0)
    return resp, teardown


def _run_batch(mod, record, report, live_tags=None):
    state = MagicMock()
    record = {**record, "grace_deadline": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
    state.get_warned_records.return_value = [record]
    with (
        patch.object(mod, "execute_teardowns", return_value=[{"resource_arn": ARN, "success": True}]) as teardown,
        patch.object(mod, "_load_compliance_report", return_value=report),
        patch.object(mod, "_live_tags", return_value=live_tags),
        patch.object(mod, "_trigger_rescan"),
        patch.object(mod, "SES_SENDER_EMAIL", ""),
    ):
        mod._handle_enforce(state, "2026-01-01", "batch-1", 0)
    return state, teardown


class TestApprovalState:
    def test_recent_approval(self, executor_index):
        assert executor_index._approval_state(_approved(), datetime.now(UTC))[0] == "approved"

    def test_old_approval_expires(self, executor_index):
        old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        assert executor_index._approval_state(_approved(approved_at=old), datetime.now(UTC))[0] == "expired"

    @pytest.mark.parametrize("value", ["", None, "not-a-time", 12345])
    def test_missing_or_unreadable_time_expires(self, executor_index, value):
        assert executor_index._approval_state(_approved(approved_at=value), datetime.now(UTC))[0] == "expired"

    def test_time_without_zone_is_read_as_utc(self, executor_index):
        naive = datetime.now(UTC).replace(tzinfo=None).isoformat()
        assert executor_index._approval_state(_approved(approved_at=naive), datetime.now(UTC))[0] == "approved"
        old = (datetime.now(UTC) - timedelta(hours=30)).replace(tzinfo=None).isoformat()
        assert executor_index._approval_state(_approved(approved_at=old), datetime.now(UTC))[0] == "expired"

    def test_future_time_expires(self, executor_index):
        future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        assert executor_index._approval_state(_approved(approved_at=future), datetime.now(UTC))[0] == "expired"

    def test_pending_stays_pending(self, executor_index):
        assert executor_index._approval_state(_approved(approval_status="pending"), datetime.now(UTC))[0] == "pending"


class TestEnforceSingle:
    def test_approved_warned_resource_proceeds(self, executor_index):
        resp, teardown = _run_single(executor_index, _approved())
        assert resp["statusCode"] == 200
        teardown.assert_called_once()

    @pytest.mark.parametrize("status", ["aborted", "resolved", "protected", "torn_down", "dry_run", ""])
    def test_only_a_warned_resource_is_eligible(self, executor_index, status):
        resp, teardown = _run_single(executor_index, _approved(status=status))
        assert resp["statusCode"] == 409
        teardown.assert_not_called()

    def test_record_that_never_came_from_a_warning_is_refused(self, executor_index):
        bare = {"resource_arn": ARN, "approval_status": "approved", "approved_at": datetime.now(UTC).isoformat()}
        resp, teardown = _run_single(executor_index, bare)
        assert resp["statusCode"] == 409
        teardown.assert_not_called()

    def test_approval_without_time_is_refused(self, executor_index):
        resp, teardown = _run_single(executor_index, _approved(approved_at=""))
        assert resp["statusCode"] == 403
        teardown.assert_not_called()

    def test_protected_resource_is_refused(self, executor_index):
        resp, teardown = _run_single(executor_index, _approved(tags={"Environment": "prod"}))
        assert "protected" in resp["body"]
        teardown.assert_not_called()


class TestProtection:
    def test_tag_keys_match_without_case(self, executor_index):
        assert executor_index._is_protected(_approved(tags={"environment": "Prod"})) is True
        assert executor_index._is_protected(_approved(tags={"PROTECTED": "True"})) is True
        assert executor_index._is_protected(_approved(tags={"Environment": "dev"})) is False

    def test_unreadable_tags_protect(self, executor_index):
        assert executor_index._is_protected(_approved(tags="Environment=dev")) is True

    def test_unreadable_protected_list_protects_everything(self, executor_index):
        ssm = MagicMock()
        ssm.get_parameter.side_effect = RuntimeError("access denied")
        with (
            patch.object(executor_index, "PROTECTED_RESOURCES_SSM", "/test/protected"),
            patch("boto3.client", return_value=ssm),
        ):
            assert executor_index._is_protected(_approved()) is True

    def test_malformed_protected_list_protects_everything(self, executor_index):
        ssm = MagicMock()
        ssm.get_parameter.return_value = {"Parameter": {"Value": "not json"}}
        with (
            patch.object(executor_index, "PROTECTED_RESOURCES_SSM", "/test/protected"),
            patch("boto3.client", return_value=ssm),
        ):
            assert executor_index._is_protected(_approved()) is True

    def test_no_list_configured(self, executor_index):
        with patch.object(executor_index, "PROTECTED_RESOURCES_SSM", ""):
            assert executor_index._is_protected(_approved()) is False


class TestProtectionFromCurrentTags:
    """Protection is decided from the tags the resource carries now, not when it was warned."""

    def test_single_protected_tag_added_after_warning_in_fresh_report(self, executor_index):
        report = _report(_finding(tags={"Environment": "dev", "Protected": "true"}))
        resp, teardown = _run_single(executor_index, _approved(), report=report)
        assert "protected" in resp["body"]
        teardown.assert_not_called()

    def test_single_protected_tag_read_live(self, executor_index):
        resp, teardown = _run_single(executor_index, _approved(), live_tags={"Protected": "yes"})
        assert "protected" in resp["body"]
        teardown.assert_not_called()

    def test_single_unreadable_live_tags_protect(self, executor_index):
        state = MagicMock()
        state.get_record.return_value = _approved()
        with (
            patch.object(executor_index, "execute_teardowns") as teardown,
            patch.object(executor_index, "_load_compliance_report", return_value=_report(_finding())),
            patch.object(executor_index, "_live_tags", side_effect=executor_index.LiveTagsUnavailable("denied")),
        ):
            resp = executor_index._handle_enforce_single(state, ARN, "admin@example.com", 0)
        assert "protected" in resp["body"]
        teardown.assert_not_called()

    def test_batch_protected_tag_added_after_warning(self, executor_index):
        report = _report(_finding(tags={"Protected": "TRUE"}))
        state, teardown = _run_batch(executor_index, _approved(), report)
        teardown.assert_not_called()
        state.update_status.assert_any_call(ARN, "protected")

    def test_batch_protected_tag_read_live(self, executor_index):
        state, teardown = _run_batch(
            executor_index, _approved(), _report(_finding()), live_tags={"Environment": "prod"}
        )
        teardown.assert_not_called()

    def test_batch_unprotected_resource_proceeds(self, executor_index):
        state, teardown = _run_batch(executor_index, _approved(), _report(_finding()), live_tags={"Environment": "dev"})
        teardown.assert_called_once()

    def test_stored_tags_still_protect(self, executor_index):
        resp, teardown = _run_single(executor_index, _approved(tags={"Protected": "true"}))
        teardown.assert_not_called()


class TestSingleNeedsCurrentFinding:
    def test_resource_fixed_since_warning_is_refused(self, executor_index):
        report = _report(_finding(arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-other"))
        resp, teardown = _run_single(executor_index, _approved(), report=report)
        assert resp["statusCode"] == 409
        teardown.assert_not_called()

    def test_finding_below_high_is_refused(self, executor_index):
        resp, teardown = _run_single(executor_index, _approved(), report=_report(_finding(severity="MEDIUM")))
        assert resp["statusCode"] == 409
        teardown.assert_not_called()

    def test_no_report_is_refused(self, executor_index):
        resp, teardown = _run_single(executor_index, _approved(), report=None)
        assert resp["statusCode"] == 409
        teardown.assert_not_called()


class TestLiveTagRead:
    def _client(self, executor_index, client):
        return patch("boto3.client", return_value=client)

    def test_iam_role_read_from_iam(self, executor_index):
        iam = MagicMock()
        iam.list_role_tags.return_value = {"Tags": [{"Key": "Protected", "Value": "true"}]}
        with self._client(executor_index, iam):
            tags = executor_index._live_tags("arn:aws:iam::123456789012:role/app/deployer", "iam:role")
        assert tags == {"Protected": "true"}
        iam.list_role_tags.assert_called_once_with(RoleName="deployer")

    def test_regional_resource_read_from_tagging_api(self, executor_index):
        tagging = MagicMock()
        tagging.get_resources.return_value = {
            "ResourceTagMappingList": [{"ResourceARN": ARN, "Tags": [{"Key": "Environment", "Value": "prod"}]}]
        }
        with self._client(executor_index, tagging):
            assert executor_index._live_tags(ARN, "ec2:instance") == {"Environment": "prod"}
        tagging.get_resources.assert_called_once_with(ResourceARNList=[ARN])

    def test_resource_not_listed_has_no_live_read(self, executor_index):
        tagging = MagicMock()
        tagging.get_resources.return_value = {"ResourceTagMappingList": []}
        with self._client(executor_index, tagging):
            assert executor_index._live_tags(ARN, "ec2:instance") is None

    def test_failed_read_raises(self, executor_index):
        iam = MagicMock()
        iam.list_user_tags.side_effect = RuntimeError("AccessDenied")
        with self._client(executor_index, iam), pytest.raises(executor_index.LiveTagsUnavailable):
            executor_index._live_tags("arn:aws:iam::123456789012:user/someone", "iam:user")


class TestProtectedListShape:
    @pytest.mark.parametrize(
        "value",
        [
            '{"resources": {"app-db": {"arn_pattern": "arn:aws:rds:*"}}}',
            '{"resources": "arn:aws:rds:*"}',
            '{"resources": [{"name": "app-db"}]}',
            '{"resources": [{"arn_pattern": ""}]}',
            '{"resources": ["arn:aws:rds:*"]}',
        ],
    )
    def test_list_that_protects_nothing_by_its_shape_protects_everything(self, executor_index, value):
        ssm = MagicMock()
        ssm.get_parameter.return_value = {"Parameter": {"Value": value}}
        with (
            patch.object(executor_index, "PROTECTED_RESOURCES_SSM", "/test/protected"),
            patch("boto3.client", return_value=ssm),
        ):
            assert executor_index._is_protected(_approved()) is True

    def test_well_formed_list_protects_only_matches(self, executor_index):
        ssm = MagicMock()
        ssm.get_parameter.return_value = {"Parameter": {"Value": '{"resources": [{"arn_pattern": "arn:aws:rds:"}]}'}}
        ssm.get_caller_identity.return_value = {"Account": "123456789012"}
        with (
            patch.object(executor_index, "PROTECTED_RESOURCES_SSM", "/test/protected"),
            patch("boto3.client", return_value=ssm),
        ):
            assert executor_index._is_protected(_approved()) is False


class TestDestroyAllScope:
    def test_all_warned_scope_reads_every_batch(self, executor_index):
        state = MagicMock()
        state.get_warned_records.return_value = []
        with patch.object(executor_index, "StateManager", return_value=state):
            executor_index.handler({"mode": "enforce", "scope": "all_warned"}, None)
        state.get_warned_records.assert_called_once_with("", all_batches=True)

    def test_scheduled_run_reads_its_batch(self, executor_index):
        state = MagicMock()
        state.get_warned_records.return_value = []
        with patch.object(executor_index, "StateManager", return_value=state):
            executor_index.handler({"mode": "enforce", "batch_id": "20260101T000000"}, None)
        state.get_warned_records.assert_called_once_with("20260101T000000", all_batches=False)


class TestAutomaticApproval:
    def _at_scheduled_run(self, executor_index, approved_by, hours_ago):
        approved_at = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
        record = _approved(approved_by=approved_by, approved_at=approved_at)
        with patch.object(executor_index, "GRACE_PERIOD_HOURS", 24.0):
            return executor_index._approval_state(record, datetime.now(UTC))[0]

    def test_automatic_approval_holds_at_the_run_it_was_made_for(self, executor_index):
        # Written when the warning is sent; the run fires a grace period later.
        assert self._at_scheduled_run(executor_index, "auto:expired", 24.1) == "approved"

    def test_automatic_approval_still_expires(self, executor_index):
        assert self._at_scheduled_run(executor_index, "auto:expired", 49) == "expired"

    def test_person_approval_keeps_the_24_hour_window(self, executor_index):
        assert self._at_scheduled_run(executor_index, "admin@example.com", 24.1) == "expired"


class TestDestroyNowReportAge:
    def _single(self, executor_index, reports_by_day):
        state = MagicMock()
        state.get_record.return_value = _approved()
        load = lambda day: reports_by_day.get(day)  # noqa: E731
        with (
            patch.object(
                executor_index, "execute_teardowns", return_value=[{"resource_arn": ARN, "success": True}]
            ) as t,
            patch.object(executor_index, "_load_compliance_report", side_effect=load),
            patch.object(executor_index, "_live_tags", return_value=None),
            patch.object(executor_index, "REPORT_MAX_AGE_HOURS", 36.0),
        ):
            resp = executor_index._handle_enforce_single(state, ARN, "admin@example.com", 0)
        return resp, t

    def _day(self, back):
        return (datetime.now(UTC) - timedelta(days=back)).strftime("%Y-%m-%d")

    def test_before_todays_scan_the_recent_report_is_used(self, executor_index):
        resp, teardown = self._single(executor_index, {self._day(1): _report(_finding(), hours_ago=20)})
        assert resp["statusCode"] == 200
        teardown.assert_called_once()

    def test_report_older_than_the_limit_is_refused(self, executor_index):
        resp, teardown = self._single(executor_index, {self._day(2): _report(_finding(), hours_ago=40)})
        assert resp["statusCode"] == 409
        teardown.assert_not_called()

    def test_recent_report_without_a_high_finding_is_refused(self, executor_index):
        resp, teardown = self._single(
            executor_index, {self._day(1): _report(_finding(severity="MEDIUM"), hours_ago=20)}
        )
        assert resp["statusCode"] == 409
        teardown.assert_not_called()

    def test_report_without_a_readable_time_is_not_used(self, executor_index):
        report = _report(_finding())
        report["generated_at"] = "soon"
        resp, teardown = self._single(executor_index, {self._day(0): report})
        assert resp["statusCode"] == 409

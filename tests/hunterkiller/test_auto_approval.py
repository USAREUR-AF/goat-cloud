"""Automatic approval needs a resource that declares itself temporary, not only a past expiry."""

from datetime import UTC, datetime

import pytest
from factories import make_finding

NOW = datetime(2026, 1, 10, tzinfo=UTC)
PAST = "2026-01-01T00:00:00Z"
FUTURE = "2026-02-01T00:00:00Z"


def _finding(**tags):
    return make_finding(severity="HIGH", tags=tags)


@pytest.mark.parametrize("lifecycle", ["temporary", "experiment", "Temporary"])
def test_temporary_resource_past_its_expiry_is_approvable(hk_index, lifecycle):
    assert hk_index._auto_approvable(_finding(Lifecycle=lifecycle, ExpiresAt=PAST), NOW)


@pytest.mark.parametrize(
    "tags",
    [
        {"ExpiresAt": PAST},
        {"Lifecycle": "persistent", "ExpiresAt": PAST},
        {"Lifecycle": "deprecated", "ExpiresAt": PAST},
        {"Lifecycle": "temporary", "ExpiresAt": FUTURE},
        {"Lifecycle": "temporary", "ExpiresAt": "soon"},
        {"Lifecycle": "temporary"},
        {"Lifecycle": 7, "ExpiresAt": PAST},
    ],
)
def test_other_resources_are_not_approvable(hk_index, tags):
    assert not hk_index._auto_approvable(_finding(**tags), NOW)


def test_unreadable_tags_are_not_approvable(hk_index):
    assert not hk_index._auto_approvable({"resource_arn": "x", "tags": "Lifecycle=temporary"}, NOW)


def test_compact_expiry_form_is_read(hk_index):
    assert hk_index._auto_approvable(_finding(Lifecycle="temporary", ExpiresAt="20260101t0000z"), NOW)
    assert not hk_index._auto_approvable(
        _finding(Lifecycle="temporary", ExpiresAt="20260101t0000z"), datetime(2025, 12, 31, tzinfo=UTC)
    )


def _warn(hk_index, finding_tags):
    from unittest.mock import MagicMock, patch

    finding = make_finding(severity="HIGH", tags=finding_tags)
    report = {"drift_detection": {"findings": [finding]}}
    state = MagicMock()
    state.get_record.return_value = None
    state.reconcile_stale_warnings.return_value = []
    with (
        patch.object(hk_index, "AUTO_APPROVE_EXPIRED", True),
        patch.object(hk_index, "SES_SENDER_EMAIL", ""),
        patch.object(hk_index, "_load_compliance_report", return_value=report),
        patch.object(hk_index, "_load_protected_config", return_value=None),
        patch.object(hk_index, "_schedule_enforcement"),
        patch.object(hk_index, "_write_job_status", create=True),
    ):
        hk_index._handle_warn(state, "2026-01-10", 0)
    return state


def test_past_expiry_tag_alone_approves_nothing(hk_index):
    state = _warn(hk_index, {"ExpiresAt": PAST})
    state.update_approval_status.assert_not_called()


def test_temporary_resource_past_its_expiry_is_approved_at_warning(hk_index):
    state = _warn(hk_index, {"Lifecycle": "temporary", "ExpiresAt": PAST})
    state.update_approval_status.assert_called_once()
    assert state.update_approval_status.call_args.kwargs["approved_by"] == "auto:expired"

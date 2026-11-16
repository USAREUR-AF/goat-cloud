"""Which stacks the reaper tears down: the registry entry decides, tags only hold back."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
import reaper_index
import reconciler_index
from compliance_contract import normalize_repository, parse_expires_at, same_repository
from expiry_decision import decide, resources_of_repository

REPO = "https://github.com/acme/payments"
PAST = "2020-01-01T00:00:00Z"
NOW = datetime.now(UTC)
FUTURE = (NOW + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
SCP = "git" + "@" + "github.com:acme/payments.git"


def _entry(**fields):
    return {"repo_slug": "payments", "repo_url": REPO, "iac_type": "terraform", **fields}


def _resource(name, **tags):
    return {"arn": f"arn:aws:ec2:eu-west-1:123456789012:instance/{name}", "tags": {"Repository": REPO, **tags}}


class _FakeTagging:
    """get_resources over a fixed resource list, honoring exact-match Repository filters."""

    def __init__(self, resources):
        self.resources = resources
        self.filters = None

    def get_paginator(self, name):
        assert name == "get_resources"
        return self

    def paginate(self, TagFilters, ResourcesPerPage):
        self.filters = TagFilters
        values = set(TagFilters[0]["Values"])
        yield {
            "ResourceTagMappingList": [
                {"ResourceARN": r["arn"], "Tags": [{"Key": k, "Value": v} for k, v in r["tags"].items()]}
                for r in self.resources
                if r["tags"].get("Repository") in values
            ]
        }


class TestRegistryEntryDecides:
    @pytest.mark.parametrize(
        "fields",
        [{}, {"lifecycle": "persistent"}, {"lifecycle": "persistent", "expires_at": PAST}, {"lifecycle": "deprecated"}],
    )
    def test_entry_that_does_not_expire_is_persistent(self, fields):
        decision = decide(_entry(**fields), [_resource("a", ExpiresAt=PAST, Lifecycle="temporary")])
        assert decision["status"] == "persistent"

    @pytest.mark.parametrize("expires_at", [None, "", "soon", 20200101, ["2020-01-01"]])
    def test_temporary_entry_without_readable_expiry_is_persistent(self, expires_at):
        decision = decide(_entry(lifecycle="temporary", expires_at=expires_at), [_resource("a", ExpiresAt=PAST)])
        assert decision["status"] == "persistent"

    @pytest.mark.parametrize("lifecycle", [7, None, {"x": 1}])
    def test_lifecycle_of_another_type_is_persistent(self, lifecycle):
        assert decide(_entry(lifecycle=lifecycle, expires_at=PAST), [_resource("a", ExpiresAt=PAST)])["status"] == (
            "persistent"
        )

    def test_temporary_entry_expires_when_resources_agree(self):
        decision = decide(
            _entry(lifecycle="Temporary", expires_at=PAST),
            [_resource("a", ExpiresAt=PAST), _resource("b", ExpiresAt="2020-01-01T00:03:00+00:00")],
        )
        assert decision["status"] == "expires"
        assert decision["expires_at"] == datetime(2020, 1, 1, 0, 3, tzinfo=UTC)

    def test_no_live_resources(self):
        assert decide(_entry(lifecycle="temporary", expires_at=PAST), [])["status"] == "no_resources"


class TestTagsOnlyHoldBack:
    def test_resource_without_expiry_blocks(self):
        decision = decide(
            _entry(lifecycle="temporary", expires_at=PAST), [_resource("a", ExpiresAt=PAST), _resource("b")]
        )
        assert decision["status"] == "blocked"
        assert "instance/b: no ExpiresAt" in decision["reason"]

    def test_unreadable_resource_expiry_blocks(self):
        decision = decide(_entry(lifecycle="temporary", expires_at=PAST), [_resource("a", ExpiresAt="never")])
        assert decision["status"] == "blocked"

    def test_persistent_resource_blocks(self):
        decision = decide(
            _entry(lifecycle="temporary", expires_at=PAST), [_resource("a", ExpiresAt=PAST, Lifecycle="persistent")]
        )
        assert decision["status"] == "blocked"

    def test_later_resource_expiry_delays(self):
        decision = decide(_entry(lifecycle="temporary", expires_at=PAST), [_resource("a", ExpiresAt=FUTURE)])
        assert decision["status"] == "expires"
        assert decision["expires_at"] == parse_expires_at(FUTURE)

    def test_earlier_resource_expiry_changes_nothing(self):
        decision = decide(_entry(lifecycle="temporary", expires_at=FUTURE), [_resource("a", ExpiresAt=PAST)])
        assert decision["status"] == "expires"
        assert decision["expires_at"] == parse_expires_at(FUTURE)

    def test_conflicting_resource_expiries_block_and_are_reported(self):
        decision = decide(
            _entry(lifecycle="temporary", expires_at=PAST),
            [_resource("a", ExpiresAt=PAST), _resource("b", ExpiresAt="2021-06-01T00:00:00Z")],
        )
        assert decision["status"] == "blocked"
        assert "2020-01-01T00:00:00+00:00" in decision["reason"]
        assert "2021-06-01T00:00:00+00:00" in decision["reason"]


def _run_precheck(entry, resources):
    return reaper_index._jit_precheck(_FakeTagging(resources), entry, entry["repo_slug"])


class TestReaperPrecheck:
    def test_resource_tagged_with_a_persistent_stacks_repository_does_not_dispatch(self):
        # The persistent stack's own resources carry no expiry; one other
        # resource names the same repository with a past expiry.
        resources = [_resource("app"), _resource("db"), _resource("planted", ExpiresAt=PAST)]
        assert _run_precheck(_entry(), resources)["action"] == "skip"

    def test_planted_past_expiry_does_not_advance_a_temporary_stack(self):
        resources = [_resource("app", ExpiresAt=FUTURE), _resource("planted", ExpiresAt=PAST)]
        assert _run_precheck(_entry(lifecycle="temporary", expires_at=FUTURE), resources)["action"] == "skip"

    def test_expired_temporary_stack_proceeds(self):
        resources = [_resource("app", ExpiresAt=PAST), _resource("db", ExpiresAt=PAST)]
        assert _run_precheck(_entry(lifecycle="temporary", expires_at=PAST), resources)["action"] == "proceed"

    def test_future_expiry_skips(self):
        resources = [_resource("app", ExpiresAt=FUTURE)]
        assert _run_precheck(_entry(lifecycle="temporary", expires_at=FUTURE), resources)["action"] == "skip"

    def test_tag_api_error_skips(self):
        tagging = MagicMock()
        tagging.get_paginator.side_effect = RuntimeError("throttled")
        result = reaper_index._jit_precheck(tagging, _entry(lifecycle="temporary", expires_at=PAST), "payments")
        assert result["action"] == "skip"

    def test_handler_never_dispatches_a_persistent_stack(self, monkeypatch):
        dispatched = []
        monkeypatch.setattr(reaper_index, "DRY_RUN", False)
        monkeypatch.setattr(reaper_index, "load_iac_registry", lambda project, environment: [_entry()])
        boto = MagicMock()
        boto.client.return_value = _FakeTagging([_resource("planted", ExpiresAt=PAST)])
        monkeypatch.setattr(reaper_index, "boto3", boto)
        monkeypatch.setattr(reaper_index, "dispatch_teardown", lambda **kw: dispatched.append(kw) or {"success": True})
        result = reaper_index.handler({"stack_id": "payments", "scheduled_expires_at": PAST}, None)
        assert result["status"] == "skipped"
        assert dispatched == []


class TestReconciler:
    def _run(self, monkeypatch, entry, resources):
        upserts, states = [], []
        monkeypatch.setattr(reconciler_index, "load_iac_registry", lambda project, environment: [entry])
        monkeypatch.setattr(reconciler_index, "REAPER_FUNCTION_ARN", "arn:aws:lambda:eu-west-1:123456789012:function:r")
        boto = MagicMock()
        boto.client.side_effect = (
            lambda name, **kw: _FakeTagging(resources) if name == "resourcegroupstaggingapi" else MagicMock()
        )
        monkeypatch.setattr(reconciler_index, "boto3", boto)
        monkeypatch.setattr(reconciler_index, "_upsert_schedule", lambda *a: upserts.append(a))
        monkeypatch.setattr(reconciler_index, "_update_state", lambda table, slug, state: states.append(state))
        monkeypatch.setattr(reconciler_index, "_scan_failed_entries", lambda table: [])
        monkeypatch.setattr(reconciler_index, "_find_orphan_schedules", lambda *a: [])
        monkeypatch.setattr(reconciler_index, "_write_job_status", lambda *a: None)
        return reconciler_index.handler({}, None), upserts, states

    def test_persistent_stack_with_planted_expiry_is_not_scheduled(self, monkeypatch):
        stats, upserts, states = self._run(
            monkeypatch, _entry(), [_resource("app"), _resource("planted", ExpiresAt=PAST)]
        )
        assert upserts == []
        assert states[-1]["status"] == "persistent"

    def test_conflict_is_reported_not_scheduled(self, monkeypatch):
        stats, upserts, states = self._run(
            monkeypatch,
            _entry(lifecycle="temporary", expires_at=PAST),
            [_resource("a", ExpiresAt=PAST), _resource("b", ExpiresAt=FUTURE)],
        )
        assert upserts == []
        assert states[-1]["status"] == "blocked"
        assert stats["warnings"] == 1

    def test_temporary_stack_is_scheduled_at_its_expiry(self, monkeypatch):
        stats, upserts, states = self._run(
            monkeypatch, _entry(lifecycle="temporary", expires_at=FUTURE), [_resource("a", ExpiresAt=FUTURE)]
        )
        assert len(upserts) == 1
        assert upserts[0][2] == parse_expires_at(FUTURE)


class TestRepositoryValues:
    @pytest.mark.parametrize(
        "value",
        [
            "https://github.com/acme/payments",
            "https://GitHub.com/acme/payments/",
            "https://github.com/acme/payments.git",
            SCP,
            "ssh://" + "git" + "@" + "github.com/acme/payments.git",
            "http://github.com/acme/payments",
        ],
    )
    def test_spellings_normalize_to_one(self, value):
        assert normalize_repository(value) == "https://github.com/acme/payments"
        assert same_repository(value, REPO)

    @pytest.mark.parametrize(
        "value", ["", None, "https://github.com/acme/payments-old", "https://github.com/other/payments"]
    )
    def test_other_values_differ(self, value):
        assert not same_repository(value, REPO)

    def test_tag_query_finds_other_spellings_and_drops_lookalikes(self):
        resources = [
            {"arn": "arn:1", "tags": {"Repository": SCP, "ExpiresAt": PAST}},
            {"arn": "arn:2", "tags": {"Repository": REPO + ".git", "ExpiresAt": PAST}},
            {"arn": "arn:3", "tags": {"Repository": "https://github.com/acme/payments-old"}},
        ]
        found = resources_of_repository(_FakeTagging(resources), REPO)
        assert sorted(r["arn"] for r in found) == ["arn:1", "arn:2"]


class TestOneExpiryParser:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("2020-01-01T00:00:00Z", datetime(2020, 1, 1, tzinfo=UTC)),
            ("2020-01-01T02:00:00+02:00", datetime(2020, 1, 1, tzinfo=UTC)),
            ("2020-01-01", datetime(2020, 1, 1, tzinfo=UTC)),
            ("20200101t0000z", datetime(2020, 1, 1, tzinfo=UTC)),
        ],
    )
    def test_forms(self, value, expected):
        assert parse_expires_at(value) == expected

    @pytest.mark.parametrize("value", ["", "  ", "tomorrow", None, 5])
    def test_unreadable(self, value):
        assert parse_expires_at(value) is None

    def test_both_functions_use_it(self):
        import expiry_decision

        assert expiry_decision.parse_expires_at is parse_expires_at
        assert reconciler_index.parse_expires_at is parse_expires_at
        assert reaper_index.decide is expiry_decision.decide
        assert reconciler_index.decide is expiry_decision.decide

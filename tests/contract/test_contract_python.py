"""Tests for the generated Python contract module and the published JSON of each cloud."""

import json
import os
import re
from datetime import UTC, datetime

import compliance_contract as contract
import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _published(cloud):
    with open(os.path.join(_ROOT, "contract", "dist", f"compliance-contract.{cloud}.json"), encoding="utf-8") as f:
        return json.load(f)


class TestNormalizeRepository:
    @pytest.mark.parametrize(
        "value",
        [
            "https://git.example/example-org/example-repo",
            "https://git.example/example-org/example-repo/",
            "https://git.example/example-org/example-repo.git",
            "https://git.example/example-org/example-repo.git/",
            "https://GIT.Example/example-org/example-repo",
            "https://user@git.example/example-org/example-repo.git",
            "http://git.example/example-org/example-repo",
            "git@git.example:example-org/example-repo.git",
            "git@git.example:example-org/example-repo",
            "git@GIT.EXAMPLE:example-org/example-repo.git",
            "ssh://git@git.example/example-org/example-repo.git",
            "ssh://git@git.example:22/example-org/example-repo.git",
            "  https://git.example/example-org/example-repo  ",
        ],
    )
    def test_every_form_of_one_repository_is_the_canonical_form(self, value):
        assert contract.normalize_repository(value) == "https://git.example/example-org/example-repo"

    def test_path_case_is_kept(self):
        assert (
            contract.normalize_repository("git@git.example:Example-Org/Repo.git")
            == "https://git.example/Example-Org/Repo"
        )

    def test_https_port_is_kept(self):
        assert (
            contract.normalize_repository("https://git.example:8443/org/repo.git")
            == "https://git.example:8443/org/repo"
        )

    def test_nested_groups(self):
        assert (
            contract.normalize_repository("git@git.example:group/sub/repo.git") == "https://git.example/group/sub/repo"
        )

    @pytest.mark.parametrize("value", ["", None, "   "])
    def test_empty(self, value):
        assert contract.normalize_repository(value) == ""

    def test_a_value_that_is_not_an_address_is_returned_unchanged(self):
        assert contract.normalize_repository(" example-repo ") == "example-repo"

    def test_idempotent(self):
        once = contract.normalize_repository("git@git.example:example-org/example-repo.git")
        assert contract.normalize_repository(once) == once

    def test_canonical_form_matches_the_repository_pattern(self):
        import re

        assert re.match(contract.REPOSITORY_PATTERN, contract.normalize_repository("git@git.example:org/repo.git"))

    def test_same_repository(self):
        assert contract.same_repository("git@git.example:org/repo.git", "https://git.example/org/repo")
        assert not contract.same_repository("https://git.example/org/repo", "https://git.example/org/other")
        assert not contract.same_repository("", "")
        assert not contract.same_repository(None, "https://git.example/org/repo")


class TestExpiresAt:
    @pytest.mark.parametrize("value", ["2026-12-01T00:00:00Z", "2026-12-01T00:00Z"])
    def test_contract_form_is_valid(self, value):
        assert contract.expires_at_is_valid(value)
        assert contract.parse_expires_at(value) == datetime(2026, 12, 1, tzinfo=UTC)

    @pytest.mark.parametrize(
        "value",
        ["2026-12-01", "2026-12-01T00:00:00", "20261201t0000z", "2026-12-01T00:00:00+00:00", "2026-12-01 00:00:00Z"],
    )
    def test_other_readable_forms_are_invalid_and_still_parsed_as_utc(self, value):
        assert not contract.expires_at_is_valid(value)
        assert contract.parse_expires_at(value) == datetime(2026, 12, 1, tzinfo=UTC)

    def test_offset_is_converted_to_utc(self):
        assert contract.parse_expires_at("2026-12-01T02:00:00+02:00") == datetime(2026, 12, 1, tzinfo=UTC)

    @pytest.mark.parametrize("value", ["", None, "next week", "2026-13-40", "20261340t0000z", 20261201])
    def test_values_that_name_no_moment(self, value):
        assert contract.parse_expires_at(value) is None
        assert not contract.expires_at_is_valid(value)

    def test_trailing_text_is_invalid(self):
        assert not contract.expires_at_is_valid("2026-12-01T00:00:00Z ")
        assert not contract.expires_at_is_valid("2026-12-01T00:00:00Z\n")


class TestConstants:
    def test_expiry_required_lifecycles(self):
        assert contract.EXPIRY_REQUIRED_LIFECYCLES == ["temporary", "experiment"]
        assert contract.expiry_required("temporary") and contract.expiry_required("experiment")
        assert not contract.expiry_required("persistent") and not contract.expiry_required("")

    def test_protected_and_schedule_keys(self):
        assert contract.TAG_KEYS["protected"] == "Protected"
        assert contract.TAG_KEYS["schedule"] == "Schedule"
        assert contract.ALLOWED_VALUES["protected"] == ["true"]

    def test_environment(self):
        assert contract.ENVIRONMENT_ALLOWED == []
        assert contract.PROTECTED_ENVIRONMENTS == ["prod", "production"]

    def test_managed_by(self):
        assert "opentofu" in contract.VALID_MANAGED_BY and "infrastructure-manager" in contract.VALID_MANAGED_BY
        assert "deployment-manager" not in contract.VALID_MANAGED_BY


class TestPublishedJson:
    NORMATIVE = ("required_facts", "keys", "allowed", "patterns")
    LEGACY = ("required_tags", "valid_managed_by", "valid_lifecycle", "tag_validation")

    @pytest.mark.parametrize("platform", ["aws", "azure", "gcp"])
    def test_normative_fields_on_every_platform(self, platform):
        published = _published(platform)
        for field in (*self.NORMATIVE, "carriers", "unit", "rules", "protected_values", "version", "platform"):
            assert field in published, field
        assert "kinds" not in published and "cloud" not in published
        assert published["platform"] == platform
        assert published["version"] == "1.0"

    def test_platform_neutral_fields_are_equal_on_every_platform(self):
        aws, azure, gcp = (_published(platform) for platform in ("aws", "azure", "gcp"))
        for field in ("allowed", "rules", "protected_values"):
            assert aws[field] == azure[field] == gcp[field], field
        # a rendering may override the required set: Google Cloud requires owner and not repository
        assert aws["required_facts"] == azure["required_facts"]
        assert gcp["required_facts"] == ["project", "environment", "managed_by", "lifecycle", "owner"]
        assert aws["patterns"]["repository"] == azure["patterns"]["repository"] == gcp["patterns"]["repository"]

    def test_expires_at_pattern_follows_the_encoding_of_the_cloud(self):
        iso = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$"
        assert _published("aws")["patterns"]["expires_at"] == iso
        assert _published("azure")["patterns"]["expires_at"] == iso
        assert _published("gcp")["patterns"]["expires_at"] == r"^\d{8}t\d{4}z$"

    def test_only_the_aws_file_carries_the_legacy_aliases(self):
        aws = _published("aws")
        for field in self.LEGACY:
            assert field in aws
            assert field not in _published("azure")
            assert field not in _published("gcp")
        assert aws["required_tags"] == [aws["keys"][fact] for fact in aws["required_facts"]]
        assert aws["valid_managed_by"] == aws["allowed"]["managed_by"]
        assert aws["valid_lifecycle"] == aws["allowed"]["lifecycle"]

    def test_rules(self):
        assert _published("aws")["rules"] == [
            {
                "id": "temporary-requires-expires-at",
                "when": {"fact": "lifecycle", "equals": "temporary"},
                "require": ["expires_at"],
            },
            {
                "id": "experiment-requires-expires-at",
                "when": {"fact": "lifecycle", "equals": "experiment"},
                "require": ["expires_at"],
            },
        ]

    def test_protected_and_schedule_keys_per_cloud(self):
        assert _published("aws")["keys"]["protected"] == _published("azure")["keys"]["protected"] == "Protected"
        assert _published("aws")["keys"]["schedule"] == _published("azure")["keys"]["schedule"] == "Schedule"
        assert _published("gcp")["keys"]["protected"] == "protected"
        assert _published("gcp")["keys"]["schedule"] == "schedule"
        for cloud in ("aws", "azure", "gcp"):
            assert _published(cloud)["allowed"]["protected"] == ["true"]

    def test_gcp_contact_is_carried_by_the_registry_entry(self):
        gcp = _published("gcp")
        assert "contact" not in gcp["keys"]
        assert gcp["carriers"]["contact"] == "registry-entry"
        assert _published("aws")["carriers"]["contact"] == "tag"


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("https://git.example/example-org/example-repo", True),
        ("https://git.example:8443/example-org/example-repo", True),
        ("git@git.example:example-org/example-repo.git", True),
        ("https://user:token@git.example/example-org/example-repo", False),
        ("https://token@git.example/example-org/example-repo", False),
        ("ssh://git@git.example/example-org/example-repo.git", False),
        ("https://git.example", False),
    ],
)
def test_repository_pattern_refuses_user_info(value, accepted):
    """A repository value never carries a user name, password or token."""
    assert bool(re.match(contract.REPOSITORY_PATTERN, value)) is accepted

"""Inputs with no default, and values that are unknown outside the region a table describes."""

import pytest
from compliance_schema import PRICE_TABLE_REGION, repo_slug, require_env
from factories import make_resource
from utils import aws_resources
from utils.aws_resources import _enrich_costs, _infer_purpose

OTHER_REGION = "ap-southeast-2"


class TestRequireEnv:
    def test_returns_the_value(self, monkeypatch):
        monkeypatch.setenv("SOME_INPUT", " value ")
        assert require_env("SOME_INPUT") == "value"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_unset_or_empty_stops_with_the_name(self, monkeypatch, value):
        if value is None:
            monkeypatch.delenv("SOME_INPUT", raising=False)
        else:
            monkeypatch.setenv("SOME_INPUT", value)
        with pytest.raises(RuntimeError, match="SOME_INPUT"):
            require_env("SOME_INPUT")


class TestRepoSlug:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/acme/example-repo",
            "https://github.com/acme/example-repo/",
            "https://github.com/acme/example-repo.git",
            "ssh://git.example.com:acme/example-repo.git",
        ],
    )
    def test_last_path_segment(self, url):
        assert repo_slug(url) == "example-repo"


def _priced(resource_type, region, **fields):
    resource = make_resource(resource_type=resource_type, region=region, estimated_monthly_cost=None, **fields)
    _enrich_costs([resource], {})
    return resource["estimated_monthly_cost"]


@pytest.fixture(autouse=True)
def _no_pricing_api(monkeypatch):
    monkeypatch.setattr(aws_resources, "get_ec2_monthly_cost", lambda itype, region, state: None)
    monkeypatch.setattr(aws_resources, "flush_cache", lambda: None)


class TestPriceTableRegion:
    @pytest.mark.parametrize(
        "resource_type, fields",
        [
            ("ec2:volume", {"size_gb": 100}),
            ("ec2:natgateway", {}),
            ("rds:db", {"instance_class": "db.t3.micro"}),
            ("ecs:service", {}),
            ("elasticache:cluster", {}),
            ("elasticloadbalancing:loadbalancer", {}),
            ("logs:log-group", {"stored_bytes": 5 * 1024**3}),
            ("efs:filesystem", {"size_bytes_standard": 1024**3}),
            ("s3:bucket", {"size_bytes": 1024**3}),
        ],
    )
    def test_region_priced_type_is_unknown_elsewhere(self, resource_type, fields):
        assert _priced(resource_type, PRICE_TABLE_REGION, **fields) is not None
        assert _priced(resource_type, OTHER_REGION, **fields) is None

    def test_ec2_without_a_rate_for_its_region_is_unknown(self):
        assert _priced("ec2:instance", PRICE_TABLE_REGION, instance_type="t3.micro", state="running") == 10.51
        assert _priced("ec2:instance", OTHER_REGION, instance_type="t3.micro", state="running") is None

    @pytest.mark.parametrize("resource_type, cost", [("secretsmanager:secret", 0.40), ("kms:key", 1.00)])
    def test_flat_rate_applies_in_every_region(self, resource_type, cost):
        assert _priced(resource_type, OTHER_REGION) == cost


class TestPurposeHints:
    def test_no_hints_by_default(self, monkeypatch):
        monkeypatch.delenv("PURPOSE_HINTS", raising=False)
        monkeypatch.setattr(aws_resources, "_purpose_hints_cache", None)
        resource = make_resource(name="wiki-server", tags={}, inferred_purpose=None)
        assert _infer_purpose(resource) == f"{resource['resource_type']}: wiki-server"

    def test_hint_from_the_deployment(self, monkeypatch):
        monkeypatch.setenv("PURPOSE_HINTS", '{"Wiki": "Team wiki"}')
        monkeypatch.setattr(aws_resources, "_purpose_hints_cache", None)
        assert _infer_purpose(make_resource(name="wiki-server", tags={}, inferred_purpose=None)) == "Team wiki"

    def test_invalid_json_is_ignored(self, monkeypatch):
        monkeypatch.setenv("PURPOSE_HINTS", "{not json")
        monkeypatch.setattr(aws_resources, "_purpose_hints_cache", None)
        assert aws_resources._purpose_hints() == {}

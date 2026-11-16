"""Tests for hunterkiller index.py — _is_protected.

Uses hk_index fixture to avoid collision with scanner's index.py.
"""

from factories import make_finding


def _finding_with(**overrides):
    """Build a finding dict for _is_protected testing."""
    base = make_finding()
    base.update(overrides)
    return base


class TestIsProtected:
    def test_protected_resource_type(self, hk_index):
        f = _finding_with(resource_type="route53:hostedzone")
        assert hk_index._is_protected(f, None, "123456789012") is True

    def test_payments_type_protected(self, hk_index):
        f = _finding_with(resource_type="payments:payment-instrument")
        assert hk_index._is_protected(f, None, "123456789012") is True

    def test_environment_prod_tag(self, hk_index):
        f = _finding_with(tags={"Environment": "prod"})
        assert hk_index._is_protected(f, None, "123456789012") is True

    def test_environment_production_tag(self, hk_index):
        f = _finding_with(tags={"Environment": "production"})
        assert hk_index._is_protected(f, None, "123456789012") is True

    def test_protected_true_tag(self, hk_index):
        f = _finding_with(tags={"Protected": "true"})
        assert hk_index._is_protected(f, None, "123456789012") is True

    def test_protected_yes_tag(self, hk_index):
        f = _finding_with(tags={"Protected": "yes"})
        assert hk_index._is_protected(f, None, "123456789012") is True

    def test_protected_1_tag(self, hk_index):
        f = _finding_with(tags={"Protected": "1"})
        assert hk_index._is_protected(f, None, "123456789012") is True

    def test_ssm_config_arn_match(self, hk_index):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-protected"
        config = {"resources": [{"arn_pattern": arn}]}
        f = _finding_with(resource_arn=arn)
        assert hk_index._is_protected(f, config, "123456789012") is True

    def test_ssm_config_wildcard_resolved(self, hk_index):
        config = {"resources": [{"arn_pattern": "arn:aws:ec2:eu-central-1:*:instance/i-wild"}]}
        f = _finding_with(resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-wild")
        assert hk_index._is_protected(f, config, "123456789012") is True

    def test_no_match_returns_false(self, hk_index):
        f = _finding_with(
            resource_type="ec2:instance",
            tags={"Environment": "dev"},
            resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-normal",
        )
        assert hk_index._is_protected(f, None, "123456789012") is False

    def test_no_config_no_tags_not_protected(self, hk_index):
        f = _finding_with(
            resource_type="ec2:instance",
            tags={},
            resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-orphan",
        )
        assert hk_index._is_protected(f, None, "123456789012") is False

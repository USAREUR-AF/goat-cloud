"""Tests for utils/exclusions.py — ExclusionEngine logic."""

from factories import make_resource
from utils.exclusions import ExclusionEngine


def _engine(**kwargs):
    """Create an ExclusionEngine with defaults."""
    defaults = {
        "explicit_arns": [],
        "patterns": [],
        "tf_arns": set(),
        "account_id": "123456789012",
    }
    defaults.update(kwargs)
    return ExclusionEngine(**defaults)


class TestShouldExclude:
    def test_explicit_arn_match(self):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-exclude"
        engine = _engine(explicit_arns=[arn])
        r = make_resource(arn=arn)
        excluded, _reason = engine.should_exclude(r)
        assert excluded is True

    def test_explicit_arn_no_match(self):
        engine = _engine(explicit_arns=["arn:aws:ec2:eu-central-1:123456789012:instance/i-other"])
        r = make_resource()
        excluded, _ = engine.should_exclude(r)
        assert excluded is False

    def test_service_linked_role_auto_excluded(self):
        r = make_resource(
            arn="arn:aws:iam::123456789012:role/aws-service-role/ecs.amazonaws.com/AWSServiceRoleForECS",
            resource_type="iam:role",
            name="AWSServiceRoleForECS",
        )
        engine = _engine()
        excluded, _reason = engine.should_exclude(r)
        assert excluded is True

    def test_role_named_like_a_service_role_outside_the_service_path_is_not_excluded(self):
        r = make_resource(
            arn="arn:aws:iam::123456789012:role/AWSServiceRoleBackdoor",
            resource_type="iam:role",
            name="AWSServiceRoleBackdoor",
        )
        excluded, _reason = _engine().should_exclude(r)
        assert excluded is False

    def test_default_security_group_excluded(self):
        r = make_resource(
            arn="arn:aws:ec2:eu-central-1:123456789012:security-group/sg-default",
            resource_type="ec2:security-group",
            name="default",
        )
        engine = _engine()
        excluded, _reason = engine.should_exclude(r)
        assert excluded is True

    def test_default_vpc_excluded(self):
        r = make_resource(
            arn="arn:aws:ec2:eu-central-1:123456789012:vpc/vpc-default",
            resource_type="ec2:vpc",
            name="default",
            is_default=True,
        )
        engine = _engine()
        excluded, _reason = engine.should_exclude(r)
        assert excluded is True

    def test_custom_pattern_match(self):
        engine = _engine(patterns=[".*test-pattern.*"])
        r = make_resource(arn="arn:aws:ec2:eu-central-1:123456789012:instance/test-pattern-123")
        excluded, _reason = engine.should_exclude(r)
        assert excluded is True

    def test_no_match_returns_false(self):
        engine = _engine()
        r = make_resource(
            arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-normal",
            name="normal-instance",
            resource_type="ec2:instance",
            is_default=False,
        )
        excluded, _ = engine.should_exclude(r)
        assert excluded is False

    def test_override_arn_excluded(self):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-override"
        engine = _engine()
        engine.override_arns = {arn}
        r = make_resource(arn=arn)
        excluded, _reason = engine.should_exclude(r)
        assert excluded is True


class TestClassifyConfidence:
    def test_in_terraform_returns_none(self):
        engine = _engine()
        r = make_resource()
        assert engine.classify_confidence(r, in_terraform_state=True) is None

    def test_high_confidence_type_with_name(self):
        engine = _engine()
        r = make_resource(
            resource_type="ec2:instance",
            name="my-instance",
            tags={"Name": "my-instance"},
        )
        confidence = engine.classify_confidence(r, in_terraform_state=False)
        assert confidence == "HIGH"

    def test_low_confidence_no_name_no_tags(self):
        engine = _engine()
        r = make_resource(
            resource_type="ec2:network-interface",
            name="",
            tags={},
            is_default=False,
        )
        confidence = engine.classify_confidence(r, in_terraform_state=False)
        assert confidence in ("LOW", None)

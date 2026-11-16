"""Tests for scanners/drift_detector.py — pure drift detection logic."""

from unittest.mock import MagicMock

from factories import make_resource
from scanners.drift_detector import (
    _extract_id_from_arn,
    _is_in_terraform_state,
    _recommendation,
    scan_drift,
)


def _mock_exclusion_engine(should_exclude=False, confidence="HIGH"):
    """Build a mock ExclusionEngine."""
    engine = MagicMock()
    engine.should_exclude.return_value = (should_exclude, "test" if should_exclude else None)
    engine.classify_confidence.return_value = confidence
    return engine


class TestScanDrift:
    def test_resource_in_state(self):
        r = make_resource()
        tf_arns = {r["arn"]}
        result = scan_drift([r], tf_arns, _mock_exclusion_engine())
        assert result["terraform_managed"] == 1
        assert result["unmanaged"] == 0
        assert len(result["findings"]) == 0

    def test_resource_not_in_state(self):
        r = make_resource(tags={})
        tf_arns = set()
        result = scan_drift([r], tf_arns, _mock_exclusion_engine())
        assert result["terraform_managed"] == 0
        assert result["unmanaged"] == 1
        assert len(result["findings"]) == 1
        assert result["findings"][0]["category"] == "drift_detection"

    def test_excluded_resource_skipped(self):
        r = make_resource(tags={})
        tf_arns = set()
        result = scan_drift([r], tf_arns, _mock_exclusion_engine(should_exclude=True))
        assert result["terraform_managed"] == 0
        assert result["unmanaged"] == 0
        assert len(result["findings"]) == 0

    def test_confidence_none_skips_finding(self):
        r = make_resource(tags={})
        tf_arns = set()
        result = scan_drift([r], tf_arns, _mock_exclusion_engine(confidence=None))
        assert result["unmanaged"] == 1
        assert len(result["findings"]) == 0

    def test_empty_tf_arns(self):
        r = make_resource(tags={})
        result = scan_drift([r], set(), _mock_exclusion_engine())
        assert result["unmanaged"] == 1


class TestIsInTerraformState:
    def test_direct_match(self):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-abc123"
        assert _is_in_terraform_state(arn, {}, {arn}) is True

    def test_normalized_match_strips_star(self):
        arn = "arn:aws:iam::123456789012:role/my-role:*"
        tf_arns = {"arn:aws:iam::123456789012:role/my-role"}
        assert _is_in_terraform_state(arn, {}, tf_arns) is True

    def test_resource_id_from_arn(self):
        arn = "arn:aws:ec2:eu-central-1:123456789012:security-group/sg-abc123"
        tf_arns = {"sg-abc123"}
        assert _is_in_terraform_state(arn, {}, tf_arns) is True

    def test_secrets_manager_single_strip(self):
        arn = "arn:aws:secretsmanager:eu-central-1:123456789012:secret:my-secret-AbCdEf"
        tf_arns = {"arn:aws:secretsmanager:eu-central-1:123456789012:secret:my-secret"}
        resource = {"resource_type": "secretsmanager:secret"}
        assert _is_in_terraform_state(arn, resource, tf_arns) is True

    def test_secrets_manager_double_strip(self):
        arn = "arn:aws:secretsmanager:eu-central-1:123456789012:secret:my-secret-a1b2c3d4-AbCdEf"
        tf_arns = {"arn:aws:secretsmanager:eu-central-1:123456789012:secret:my-secret"}
        resource = {"resource_type": "secretsmanager:secret"}
        assert _is_in_terraform_state(arn, resource, tf_arns) is True

    def test_s3_bucket_name_match(self):
        arn = "arn:aws:s3:::my-bucket"
        tf_arns = {"arn:aws:s3:::my-bucket"}
        resource = {"resource_type": "s3:bucket", "name": "my-bucket"}
        assert _is_in_terraform_state(arn, resource, tf_arns) is True

    def test_no_match(self):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-orphan"
        assert _is_in_terraform_state(arn, {}, set()) is False

    def test_empty_tf_arns_returns_false(self):
        assert _is_in_terraform_state("arn:aws:ec2:eu-central-1:123456789012:instance/i-x", {}, set()) is False


class TestExtractIdFromArn:
    def test_with_slash(self):
        assert _extract_id_from_arn("arn:aws:ec2:eu-central-1:123456789012:instance/i-abc123") == "i-abc123"

    def test_nested_slashes(self):
        assert _extract_id_from_arn("arn:aws:ec2:eu-central-1:123456789012:security-group/sg-abc") == "sg-abc"

    def test_no_slash(self):
        assert _extract_id_from_arn("arn:aws:sns:eu-central-1:123456789012:my-topic") is None

    def test_short_arn(self):
        assert _extract_id_from_arn("arn:aws:s3") is None


class TestRecommendation:
    def test_high_ec2(self):
        rec = _recommendation("ec2:instance", "HIGH")
        assert "terminate" in rec.lower() or "import" in rec.lower()

    def test_high_rds(self):
        rec = _recommendation("rds:instance", "HIGH")
        assert "decommission" in rec.lower() or "import" in rec.lower()

    def test_medium(self):
        rec = _recommendation("ec2:volume", "MEDIUM")
        assert "review" in rec.lower() or "verify" in rec.lower()

    def test_low(self):
        rec = _recommendation("ec2:network-interface", "LOW")
        assert "low priority" in rec.lower() or "verify" in rec.lower()

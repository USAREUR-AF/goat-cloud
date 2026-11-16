"""Tests for utils/state_parser.py — Terraform state ARN extraction."""

import pytest
from utils.state_parser import (
    _extract_arns_from_state,
    _looks_like_resource_id,
)


class TestExtractArnsFromState:
    def test_v4_format(self):
        state = {
            "version": 4,
            "resources": [
                {
                    "type": "aws_instance",
                    "instances": [
                        {
                            "attributes": {
                                "arn": "arn:aws:ec2:eu-central-1:123456789012:instance/i-abc123",
                                "id": "i-abc123",
                            }
                        }
                    ],
                }
            ],
        }
        arns = set()
        _extract_arns_from_state(state, arns)
        assert "arn:aws:ec2:eu-central-1:123456789012:instance/i-abc123" in arns

    def test_v3_format(self):
        state = {
            "version": 3,
            "modules": [
                {
                    "resources": {
                        "aws_instance.main": {
                            "primary": {
                                "attributes": {
                                    "arn": "arn:aws:ec2:eu-central-1:123456789012:instance/i-v3test",
                                }
                            }
                        }
                    }
                }
            ],
        }
        arns = set()
        _extract_arns_from_state(state, arns)
        assert "arn:aws:ec2:eu-central-1:123456789012:instance/i-v3test" in arns

    def test_arn_normalization_strips_trailing_star(self):
        state = {
            "version": 4,
            "resources": [
                {
                    "type": "aws_iam_role",
                    "instances": [{"attributes": {"arn": "arn:aws:iam::123456789012:role/my-role:*"}}],
                }
            ],
        }
        arns = set()
        _extract_arns_from_state(state, arns)
        assert "arn:aws:iam::123456789012:role/my-role" in arns

    def test_secrets_manager_single_strip(self):
        state = {
            "version": 4,
            "resources": [
                {
                    "type": "aws_secretsmanager_secret",
                    "instances": [
                        {
                            "attributes": {
                                "arn": "arn:aws:secretsmanager:eu-central-1:123456789012:secret:my-secret-AbCdEf"
                            }
                        }
                    ],
                }
            ],
        }
        arns = set()
        _extract_arns_from_state(state, arns)
        assert "arn:aws:secretsmanager:eu-central-1:123456789012:secret:my-secret" in arns

    def test_secrets_manager_double_strip(self):
        state = {
            "version": 4,
            "resources": [
                {
                    "type": "aws_secretsmanager_secret",
                    "instances": [
                        {
                            "attributes": {
                                "arn": "arn:aws:secretsmanager:eu-central-1:123456789012:secret:my-secret-a1b2c3d4-AbCdEf"
                            }
                        }
                    ],
                }
            ],
        }
        arns = set()
        _extract_arns_from_state(state, arns)
        # Should have both single-stripped and double-stripped
        assert "arn:aws:secretsmanager:eu-central-1:123456789012:secret:my-secret-a1b2c3d4" in arns
        assert "arn:aws:secretsmanager:eu-central-1:123456789012:secret:my-secret" in arns

    def test_empty_state(self):
        arns = set()
        _extract_arns_from_state({}, arns)
        assert len(arns) == 0

    def test_malformed_state_no_crash(self):
        arns = set()
        _extract_arns_from_state({"version": 4}, arns)
        assert len(arns) == 0


class TestLooksLikeResourceId:
    @pytest.mark.parametrize(
        "key,value,expected",
        [
            ("security_group_id", "sg-abc123", True),
            ("vpc_id", "vpc-abc123", True),
            ("subnet_id", "subnet-abc123", True),
            ("id", "i-abc123", True),
            ("name", "my-resource", False),
            ("description", "some desc", False),
            ("tags", "Project", False),
        ],
    )
    def test_known_patterns(self, key, value, expected):
        assert _looks_like_resource_id(key, value) == expected


class TestStateSizeLimit:
    def _s3(self, body, content_length=None):
        import io
        from unittest.mock import MagicMock

        s3 = MagicMock()
        response = {"Body": io.BytesIO(body)}
        if content_length is not None:
            response["ContentLength"] = content_length
        s3.get_object.return_value = response
        return s3

    def test_object_within_limit_is_parsed(self):
        from utils.state_parser import read_json_object

        assert read_json_object(self._s3(b'{"a": 1}', 8), "b", "k", max_bytes=100) == {"a": 1}

    def test_declared_size_above_limit_is_refused_without_reading(self):
        import pytest
        from utils.state_parser import StateTooLarge, read_json_object

        s3 = self._s3(b"{}", content_length=10_000)
        with pytest.raises(StateTooLarge):
            read_json_object(s3, "b", "k", max_bytes=100)
        assert s3.get_object.return_value["Body"].tell() == 0

    def test_body_above_limit_is_refused(self):
        import pytest
        from utils.state_parser import StateTooLarge, read_json_object

        with pytest.raises(StateTooLarge):
            read_json_object(self._s3(b'"' + b"x" * 500 + b'"'), "b", "k", max_bytes=100)

    def test_oversized_state_yields_no_arns(self):
        from unittest.mock import patch

        from utils import state_parser

        with (
            patch.object(state_parser.boto3, "client", return_value=self._s3(b"{}", content_length=10**12)),
        ):
            assert state_parser.load_terraform_state_arns("b", "k") == set()

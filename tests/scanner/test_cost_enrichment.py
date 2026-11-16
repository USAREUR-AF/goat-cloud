"""Tests for _enrich_costs() — storage-based cost estimation for S3, EFS, ECR, CloudWatch Logs."""

from factories import make_resource
from utils.aws_resources import _enrich_costs


def _make_storage_resource(resource_type, **overrides):
    """Build a resource with estimated_monthly_cost=None for cost enrichment."""
    return make_resource(
        resource_type=resource_type,
        estimated_monthly_cost=None,
        **overrides,
    )


class TestCloudWatchLogsCost:
    def test_log_group_with_stored_bytes(self):
        r = _make_storage_resource("logs:log-group", stored_bytes=10 * 1024**3)  # 10 GB
        _enrich_costs([r], {})
        # 10 GB * $0.0314/GB = $0.31
        assert r["estimated_monthly_cost"] == 0.31

    def test_log_group_zero_bytes(self):
        r = _make_storage_resource("logs:log-group", stored_bytes=0)
        _enrich_costs([r], {})
        assert r["estimated_monthly_cost"] == 0

    def test_log_group_missing_stored_bytes(self):
        r = _make_storage_resource("logs:log-group")
        _enrich_costs([r], {})
        assert r["estimated_monthly_cost"] == 0

    def test_log_group_none_stored_bytes(self):
        r = _make_storage_resource("logs:log-group", stored_bytes=None)
        _enrich_costs([r], {})
        assert r["estimated_monthly_cost"] == 0


class TestEfsCost:
    def test_efs_standard_only(self):
        r = _make_storage_resource(
            "efs:filesystem",
            size_bytes_standard=1 * 1024**3,  # 1 GB Standard
            size_bytes_ia=0,
        )
        _enrich_costs([r], {})
        # 1 GB * $0.33/GB = $0.33
        assert r["estimated_monthly_cost"] == 0.33

    def test_efs_ia_only(self):
        r = _make_storage_resource(
            "efs:filesystem",
            size_bytes_standard=0,
            size_bytes_ia=100 * 1024**3,  # 100 GB IA
        )
        _enrich_costs([r], {})
        # 100 GB * $0.0205/GB = $2.05
        assert r["estimated_monthly_cost"] == 2.05

    def test_efs_mixed_storage(self):
        r = _make_storage_resource(
            "efs:filesystem",
            size_bytes_standard=1 * 1024**3,
            size_bytes_ia=10 * 1024**3,
        )
        _enrich_costs([r], {})
        # 1*0.33 + 10*0.0205 = 0.33 + 0.205 = 0.535 → 0.54
        assert r["estimated_monthly_cost"] == 0.54

    def test_efs_empty(self):
        r = _make_storage_resource("efs:filesystem", size_bytes_standard=0, size_bytes_ia=0)
        _enrich_costs([r], {})
        assert r["estimated_monthly_cost"] == 0

    def test_efs_missing_fields(self):
        r = _make_storage_resource("efs:filesystem")
        _enrich_costs([r], {})
        assert r["estimated_monthly_cost"] == 0


class TestEcrCost:
    def test_ecr_with_images(self):
        r = _make_storage_resource("ecr:repository", size_bytes=5 * 1024**3)  # 5 GB
        _enrich_costs([r], {})
        # 5 GB * $0.10/GB = $0.50
        assert r["estimated_monthly_cost"] == 0.50

    def test_ecr_empty_repo(self):
        r = _make_storage_resource("ecr:repository", size_bytes=0)
        _enrich_costs([r], {})
        assert r["estimated_monthly_cost"] == 0

    def test_ecr_missing_size(self):
        r = _make_storage_resource("ecr:repository")
        _enrich_costs([r], {})
        assert r["estimated_monthly_cost"] == 0

    def test_ecr_none_size(self):
        r = _make_storage_resource("ecr:repository", size_bytes=None)
        _enrich_costs([r], {})
        assert r["estimated_monthly_cost"] == 0


class TestS3Cost:
    def test_s3_with_size(self):
        r = _make_storage_resource("s3:bucket", size_bytes=100 * 1024**3)  # 100 GB
        _enrich_costs([r], {})
        # 100 GB * $0.0245/GB = $2.45
        assert r["estimated_monthly_cost"] == 2.45

    def test_s3_small_bucket(self):
        r = _make_storage_resource("s3:bucket", size_bytes=1 * 1024**3)  # 1 GB
        _enrich_costs([r], {})
        # 1 GB * $0.0245/GB = $0.02 (rounded)
        assert r["estimated_monthly_cost"] == 0.02

    def test_s3_no_metric_data(self):
        """When CloudWatch returned no data, size_bytes is None — cost stays None (unknown)."""
        r = _make_storage_resource("s3:bucket", size_bytes=None)
        _enrich_costs([r], {})
        assert r["estimated_monthly_cost"] is None

    def test_s3_missing_size_bytes(self):
        """When size_bytes key is absent — cost stays None (unknown)."""
        r = _make_storage_resource("s3:bucket")
        _enrich_costs([r], {})
        assert r["estimated_monthly_cost"] is None

    def test_s3_empty_bucket(self):
        r = _make_storage_resource("s3:bucket", size_bytes=0)
        _enrich_costs([r], {})
        assert r["estimated_monthly_cost"] == 0


class TestExistingCostNotOverwritten:
    def test_pre_set_cost_preserved(self):
        """Resources with costs set during discovery should not be overwritten."""
        r = make_resource(
            resource_type="s3:bucket",
            estimated_monthly_cost=99.99,
            size_bytes=1 * 1024**3,
        )
        _enrich_costs([r], {})
        assert r["estimated_monthly_cost"] == 99.99

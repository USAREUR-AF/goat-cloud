"""Tests for views_burn.py — burn page rendering with storage-costed resources."""

from factories import make_report, make_resource
from views_burn import (
    _FIXED_COST_TOTAL,
    _aggregate,
    _envelope_estimates,
    _render_uncalculated_costs,
    render_burn,
)


def _storage_resource(resource_type, cost, **extra):
    return make_resource(
        resource_type=resource_type,
        estimated_monthly_cost=cost,
        **extra,
    )


class TestAggregate:
    def test_includes_storage_costs_in_total(self):
        """S3, EFS, ECR, CW Logs costs contribute to total estimated spend."""
        report = make_report()
        report["discovered_resources"] = [
            _storage_resource("s3:bucket", 2.45, arn="arn:aws:s3:::my-bucket"),
            _storage_resource(
                "efs:filesystem", 0.33, arn="arn:aws:elasticfilesystem:eu-central-1:123:file-system/fs-1"
            ),
            _storage_resource("ecr:repository", 0.50, arn="arn:aws:ecr:eu-central-1:123:repository/app"),
            _storage_resource("logs:log-group", 0.31, arn="arn:aws:logs:eu-central-1:123:log-group:/ecs/app"),
        ]
        agg = _aggregate(report)
        assert agg["total_estimated"] == 2.45 + 0.33 + 0.50 + 0.31 + _FIXED_COST_TOTAL

    def test_zero_cost_storage_resources(self):
        """Empty storage resources contribute $0 (plus fixed costs)."""
        report = make_report()
        report["discovered_resources"] = [
            _storage_resource("s3:bucket", 0, arn="arn:aws:s3:::empty-bucket"),
            _storage_resource("ecr:repository", 0, arn="arn:aws:ecr:eu-central-1:123:repository/empty"),
        ]
        agg = _aggregate(report)
        assert agg["total_estimated"] == _FIXED_COST_TOTAL


class TestRenderBurn:
    def test_renders_without_error(self):
        """render_burn produces HTML with storage-costed resources."""
        report = make_report()
        report["discovered_resources"].extend(
            [
                _storage_resource("s3:bucket", 2.45, arn="arn:aws:s3:::data-bucket", name="data-bucket"),
                _storage_resource(
                    "efs:filesystem", 0.33, arn="arn:aws:elasticfilesystem:eu-central-1:123:file-system/fs-1"
                ),
                _storage_resource("ecr:repository", 0.50, arn="arn:aws:ecr:eu-central-1:123:repository/app"),
                _storage_resource("logs:log-group", 0.31, arn="arn:aws:logs:eu-central-1:123:log-group:/ecs/app"),
            ]
        )
        html = render_burn(report, user_email="test@example.com")
        assert "SPEND ANALYSIS" in html
        assert "What&#39;s Not in the Estimates" in html

    def test_uncalculated_costs_section_present(self):
        """The estimation gaps section lists known uncovered categories."""
        html = _render_uncalculated_costs()
        assert "Data Transfer" in html
        assert "S3 Request Costs" in html
        assert "Lambda Invocations" in html
        assert "API Gateway Requests" in html
        assert "DynamoDB Throughput" in html

    def test_uncalculated_costs_with_no_args(self):
        """Calling with no args still renders all categories gracefully."""
        html = _render_uncalculated_costs()
        assert "Your infra:" in html
        assert "Napkin Math:" in html

    def test_empty_report_renders_placeholder(self):
        """Empty report shows placeholder, not a crash."""
        html = render_burn({}, user_email="test@example.com")
        assert "No compliance report data available" in html

    def test_none_report_renders_placeholder(self):
        html = render_burn(None, user_email="test@example.com")
        assert "No compliance report data available" in html


class TestEnvelopeEstimates:
    """Tests for _envelope_estimates — back-of-the-envelope cost heuristics."""

    def test_returns_all_categories(self):
        """All 10 cost categories are present in the output."""
        est = _envelope_estimates([], {})
        expected_keys = {
            "data_transfer",
            "s3_requests",
            "cw_metrics",
            "cw_log_ingestion",
            "lambda",
            "api_gateway",
            "dynamodb",
            "security_services",
            "bedrock",
            "ses",
        }
        assert set(est.keys()) == expected_keys

    def test_each_category_has_required_fields(self):
        """Each estimate has infra, math, est, billed."""
        est = _envelope_estimates([], {})
        for key, val in est.items():
            assert "infra" in val, f"{key} missing infra"
            assert "math" in val, f"{key} missing math"
            assert "est" in val, f"{key} missing est"
            assert "billed" in val, f"{key} missing billed"

    def test_empty_resources_produce_zero_estimates(self):
        """No resources = zero or near-zero estimates (no crashes)."""
        est = _envelope_estimates([], {})
        for key in ("data_transfer", "s3_requests", "lambda", "api_gateway", "dynamodb"):
            assert est[key]["est"] == 0 or est[key]["est"] < 0.01

    def test_s3_estimate_scales_with_size(self):
        """S3 request estimate grows with total bucket size."""
        small = [make_resource(resource_type="s3:bucket", size_bytes=1 * 1024**3)]
        large = [make_resource(resource_type="s3:bucket", size_bytes=100 * 1024**3)]

        est_small = _envelope_estimates(small, {})
        est_large = _envelope_estimates(large, {})
        assert est_large["s3_requests"]["est"] > est_small["s3_requests"]["est"]

    def test_lambda_estimate_scales_with_eventbridge_rules(self):
        """More EventBridge rules = higher Lambda invocation estimate."""
        resources_few = [
            make_resource(resource_type="lambda:function", name="fn-1"),
            make_resource(resource_type="events:rule", name="rule-1"),
        ]
        resources_many = [
            make_resource(resource_type="lambda:function", name="fn-1"),
            *[make_resource(resource_type="events:rule", name=f"rule-{i}") for i in range(10)],
        ]

        est_few = _envelope_estimates(resources_few, {})
        est_many = _envelope_estimates(resources_many, {})
        assert est_many["lambda"]["est"] > est_few["lambda"]["est"]

    def test_dynamodb_estimate_scales_with_tables(self):
        """More DynamoDB tables = higher throughput estimate."""
        one = [make_resource(resource_type="dynamodb:table", name="t1")]
        five = [make_resource(resource_type="dynamodb:table", name=f"t{i}") for i in range(5)]

        est_one = _envelope_estimates(one, {})
        est_five = _envelope_estimates(five, {})
        assert est_five["dynamodb"]["est"] == 5 * est_one["dynamodb"]["est"]

    def test_log_ingestion_estimate_scales_with_stored_bytes(self):
        """Log ingestion estimate grows with stored bytes."""
        resources = [
            make_resource(resource_type="logs:log-group", stored_bytes=10 * 1024**3),
        ]
        est = _envelope_estimates(resources, {})
        assert est["cw_log_ingestion"]["est"] > 1.0  # 10GB * $0.57 ~ $5.70

    def test_billed_amounts_from_cost_explorer(self):
        """Billed amounts are extracted from service_costs dict."""
        svc_costs = {
            "AWS Data Transfer": 5.67,
            "AWS Lambda": 0.12,
            "Amazon DynamoDB": 1.23,
            "Amazon API Gateway": 0.45,
        }
        est = _envelope_estimates([], svc_costs)
        assert est["data_transfer"]["billed"] == 5.67
        assert est["lambda"]["billed"] == 0.12
        assert est["dynamodb"]["billed"] == 1.23
        assert est["api_gateway"]["billed"] == 0.45

    def test_bedrock_estimate_with_hunterkiller(self):
        """Bedrock estimate is non-zero when hunterkiller functions exist."""
        resources = [
            make_resource(resource_type="lambda:function", name="acme-prod-hunterkiller"),
        ]
        est = _envelope_estimates(resources, {})
        assert est["bedrock"]["est"] > 0
        assert "hunterkiller" in est["bedrock"]["infra"].lower() or "1" in est["bedrock"]["infra"]

    def test_bedrock_estimate_zero_without_hunterkiller(self):
        """Bedrock estimate is zero when no hunterkiller functions exist."""
        resources = [
            make_resource(resource_type="lambda:function", name="some-other-fn"),
        ]
        est = _envelope_estimates(resources, {})
        assert est["bedrock"]["est"] == 0

    def test_data_transfer_counts_running_ec2_only(self):
        """Stopped EC2 instances don't contribute to data transfer estimate."""
        resources = [
            make_resource(resource_type="ec2:instance", state="running"),
            make_resource(resource_type="ec2:instance", state="stopped"),
        ]
        est = _envelope_estimates(resources, {})
        # Only 1 running EC2 counted (not the stopped one)
        assert "1 running EC2" in est["data_transfer"]["infra"]

    def test_render_includes_envelope_subrows(self):
        """Rendered HTML includes envelope sub-rows with infra and math."""
        resources = [
            make_resource(resource_type="s3:bucket", size_bytes=50 * 1024**3),
            make_resource(resource_type="lambda:function", name="fn-1"),
            make_resource(resource_type="dynamodb:table", name="tbl-1"),
        ]
        html = _render_uncalculated_costs(resources, {"AWS Lambda": 0.05})
        assert "Your infra:" in html
        assert "Napkin Math:" in html
        assert "Billed:" in html
        assert "1 buckets" in html
        assert "1 functions" in html
        assert "1 tables" in html

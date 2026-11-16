"""The deferred enforcement schedule runs as a role that can only invoke the executor."""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

_MODULE = Path(__file__).resolve().parents[2] / "modules" / "aws" / "hunterkiller"
SCHEDULER_ROLE = "arn:aws:iam::123456789012:role/test-hunterkiller-scheduler"
EXECUTOR_ROLE = "arn:aws:iam::123456789012:role/test-hunterkiller-executor"


def _block(source, header):
    """Text of one top-level HCL block, by its header line."""
    start = source.index(header)
    end = source.index("\n}\n", start)
    return source[start:end]


def _clients():
    scheduler, lam = MagicMock(name="scheduler"), MagicMock(name="lambda")
    lam.get_function.return_value = {
        "Configuration": {
            "FunctionArn": "arn:aws:lambda:eu-central-1:123456789012:function:test-hunterkiller-executor",
            "Role": EXECUTOR_ROLE,
        }
    }
    return scheduler, lam, lambda name, **kw: {"scheduler": scheduler, "lambda": lam}[name]


class TestSchedule:
    def test_schedule_runs_as_scheduler_role_not_executor_role(self, hk_index):
        scheduler, lam, client = _clients()
        with (
            patch.object(hk_index, "EXECUTOR_FUNCTION_NAME", "test-hunterkiller-executor"),
            patch.object(hk_index, "SCHEDULER_ROLE_ARN", SCHEDULER_ROLE),
            patch.object(hk_index.boto3, "client", side_effect=client),
        ):
            hk_index._schedule_enforcement("batch-1", "2026-01-01")
        target = scheduler.create_schedule.call_args.kwargs["Target"]
        assert target["RoleArn"] == SCHEDULER_ROLE

    def test_no_schedule_without_scheduler_role(self, hk_index):
        scheduler, lam, client = _clients()
        with (
            patch.object(hk_index, "EXECUTOR_FUNCTION_NAME", "test-hunterkiller-executor"),
            patch.object(hk_index, "SCHEDULER_ROLE_ARN", "", create=True),
            patch.object(hk_index.boto3, "client", side_effect=client),
        ):
            hk_index._schedule_enforcement("batch-1", "2026-01-01")
        scheduler.create_schedule.assert_not_called()


class TestRoles:
    source = (_MODULE / "main.tf").read_text()

    def test_executor_role_is_trusted_by_lambda_only(self):
        block = _block(self.source, 'data "aws_iam_policy_document" "executor_assume_role" {')
        assert "scheduler.amazonaws.com" not in block
        assert '"lambda.amazonaws.com"' in block

    def test_notifier_role_is_trusted_by_lambda_only(self):
        block = _block(self.source, 'data "aws_iam_policy_document" "notifier_assume_role" {')
        assert "scheduler.amazonaws.com" not in block

    def test_scheduler_role_is_bound_to_this_account(self):
        block = _block(self.source, 'data "aws_iam_policy_document" "scheduler_assume_role" {')
        assert '"scheduler.amazonaws.com"' in block
        assert "aws:SourceAccount" in block

    def test_scheduler_role_may_only_invoke_the_executor(self):
        block = _block(self.source, 'data "aws_iam_policy_document" "scheduler_permissions" {')
        assert re.findall(r'"([a-z0-9-]+:[A-Za-z*]+)"', block) == ["lambda:InvokeFunction"]
        assert "aws_lambda_function.executor[0].arn" in block

    def test_notifier_passes_only_the_scheduler_role(self):
        block = _block(self.source, 'data "aws_iam_policy_document" "notifier_permissions" {')
        passrole = block[block.index('"iam:PassRole"') :]
        passrole = passrole[: passrole.index("condition")]
        assert "aws_iam_role.scheduler[0].arn" in passrole
        assert "aws_iam_role.executor" not in passrole
        assert "aws_iam_role.notifier" not in passrole


class TestExecutorReads:
    source = (_MODULE / "main.tf").read_text()

    def test_executor_has_no_account_wide_read_policy(self):
        assert "executor_read_only" not in self.source
        executor = _block(self.source, 'data "aws_iam_policy_document" "executor_permissions" {')
        assert "ReadOnlyAccess" not in executor

    def test_executor_reads_are_named(self):
        executor = _block(self.source, 'data "aws_iam_policy_document" "executor_permissions" {')
        reads = executor[executor.index('sid = "TeardownReads"') :]
        reads = reads[: reads.index("]")]
        for action in ("tag:GetResources", "iam:ListRoleTags", "ec2:DescribeInstances", "rds:DescribeDBInstances"):
            assert f'"{action}"' in reads

    def test_object_versions_of_infrastructure_buckets_cannot_be_deleted(self):
        executor = _block(self.source, 'data "aws_iam_policy_document" "executor_permissions" {')
        deny = executor[executor.index("Deny destructive actions on known infrastructure buckets") :]
        deny = deny[: deny.index("resources")]
        assert '"s3:DeleteObjectVersion"' in deny

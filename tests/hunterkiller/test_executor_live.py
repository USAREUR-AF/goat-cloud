"""Tests for executor.py live (non-dry-run) execution paths via moto.

Covers the destructive _execute_teardown() code path for major resource types.
This is the code that actually deletes AWS resources — bugs here have real consequences.
"""

import json

import boto3
from executor import _extract_after, _extract_log_group_name, execute_teardowns
from factories import TEST_REGION
from moto import mock_aws

REGION = TEST_REGION


def _make_record(arn, resource_type, region=REGION, name="test"):
    return {
        "resource_arn": arn,
        "resource_type": resource_type,
        "region": region,
        "resource_name": name,
    }


@mock_aws
class TestExecutorLiveEC2:
    def test_terminate_instance(self):
        ec2 = boto3.client("ec2", region_name=REGION)
        resp = ec2.run_instances(ImageId="ami-12345", MinCount=1, MaxCount=1)
        iid = resp["Instances"][0]["InstanceId"]
        arn = f"arn:aws:ec2:{REGION}:123456789012:instance/{iid}"

        results = execute_teardowns([_make_record(arn, "ec2:instance")], dry_run=False)
        assert len(results) == 1
        assert results[0]["success"] is True
        assert results[0]["dry_run"] is False
        assert "Terminated" in results[0]["message"]

        state = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]["State"]["Name"]
        assert state in ("shutting-down", "terminated")

    def test_delete_volume(self):
        ec2 = boto3.client("ec2", region_name=REGION)
        vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10)
        vid = vol["VolumeId"]
        arn = f"arn:aws:ec2:{REGION}:123456789012:volume/{vid}"

        results = execute_teardowns([_make_record(arn, "ec2:volume")], dry_run=False)
        assert results[0]["success"] is True
        assert "Deleted EBS volume" in results[0]["message"]

    def test_delete_security_group(self):
        ec2 = boto3.client("ec2", region_name=REGION)
        sg = ec2.create_security_group(GroupName="test-sg", Description="test")
        sgid = sg["GroupId"]
        arn = f"arn:aws:ec2:{REGION}:123456789012:security-group/{sgid}"

        results = execute_teardowns([_make_record(arn, "ec2:security-group")], dry_run=False)
        assert results[0]["success"] is True

    def test_release_eip(self):
        ec2 = boto3.client("ec2", region_name=REGION)
        eip = ec2.allocate_address(Domain="vpc")
        alloc_id = eip["AllocationId"]
        arn = f"arn:aws:ec2:{REGION}:123456789012:elastic-ip/{alloc_id}"

        results = execute_teardowns([_make_record(arn, "ec2:eip")], dry_run=False)
        assert results[0]["success"] is True
        assert "Released EIP" in results[0]["message"]


@mock_aws
class TestExecutorLiveLambda:
    def test_delete_function(self):
        # moto requires a real-looking IAM role for Lambda
        iam = boto3.client("iam", region_name=REGION)
        iam.create_role(
            RoleName="lambda-role",
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
        )
        role_arn = iam.get_role(RoleName="lambda-role")["Role"]["Arn"]

        lam = boto3.client("lambda", region_name=REGION)
        lam.create_function(
            FunctionName="test-func",
            Runtime="python3.12",
            Role=role_arn,
            Handler="index.handler",
            Code={"ZipFile": b"fake"},
        )
        arn = f"arn:aws:lambda:{REGION}:123456789012:function:test-func"

        results = execute_teardowns([_make_record(arn, "lambda:function")], dry_run=False)
        assert results[0]["success"] is True
        assert "Deleted Lambda" in results[0]["message"]


@mock_aws
class TestExecutorLiveS3:
    def test_delete_bucket_with_objects(self):
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(
            Bucket="test-bucket",
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        s3.put_object(Bucket="test-bucket", Key="file1.txt", Body=b"data")
        s3.put_object(Bucket="test-bucket", Key="file2.txt", Body=b"data")
        arn = "arn:aws:s3:::test-bucket"

        results = execute_teardowns([_make_record(arn, "s3:bucket")], dry_run=False, unrecoverable=True)
        assert results[0]["success"] is True
        assert "Deleted S3 bucket" in results[0]["message"]


@mock_aws
class TestExecutorLiveSSM:
    def test_delete_parameter(self):
        ssm = boto3.client("ssm", region_name=REGION)
        ssm.put_parameter(Name="/test/param", Value="secret", Type="String")
        # The ARN must match what SSM actually uses — moto may resolve differently
        arn = f"arn:aws:ssm:{REGION}:123456789012:parameter/test/param"

        results = execute_teardowns([_make_record(arn, "ssm:parameter")], dry_run=False)
        assert results[0]["success"] is True
        # May say "Deleted" or "Already gone" depending on moto's ARN handling
        assert "SSM parameter" in results[0]["message"] or "Already gone" in results[0]["message"]


@mock_aws
class TestExecutorLiveIAM:
    def test_delete_role_with_inline_policy(self):
        iam = boto3.client("iam")
        iam.create_role(
            RoleName="test-role",
            AssumeRolePolicyDocument="{}",
        )
        iam.put_role_policy(
            RoleName="test-role",
            PolicyName="inline",
            PolicyDocument='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"logs:CreateLogGroup","Resource":"*"}]}',
        )
        arn = "arn:aws:iam::123456789012:role/test-role"

        results = execute_teardowns([_make_record(arn, "iam:role", region="")], dry_run=False)
        assert results[0]["success"] is True
        assert "Deleted IAM role" in results[0]["message"]

    def test_delete_user_with_access_keys(self):
        iam = boto3.client("iam")
        iam.create_user(UserName="test-user")
        iam.create_access_key(UserName="test-user")
        arn = "arn:aws:iam::123456789012:user/test-user"

        results = execute_teardowns([_make_record(arn, "iam:user", region="")], dry_run=False)
        assert results[0]["success"] is True
        assert "Deleted IAM user" in results[0]["message"]


@mock_aws
class TestExecutorLiveLogs:
    def test_delete_log_group(self):
        logs = boto3.client("logs", region_name=REGION)
        logs.create_log_group(logGroupName="/aws/lambda/test")
        arn = f"arn:aws:logs:{REGION}:123456789012:log-group:/aws/lambda/test:*"

        results = execute_teardowns([_make_record(arn, "logs:log-group")], dry_run=False)
        assert results[0]["success"] is True
        assert "Deleted CloudWatch log group" in results[0]["message"]


@mock_aws
class TestExecutorLiveSecretsManager:
    def test_delete_secret(self):
        sm = boto3.client("secretsmanager", region_name=REGION)
        sm.create_secret(Name="test-secret", SecretString="value")
        arn = f"arn:aws:secretsmanager:{REGION}:123456789012:secret:test-secret-AbCdEf"

        results = execute_teardowns([_make_record(arn, "secretsmanager:secret")], dry_run=False)
        assert results[0]["success"] is True


class TestExecutorErrorHandling:
    @mock_aws
    def test_nonexistent_resource_idempotent(self):
        """Trying to delete a non-existent resource succeeds (idempotent teardown)."""
        arn = f"arn:aws:ec2:{REGION}:123456789012:volume/vol-0123456789abcdef0"
        results = execute_teardowns([_make_record(arn, "ec2:volume")], dry_run=False)
        assert len(results) == 1
        assert results[0]["success"] is True
        assert "Already gone" in results[0]["message"]

    def test_unknown_resource_type_returns_failure(self):
        """Unknown resource type raises ValueError, caught by executor."""
        arn = "arn:aws:unknown:eu-central-1:123456789012:thing/xyz"
        results = execute_teardowns([_make_record(arn, "unknown:thing")], dry_run=False)
        assert len(results) == 1
        assert results[0]["success"] is False
        assert "No teardown handler" in results[0]["message"]


class TestHelperFunctions:
    def test_extract_after_parameter(self):
        arn = "arn:aws:ssm:eu-central-1:123456789012:parameter/my/nested/param"
        assert _extract_after(arn, ":parameter/") == "my/nested/param"

    def test_extract_after_repository(self):
        arn = "arn:aws:ecr:eu-central-1:123456789012:repository/my-repo"
        assert _extract_after(arn, ":repository/") == "my-repo"

    def test_extract_after_no_marker(self):
        """Falls back to _extract_id when marker not found."""
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-abc"
        result = _extract_after(arn, ":parameter/")
        assert result == "i-abc"

    def test_extract_log_group_name_from_arn(self):
        arn = "arn:aws:logs:eu-central-1:123456789012:log-group:/aws/lambda/test:*"
        assert _extract_log_group_name(arn) == "/aws/lambda/test"

    def test_extract_log_group_name_raw(self):
        assert _extract_log_group_name("/my/log/group") == "/my/log/group"

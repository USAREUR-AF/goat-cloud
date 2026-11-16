"""Executor default: a resource that holds data is removed only in a recoverable form."""

import importlib.util
import os
from unittest.mock import MagicMock, patch

import boto3
import executor
import pytest
from executor import SNAPSHOT_TAG_KEY, execute_teardowns
from factories import TEST_REGION
from moto import mock_aws

REGION = TEST_REGION
ACCOUNT = "123456789012"


def _record(arn, resource_type, region=REGION):
    return {"resource_arn": arn, "resource_type": resource_type, "region": region, "resource_name": "test"}


def _run(arn, resource_type, **kwargs):
    return execute_teardowns([_record(arn, resource_type)], dry_run=False, **kwargs)[0]


@mock_aws
class TestVolumes:
    def test_volume_is_snapshotted_before_delete(self):
        ec2 = boto3.client("ec2", region_name=REGION)
        vid = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10)["VolumeId"]

        result = _run(f"arn:aws:ec2:{REGION}:{ACCOUNT}:volume/{vid}", "ec2:volume")

        assert result["success"] is True
        snaps = ec2.describe_snapshots(
            OwnerIds=["self"], Filters=[{"Name": f"tag:{SNAPSHOT_TAG_KEY}", "Values": [vid]}]
        )["Snapshots"]
        assert len(snaps) == 1 and snaps[0]["VolumeId"] == vid
        assert snaps[0]["SnapshotId"] in result["message"]
        assert ec2.describe_volumes(Filters=[{"Name": "volume-id", "Values": [vid]}])["Volumes"] == []

    def test_unrecoverable_deletes_without_snapshot(self):
        ec2 = boto3.client("ec2", region_name=REGION)
        vid = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10)["VolumeId"]

        result = _run(f"arn:aws:ec2:{REGION}:{ACCOUNT}:volume/{vid}", "ec2:volume", unrecoverable=True)

        assert result["success"] is True
        snaps = ec2.describe_snapshots(OwnerIds=["self"], Filters=[{"Name": "volume-id", "Values": [vid]}])
        assert snaps["Snapshots"] == []

    def test_instance_volumes_are_snapshotted_before_termination(self):
        ec2 = boto3.client("ec2", region_name=REGION)
        iid = ec2.run_instances(ImageId="ami-12345", MinCount=1, MaxCount=1)["Instances"][0]["InstanceId"]
        instance = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
        volume_ids = [m["Ebs"]["VolumeId"] for m in instance["BlockDeviceMappings"]]
        assert volume_ids

        result = _run(f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/{iid}", "ec2:instance")

        assert result["success"] is True
        snaps = ec2.describe_snapshots(
            OwnerIds=["self"], Filters=[{"Name": f"tag:{SNAPSHOT_TAG_KEY}", "Values": volume_ids}]
        )["Snapshots"]
        assert {s["VolumeId"] for s in snaps} == set(volume_ids)


class TestSnapshotInProgress:
    def test_volume_is_kept_while_its_snapshot_is_pending(self):
        ec2 = MagicMock()
        ec2.describe_snapshots.return_value = {"Snapshots": [{"SnapshotId": "snap-1", "State": "pending"}]}
        with (
            patch.object(executor.boto3, "client", return_value=ec2),
            patch.object(executor, "SNAPSHOT_WAIT_SECONDS", 0),
        ):
            result = _run(f"arn:aws:ec2:{REGION}:{ACCOUNT}:volume/vol-0123456789abcdef0", "ec2:volume")

        assert result["success"] is False and result["pending"] is True
        ec2.create_snapshot.assert_not_called()  # the snapshot already started is reused
        ec2.delete_volume.assert_not_called()

    def test_failed_snapshot_keeps_the_volume(self):
        ec2 = MagicMock()
        ec2.describe_snapshots.return_value = {"Snapshots": []}
        ec2.create_snapshot.return_value = {"SnapshotId": "snap-2", "State": "error"}
        with patch.object(executor.boto3, "client", return_value=ec2):
            result = _run(f"arn:aws:ec2:{REGION}:{ACCOUNT}:volume/vol-0123456789abcdef0", "ec2:volume")

        assert result["success"] is False and result["pending"] is False
        ec2.delete_volume.assert_not_called()

    def test_pending_resource_stays_eligible_for_the_next_run(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "..", "modules", "aws", "hunterkiller", "lambda", "executor_index.py"
        )
        spec = importlib.util.spec_from_file_location("hk_executor_index_recoverable", os.path.normpath(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with patch.object(mod, "DRY_RUN", False):
            assert mod._result_status({"success": False, "pending": True}) == "warned"
            assert mod._result_status({"success": False, "pending": False}) == "error"
            assert mod._result_status({"success": True}) == "torn_down"
        with patch.object(mod, "DRY_RUN", True):
            assert mod._result_status({"success": True}) == "dry_run"
        assert mod.UNRECOVERABLE_TEARDOWN is False


class TestRds:
    ARN = f"arn:aws:rds:{REGION}:{ACCOUNT}:db:orders"

    def _rds(self, **instance):
        rds = MagicMock()
        rds.describe_db_instances.return_value = {"DBInstances": [{"DBInstanceIdentifier": "orders", **instance}]}
        return rds

    def test_final_snapshot_and_backups_retained(self):
        rds = self._rds()
        with patch.object(executor.boto3, "client", return_value=rds):
            result = _run(self.ARN, "rds:instance")

        assert result["success"] is True
        kwargs = rds.delete_db_instance.call_args.kwargs
        assert kwargs["SkipFinalSnapshot"] is False
        assert kwargs["DeleteAutomatedBackups"] is False
        assert kwargs["FinalDBSnapshotIdentifier"].startswith("hk-final-orders-")
        assert kwargs["FinalDBSnapshotIdentifier"] in result["message"]

    def test_snapshot_name_is_deterministic(self):
        assert executor._final_name("orders") == executor._final_name("orders")
        assert executor._final_name("a/b:c d").startswith("hk-final-a-b-c-d-")

    @pytest.mark.parametrize(
        "member", [{"DBClusterIdentifier": "cluster-1"}, {"ReadReplicaSourceDBInstanceIdentifier": "source-1"}]
    )
    def test_cluster_member_or_replica_has_no_final_snapshot_to_take(self, member):
        rds = self._rds(**member)
        with patch.object(executor.boto3, "client", return_value=rds):
            result = _run(self.ARN, "rds:instance")

        assert result["success"] is True
        kwargs = rds.delete_db_instance.call_args.kwargs
        assert kwargs["SkipFinalSnapshot"] is True
        assert "DeleteAutomatedBackups" not in kwargs

    def test_unrecoverable(self):
        rds = self._rds()
        with patch.object(executor.boto3, "client", return_value=rds):
            _run(self.ARN, "rds:instance", unrecoverable=True)

        kwargs = rds.delete_db_instance.call_args.kwargs
        assert kwargs["SkipFinalSnapshot"] is True and kwargs["DeleteAutomatedBackups"] is True


@mock_aws
class TestBuckets:
    def _bucket(self, name):
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": REGION})
        return s3

    def test_bucket_with_objects_is_refused(self):
        s3 = self._bucket("data-bucket")
        s3.put_object(Bucket="data-bucket", Key="a.txt", Body=b"data")

        result = _run("arn:aws:s3:::data-bucket", "s3:bucket")

        assert result["success"] is False and result["pending"] is False
        assert "not empty" in result["message"]
        assert s3.get_object(Bucket="data-bucket", Key="a.txt")["Body"].read() == b"data"

    def test_bucket_with_only_old_versions_is_refused(self):
        s3 = self._bucket("versioned-bucket")
        s3.put_bucket_versioning(Bucket="versioned-bucket", VersioningConfiguration={"Status": "Enabled"})
        s3.put_object(Bucket="versioned-bucket", Key="a.txt", Body=b"data")
        s3.delete_object(Bucket="versioned-bucket", Key="a.txt")

        result = _run("arn:aws:s3:::versioned-bucket", "s3:bucket")

        assert result["success"] is False
        assert "versioned-bucket" in [b["Name"] for b in s3.list_buckets()["Buckets"]]

    def test_empty_bucket_is_deleted(self):
        s3 = self._bucket("empty-bucket")

        result = _run("arn:aws:s3:::empty-bucket", "s3:bucket")

        assert result["success"] is True
        assert "empty-bucket" not in [b["Name"] for b in s3.list_buckets()["Buckets"]]


class TestCacheClusters:
    ARN = f"arn:aws:elasticache:{REGION}:{ACCOUNT}:cluster:sessions"

    def _client(self, **cluster):
        client = MagicMock()
        client.describe_cache_clusters.return_value = {"CacheClusters": [{"CacheClusterId": "sessions", **cluster}]}
        return client

    def test_redis_takes_a_final_snapshot(self):
        client = self._client(Engine="redis")
        with patch.object(executor.boto3, "client", return_value=client):
            result = _run(self.ARN, "elasticache:cluster")

        assert result["success"] is True
        assert client.delete_cache_cluster.call_args.kwargs["FinalSnapshotIdentifier"].startswith("hk-final-sessions-")

    def test_memcached_holds_nothing_to_snapshot(self):
        client = self._client(Engine="memcached")
        with patch.object(executor.boto3, "client", return_value=client):
            result = _run(self.ARN, "elasticache:cluster")

        assert result["success"] is True
        assert "FinalSnapshotIdentifier" not in client.delete_cache_cluster.call_args.kwargs

    def test_replication_group_member_is_refused(self):
        client = self._client(Engine="redis", ReplicationGroupId="group-1")
        with patch.object(executor.boto3, "client", return_value=client):
            result = _run(self.ARN, "elasticache:cluster")

        assert result["success"] is False
        client.delete_cache_cluster.assert_not_called()


class TestOtherDataHolders:
    def test_secret_gets_a_recovery_window(self):
        client = MagicMock()
        arn = f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:api-key-AbCdEf"
        with patch.object(executor.boto3, "client", return_value=client):
            _run(arn, "secretsmanager:secret")
            assert client.delete_secret.call_args.kwargs == {"SecretId": arn, "RecoveryWindowInDays": 30}
            _run(arn, "secretsmanager:secret", unrecoverable=True)
            assert client.delete_secret.call_args.kwargs == {"SecretId": arn, "ForceDeleteWithoutRecovery": True}

    def test_key_gets_the_longest_pending_window(self):
        client = MagicMock()
        arn = f"arn:aws:kms:{REGION}:{ACCOUNT}:key/12345678-90ab-cdef-1234-567890abcdef"
        with patch.object(executor.boto3, "client", return_value=client):
            _run(arn, "kms:key")
            assert client.schedule_key_deletion.call_args.kwargs["PendingWindowInDays"] == 30
            _run(arn, "kms:key", unrecoverable=True)
            assert client.schedule_key_deletion.call_args.kwargs["PendingWindowInDays"] == 7

    def test_image_repository_is_not_forced(self):
        client = MagicMock()
        arn = f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/app"
        with patch.object(executor.boto3, "client", return_value=client):
            _run(arn, "ecr:repository")
            assert client.delete_repository.call_args.kwargs == {"repositoryName": "app", "force": False}
            _run(arn, "ecr:repository", unrecoverable=True)
            assert client.delete_repository.call_args.kwargs["force"] is True


def test_dry_run_describes_the_recoverable_form():
    arn = f"arn:aws:rds:{REGION}:{ACCOUNT}:db:orders"
    result = execute_teardowns([_record(arn, "rds:instance")], dry_run=True)[0]
    assert "final snapshot" in result["message"]
    result = execute_teardowns([_record(arn, "rds:instance")], dry_run=True, unrecoverable=True)[0]
    assert "final snapshot" not in result["message"]

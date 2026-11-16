"""
Teardown Executor

Executes resource teardowns via boto3 API calls.
Supports the same resource types as the compliance scanner's teardown commands,
but uses direct API calls instead of CLI commands.

Safety:
  - Recoverable by default: a resource that holds data is removed only in a
    form that can be restored (final snapshot, recovery window, or refusal
    when it still holds data). unrecoverable=True selects the plain delete.
  - dry_run mode: logs what WOULD happen, returns success without acting
  - Each teardown is independently wrapped in try/except
  - Results include success/failure status and messages
"""

import re
import time
from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError

# Tag put on every snapshot the executor takes before a removal.
SNAPSHOT_TAG_KEY = "hunterkiller:final-snapshot-of"
# How long one teardown waits for a volume snapshot to complete before it
# leaves the resource in place for the next run.
SNAPSHOT_WAIT_SECONDS = 60
_SNAPSHOT_POLL_SECONDS = 5


class TeardownPending(Exception):
    """A recovery point is still being made; the resource was left in place."""


class TeardownRefused(Exception):
    """The resource holds data and has no recoverable form of removal."""


def _final_name(resource_id):
    """Deterministic name for a final snapshot: one per resource per day."""
    safe = re.sub(r"[^A-Za-z0-9-]+", "-", str(resource_id)).strip("-")[:200]
    return f"hk-final-{safe}-{datetime.now(UTC).strftime('%Y%m%d')}"


def _ensure_volume_snapshot(ec2, volume_id, wait_seconds=None):
    """Make sure a completed snapshot of the volume exists; return its id.

    Reuses a snapshot this executor already started for the volume. Raises
    TeardownPending when the snapshot is not complete within the wait.
    """
    wait_seconds = SNAPSHOT_WAIT_SECONDS if wait_seconds is None else wait_seconds
    found = ec2.describe_snapshots(
        OwnerIds=["self"],
        Filters=[
            {"Name": "volume-id", "Values": [volume_id]},
            {"Name": f"tag:{SNAPSHOT_TAG_KEY}", "Values": [volume_id]},
        ],
    ).get("Snapshots", [])
    usable = [snap for snap in found if snap.get("State") in ("pending", "completed")]
    if usable:
        snapshot = sorted(usable, key=lambda snap: str(snap.get("StartTime", "")))[-1]
    else:
        snapshot = ec2.create_snapshot(
            VolumeId=volume_id,
            Description=f"Final snapshot of {volume_id} before removal by hunterkiller",
            TagSpecifications=[
                {
                    "ResourceType": "snapshot",
                    "Tags": [
                        {"Key": SNAPSHOT_TAG_KEY, "Value": volume_id},
                        {"Key": "Name", "Value": _final_name(volume_id)},
                    ],
                }
            ],
        )
    snapshot_id = snapshot["SnapshotId"]
    state = snapshot.get("State", "pending")
    deadline = time.monotonic() + wait_seconds
    while state != "completed":
        if state == "error":
            raise TeardownRefused(f"snapshot {snapshot_id} of {volume_id} failed; volume kept")
        if time.monotonic() >= deadline:
            raise TeardownPending(
                f"snapshot {snapshot_id} of {volume_id} is still in progress; resource kept until it completes"
            )
        time.sleep(_SNAPSHOT_POLL_SECONDS)
        state = ec2.describe_snapshots(SnapshotIds=[snapshot_id])["Snapshots"][0].get("State", "pending")
    return snapshot_id


# AWS error codes that mean "resource already gone" — treat as successful teardown
_NOT_FOUND_CODES = frozenset(
    {
        "InvalidVolume.NotFound",
        "InvalidSnapshot.NotFound",
        "InvalidSecurityGroupRuleId.NotFound",
        "InvalidGroup.NotFound",
        "InvalidFlowLogId.NotFound",
        "InvalidInstanceID.NotFound",
        "InvalidAllocationID.NotFound",
        "InvalidNatGatewayID.NotFound",
        "ParameterNotFound",
        "ResourceNotFoundException",  # Generic — used by many services
        "DBInstanceNotFound",
        "CacheClusterNotFound",
        "NoSuchEntity",  # IAM
        "HostedZoneNotFound",
        "NoSuchHostedZone",
        "RepositoryNotFoundException",  # ECR
        "FunctionNotFound",  # Lambda
        "NoSuchBucket",  # S3
        "RuleNotFoundException",  # EventBridge
        "SecretNotFoundException",  # Secrets Manager (ForceDelete)
        "NotFoundException",  # KMS, API Gateway, etc.
    }
)


def _is_not_found(exc):
    """Return True if a ClientError indicates the resource no longer exists."""
    if not isinstance(exc, ClientError):
        return False
    code = exc.response.get("Error", {}).get("Code", "")
    return code in _NOT_FOUND_CODES


def execute_teardowns(records, dry_run=True, unrecoverable=False):
    """
    Execute teardowns for a list of warned records.

    Args:
        records: list of DynamoDB record dicts with resource_arn, resource_type, region
        dry_run: if True, log but don't execute
        unrecoverable: if True, remove data-holding resources without a
            recovery point (no final snapshot, no recovery window, non-empty
            buckets and repositories purged)

    Returns:
        list of result dicts: {resource_arn, resource_type, success, message, dry_run}
    """
    results = []

    for record in records:
        arn = record.get("resource_arn", "")
        resource_type = record.get("resource_type", "")
        region = record.get("region", "")
        resource_name = record.get("resource_name", "")

        if dry_run:
            action = _describe_action(resource_type, arn, region, unrecoverable)
            print(f"  DRY RUN: Would {action} for {arn}")
            results.append(
                {
                    "resource_arn": arn,
                    "resource_type": resource_type,
                    "resource_name": resource_name,
                    "region": region,
                    "success": True,
                    "message": f"DRY RUN: Would {action}",
                    "dry_run": True,
                }
            )
            continue

        # Live execution
        try:
            message = _execute_teardown(resource_type, arn, region, unrecoverable, name_hint=resource_name)
            print(f"  TORN DOWN: {arn} — {message}")
            results.append(
                {
                    "resource_arn": arn,
                    "resource_type": resource_type,
                    "resource_name": resource_name,
                    "region": region,
                    "success": True,
                    "message": message,
                    "dry_run": False,
                }
            )
        except (TeardownPending, TeardownRefused) as e:
            pending = isinstance(e, TeardownPending)
            print(f"  {'PENDING' if pending else 'REFUSED'}: {arn} — {e}")
            results.append(
                {
                    "resource_arn": arn,
                    "resource_type": resource_type,
                    "resource_name": resource_name,
                    "region": region,
                    "success": False,
                    "pending": pending,
                    "message": str(e),
                    "dry_run": False,
                }
            )
        except ClientError as e:
            if _is_not_found(e):
                code = e.response["Error"]["Code"]
                msg = f"Already gone ({code}) — treating as torn down"
                print(f"  TORN DOWN (gone): {arn} — {msg}")
                results.append(
                    {
                        "resource_arn": arn,
                        "resource_type": resource_type,
                        "resource_name": resource_name,
                        "region": region,
                        "success": True,
                        "message": msg,
                        "dry_run": False,
                    }
                )
            else:
                error_msg = f"ClientError: {e}"
                print(f"  FAILED: {arn} — {error_msg}")
                results.append(
                    {
                        "resource_arn": arn,
                        "resource_type": resource_type,
                        "resource_name": resource_name,
                        "region": region,
                        "success": False,
                        "message": error_msg,
                        "dry_run": False,
                    }
                )
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            print(f"  FAILED: {arn} — {error_msg}")
            results.append(
                {
                    "resource_arn": arn,
                    "resource_type": resource_type,
                    "resource_name": resource_name,
                    "region": region,
                    "success": False,
                    "message": error_msg,
                    "dry_run": False,
                }
            )

    return results


_ARN = r"^arn:aws[a-z-]*:"
_REG = r"[a-z]{2}(?:-[a-z]+)+-\d"
_ACCT = r"\d{12}"
_HEX = r"[0-9a-f]{8,17}"

# Resource ARN shapes the executor acts on, by resource type. The `id` group
# is the identifier passed to the service; an ARN of any other shape is refused
# before a client is created.
_ID_PATTERNS = {
    "ec2:instance": rf"{_ARN}ec2:{_REG}:{_ACCT}:instance/(?P<id>i-{_HEX})$",
    "ec2:volume": rf"{_ARN}ec2:{_REG}:{_ACCT}:volume/(?P<id>vol-{_HEX})$",
    "ec2:security-group": rf"{_ARN}ec2:{_REG}:{_ACCT}:security-group/(?P<id>sg-{_HEX})$",
    "ec2:eip": rf"{_ARN}ec2:{_REG}:{_ACCT}:elastic-ip/(?P<id>eipalloc-{_HEX})$",
    "ec2:natgateway": rf"{_ARN}ec2:{_REG}:{_ACCT}:natgateway/(?P<id>nat-{_HEX})$",
    "ec2:security-group-rule": rf"{_ARN}ec2:{_REG}:{_ACCT}:security-group-rule/(?P<id>sgr-{_HEX})$",
    "ec2:vpc-flow-log": rf"{_ARN}ec2:{_REG}:{_ACCT}:vpc-flow-log/(?P<id>fl-{_HEX})$",
    "rds:instance": rf"{_ARN}rds:{_REG}:{_ACCT}:db:(?P<id>[A-Za-z][A-Za-z0-9-]{{0,62}})$",
    "rds:cluster-pg": rf"{_ARN}rds:{_REG}:{_ACCT}:cluster-pg:(?P<id>[A-Za-z][A-Za-z0-9.-]{{0,254}})$",
    "ecs:service": (
        rf"{_ARN}ecs:{_REG}:{_ACCT}:service/(?P<cluster>[A-Za-z0-9_-]{{1,255}})/(?P<id>[A-Za-z0-9_-]{{1,255}})$"
    ),
    "lambda:function": rf"{_ARN}lambda:{_REG}:{_ACCT}:function:(?P<id>[A-Za-z0-9_-]{{1,64}})$",
    "s3:bucket": rf"{_ARN}s3:::(?P<id>[a-z0-9][a-z0-9.-]{{1,61}}[a-z0-9])$",
    "elasticache:cluster": rf"{_ARN}elasticache:{_REG}:{_ACCT}:cluster:(?P<id>[A-Za-z][A-Za-z0-9-]{{0,49}})$",
    "elbv2:loadbalancer": (
        rf"{_ARN}elasticloadbalancing:{_REG}:{_ACCT}:loadbalancer/(?:app|net)/[A-Za-z0-9-]{{1,32}}/(?P<id>[0-9a-f]{{16}})$"
    ),
    "ssm:parameter": rf"{_ARN}ssm:{_REG}:{_ACCT}:parameter/(?P<id>[A-Za-z0-9_.\-/]{{1,2047}})$",
    "ecr:repository": rf"{_ARN}ecr:{_REG}:{_ACCT}:repository/(?P<id>[a-z0-9][a-z0-9._/-]{{1,255}})$",
    "events:rule": rf"{_ARN}events:{_REG}:{_ACCT}:rule/(?:[A-Za-z0-9._-]{{1,256}}/)?(?P<id>[A-Za-z0-9._-]{{1,64}})$",
    "logs:log-group": rf"{_ARN}logs:{_REG}:{_ACCT}:log-group:(?P<id>[A-Za-z0-9_./#-]{{1,512}})(?::\*)?$",
    "secretsmanager:secret": rf"{_ARN}secretsmanager:{_REG}:{_ACCT}:secret:(?P<id>[A-Za-z0-9/_+=.@-]{{1,512}})$",
    "kms:key": rf"{_ARN}kms:{_REG}:{_ACCT}:key/(?P<id>[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}|mrk-[0-9a-f]{{32}})$",
    "iam:role": rf"{_ARN}iam::{_ACCT}:role/(?:[A-Za-z0-9+=,.@_-]+/)*(?P<id>[A-Za-z0-9+=,.@_-]{{1,64}})$",
    "iam:user": rf"{_ARN}iam::{_ACCT}:user/(?:[A-Za-z0-9+=,.@_-]+/)*(?P<id>[A-Za-z0-9+=,.@_-]{{1,64}})$",
    "iam:policy": rf"{_ARN}iam::{_ACCT}:policy/(?:[A-Za-z0-9+=,.@_-]+/)*(?P<id>[A-Za-z0-9+=,.@_-]{{1,128}})$",
    "route53:hostedzone": rf"{_ARN}route53:::hostedzone/(?P<id>Z[A-Z0-9]{{1,32}})$",
}
_ID_PATTERNS = {kind: re.compile(pattern) for kind, pattern in _ID_PATTERNS.items()}
for _alias in ("elbv2:application", "elbv2:network"):
    _ID_PATTERNS[_alias] = _ID_PATTERNS["elbv2:loadbalancer"]


def _teardown_kind(resource_type, arn):
    """The handler a record goes to: its type, or the ARN's kind for a NAT gateway or load balancer."""
    if resource_type in _ID_PATTERNS:
        return resource_type
    if arn and ":natgateway/" in arn:
        return "ec2:natgateway"
    if arn and "elasticloadbalancing" in arn and ":loadbalancer/" in arn:
        return "elbv2:loadbalancer"
    return resource_type


def _validated(kind, arn):
    """Match the ARN against its type's shape, or raise ValueError."""
    pattern = _ID_PATTERNS.get(kind)
    if pattern is None:
        raise ValueError(f"No teardown handler for resource type '{kind}'")
    match = pattern.match(arn or "")
    if not match:
        raise ValueError(f"ARN does not have the form of a {kind}: {arn}")
    return match


def _execute_teardown(resource_type, arn, region, unrecoverable=False, name_hint=""):
    """Execute the actual teardown. Returns a success message. Raises on failure.

    name_hint is the resource name the scanner recorded; it is used only where
    the ARN does not carry the name exactly (an SSM parameter's leading "/")
    and only when it agrees with the ARN.
    """
    kind = _teardown_kind(resource_type, arn)
    match = _validated(kind, arn)
    resource_id = match.group("id")
    resource_type = kind

    if resource_type == "ec2:instance":
        ec2 = boto3.client("ec2", region_name=region)
        snapshots = []
        if not unrecoverable:
            # Volumes that termination deletes are snapshotted first.
            reservations = ec2.describe_instances(InstanceIds=[resource_id]).get("Reservations", [])
            for reservation in reservations:
                for instance in reservation.get("Instances", []):
                    for mapping in instance.get("BlockDeviceMappings", []):
                        ebs = mapping.get("Ebs") or {}
                        if ebs.get("VolumeId") and ebs.get("DeleteOnTermination", True):
                            snapshots.append(_ensure_volume_snapshot(ec2, ebs["VolumeId"]))
        ec2.terminate_instances(InstanceIds=[resource_id])
        note = f" (volume snapshots: {', '.join(snapshots)})" if snapshots else ""
        return f"Terminated EC2 instance {resource_id}{note}"

    if resource_type == "ec2:volume":
        ec2 = boto3.client("ec2", region_name=region)
        if unrecoverable:
            ec2.delete_volume(VolumeId=resource_id)
            return f"Deleted EBS volume {resource_id}"
        snapshot_id = _ensure_volume_snapshot(ec2, resource_id)
        ec2.delete_volume(VolumeId=resource_id)
        return f"Deleted EBS volume {resource_id} (snapshot {snapshot_id})"

    if resource_type == "ec2:security-group":
        ec2 = boto3.client("ec2", region_name=region)
        ec2.delete_security_group(GroupId=resource_id)
        return f"Deleted security group {resource_id}"

    if resource_type == "ec2:eip":
        ec2 = boto3.client("ec2", region_name=region)
        ec2.release_address(AllocationId=resource_id)
        return f"Released EIP {resource_id}"

    if resource_type == "ec2:natgateway":
        ec2 = boto3.client("ec2", region_name=region)
        ec2.delete_nat_gateway(NatGatewayId=resource_id)
        return f"Deleted NAT gateway {resource_id}"

    if resource_type == "rds:instance":
        rds = boto3.client("rds", region_name=region)
        if unrecoverable:
            rds.delete_db_instance(
                DBInstanceIdentifier=resource_id,
                SkipFinalSnapshot=True,
                DeleteAutomatedBackups=True,
            )
            return f"Deleting RDS instance {resource_id} (async, no final snapshot)"
        instance = rds.describe_db_instances(DBInstanceIdentifier=resource_id)["DBInstances"][0]
        if instance.get("DBClusterIdentifier") or instance.get("ReadReplicaSourceDBInstanceIdentifier"):
            # A cluster member or read replica cannot take a final snapshot:
            # its data stays in the cluster or in the source instance.
            rds.delete_db_instance(DBInstanceIdentifier=resource_id, SkipFinalSnapshot=True)
            return f"Deleting RDS instance {resource_id} (async; data remains in its cluster or source instance)"
        snapshot_name = _final_name(resource_id)
        rds.delete_db_instance(
            DBInstanceIdentifier=resource_id,
            SkipFinalSnapshot=False,
            FinalDBSnapshotIdentifier=snapshot_name,
            DeleteAutomatedBackups=False,
        )
        return (
            f"Deleting RDS instance {resource_id} (async, final snapshot {snapshot_name}, automated backups retained)"
        )

    if resource_type == "ecs:service":
        ecs = boto3.client("ecs", region_name=region)
        cluster, service = match.group("cluster"), resource_id
        ecs.update_service(cluster=cluster, service=service, desiredCount=0)
        ecs.delete_service(cluster=cluster, service=service, force=True)
        return f"Deleted ECS service {service} in cluster {cluster}"

    if resource_type == "lambda:function":
        lam = boto3.client("lambda", region_name=region)
        lam.delete_function(FunctionName=resource_id)
        return f"Deleted Lambda function {resource_id}"

    if resource_type == "s3:bucket":
        s3 = boto3.resource("s3", region_name=region)
        bucket = s3.Bucket(resource_id)
        if unrecoverable:
            bucket.object_versions.all().delete()
            bucket.delete()
            return f"Deleted S3 bucket {resource_id} and all objects/versions"
        if any(True for _ in bucket.object_versions.limit(1)):
            raise TeardownRefused(f"bucket {resource_id} is not empty; bucket and its objects kept")
        bucket.delete()
        return f"Deleted empty S3 bucket {resource_id}"

    if resource_type == "elasticache:cluster":
        ec = boto3.client("elasticache", region_name=region)
        if unrecoverable:
            ec.delete_cache_cluster(CacheClusterId=resource_id)
            return f"Deleting ElastiCache cluster {resource_id} (async, no final snapshot)"
        cluster = ec.describe_cache_clusters(CacheClusterId=resource_id)["CacheClusters"][0]
        if cluster.get("Engine", "").lower() == "memcached":
            # Memcached keeps no durable data and has no snapshot.
            ec.delete_cache_cluster(CacheClusterId=resource_id)
            return f"Deleting ElastiCache cluster {resource_id} (async; memcached holds no durable data)"
        if cluster.get("ReplicationGroupId"):
            raise TeardownRefused(
                f"cache cluster {resource_id} belongs to replication group {cluster['ReplicationGroupId']}; "
                "a member cannot take a final snapshot, cluster kept"
            )
        snapshot_name = _final_name(resource_id)
        ec.delete_cache_cluster(CacheClusterId=resource_id, FinalSnapshotIdentifier=snapshot_name)
        return f"Deleting ElastiCache cluster {resource_id} (async, final snapshot {snapshot_name})"

    if resource_type in ("elbv2:loadbalancer", "elbv2:application", "elbv2:network"):
        elbv2 = boto3.client("elbv2", region_name=region)
        elbv2.delete_load_balancer(LoadBalancerArn=arn)
        return f"Deleted load balancer {resource_id}"

    if resource_type == "ssm:parameter":
        ssm = boto3.client("ssm", region_name=region)
        name = _ssm_parameter_name(arn, name_hint)
        ssm.delete_parameter(Name=name)
        return f"Deleted SSM parameter {name}"

    if resource_type == "ec2:security-group-rule":
        ec2 = boto3.client("ec2", region_name=region)
        rule_id = resource_id
        resp = ec2.describe_security_group_rules(SecurityGroupRuleIds=[rule_id])
        rule = resp["SecurityGroupRules"][0]
        sg_id = rule["GroupId"]
        if rule.get("IsEgress"):
            ec2.revoke_security_group_egress(GroupId=sg_id, SecurityGroupRuleIds=[rule_id])
        else:
            ec2.revoke_security_group_ingress(GroupId=sg_id, SecurityGroupRuleIds=[rule_id])
        direction = "egress" if rule.get("IsEgress") else "ingress"
        return f"Revoked {direction} rule {rule_id} from {sg_id}"

    if resource_type == "ec2:vpc-flow-log":
        ec2 = boto3.client("ec2", region_name=region)
        ec2.delete_flow_logs(FlowLogIds=[resource_id])
        return f"Deleted VPC flow log {resource_id}"

    if resource_type == "ecr:repository":
        ecr = boto3.client("ecr", region_name=region)
        repo_name = resource_id
        # Without force the service refuses a repository that still holds images.
        ecr.delete_repository(repositoryName=repo_name, force=bool(unrecoverable))
        return f"Deleted ECR repository {repo_name}"

    if resource_type == "events:rule":
        bus, rule_name = _parse_event_rule_arn(arn)
        events_client = boto3.client("events", region_name=region)
        targets = events_client.list_targets_by_rule(Rule=rule_name, EventBusName=bus).get("Targets", [])
        if targets:
            events_client.remove_targets(Rule=rule_name, EventBusName=bus, Ids=[t["Id"] for t in targets], Force=True)
        events_client.delete_rule(Name=rule_name, EventBusName=bus, Force=True)
        return f"Deleted EventBridge rule {rule_name} on bus {bus}"

    if resource_type == "logs:log-group":
        logs = boto3.client("logs", region_name=region)
        name = resource_id
        logs.delete_log_group(logGroupName=name)
        return f"Deleted CloudWatch log group {name}"

    if resource_type == "secretsmanager:secret":
        sm = boto3.client("secretsmanager", region_name=region)
        if unrecoverable:
            sm.delete_secret(SecretId=arn, ForceDeleteWithoutRecovery=True)
            return f"Deleted secret {resource_id}"
        sm.delete_secret(SecretId=arn, RecoveryWindowInDays=30)
        return f"Scheduled secret {resource_id} for deletion (restorable for 30 days)"

    if resource_type == "kms:key":
        kms = boto3.client("kms", region_name=region)
        window = 7 if unrecoverable else 30
        kms.schedule_key_deletion(KeyId=resource_id, PendingWindowInDays=window)
        return f"Scheduled KMS key {resource_id} for deletion in {window} days"

    if resource_type == "iam:role":
        iam = boto3.client("iam")
        role_name = resource_id
        for policy in iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
        for pname in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=pname)
        for ip in iam.list_instance_profiles_for_role(RoleName=role_name).get("InstanceProfiles", []):
            iam.remove_role_from_instance_profile(InstanceProfileName=ip["InstanceProfileName"], RoleName=role_name)
        iam.delete_role(RoleName=role_name)
        return f"Deleted IAM role {role_name}"

    if resource_type == "iam:user":
        iam = boto3.client("iam")
        user_name = resource_id
        for key in iam.list_access_keys(UserName=user_name).get("AccessKeyMetadata", []):
            iam.delete_access_key(UserName=user_name, AccessKeyId=key["AccessKeyId"])
        for policy in iam.list_attached_user_policies(UserName=user_name).get("AttachedPolicies", []):
            iam.detach_user_policy(UserName=user_name, PolicyArn=policy["PolicyArn"])
        for pname in iam.list_user_policies(UserName=user_name).get("PolicyNames", []):
            iam.delete_user_policy(UserName=user_name, PolicyName=pname)
        for group in iam.list_groups_for_user(UserName=user_name).get("Groups", []):
            iam.remove_user_from_group(GroupName=group["GroupName"], UserName=user_name)
        import contextlib

        with contextlib.suppress(iam.exceptions.NoSuchEntityException):
            iam.delete_login_profile(UserName=user_name)
        for mfa in iam.list_mfa_devices(UserName=user_name).get("MFADevices", []):
            iam.deactivate_mfa_device(UserName=user_name, SerialNumber=mfa["SerialNumber"])
            iam.delete_virtual_mfa_device(SerialNumber=mfa["SerialNumber"])
        iam.delete_user(UserName=user_name)
        return f"Deleted IAM user {user_name}"

    if resource_type == "iam:policy":
        iam = boto3.client("iam")
        policy_arn = arn  # IAM policy APIs require the full ARN
        # Detach from all entities (users, groups, roles)
        entities = iam.list_entities_for_policy(PolicyArn=policy_arn)
        for role in entities.get("PolicyRoles", []):
            iam.detach_role_policy(RoleName=role["RoleName"], PolicyArn=policy_arn)
        for user in entities.get("PolicyUsers", []):
            iam.detach_user_policy(UserName=user["UserName"], PolicyArn=policy_arn)
        for group in entities.get("PolicyGroups", []):
            iam.detach_group_policy(GroupName=group["GroupName"], PolicyArn=policy_arn)
        # Delete non-default policy versions
        versions = iam.list_policy_versions(PolicyArn=policy_arn)
        for v in versions.get("Versions", []):
            if not v["IsDefaultVersion"]:
                iam.delete_policy_version(PolicyArn=policy_arn, VersionId=v["VersionId"])
        iam.delete_policy(PolicyArn=policy_arn)
        return f"Deleted IAM policy {resource_id}"

    if resource_type == "route53:hostedzone":
        r53 = boto3.client("route53")
        zone_id = resource_id
        paginator = r53.get_paginator("list_resource_record_sets")
        changes = []
        for page in paginator.paginate(HostedZoneId=zone_id):
            for rr in page["ResourceRecordSets"]:
                if rr["Type"] not in ("NS", "SOA"):
                    changes.append({"Action": "DELETE", "ResourceRecordSet": rr})
        if changes:
            for i in range(0, len(changes), 1000):
                r53.change_resource_record_sets(HostedZoneId=zone_id, ChangeBatch={"Changes": changes[i : i + 1000]})
        r53.delete_hosted_zone(Id=zone_id)
        return f"Deleted Route53 hosted zone {zone_id}"

    if resource_type == "rds:cluster-pg":
        rds = boto3.client("rds", region_name=region)
        rds.delete_db_cluster_parameter_group(DBClusterParameterGroupName=resource_id)
        return f"Deleted RDS cluster parameter group {resource_id}"

    raise ValueError(f"No teardown handler for resource type '{resource_type}'")


def _describe_action(resource_type, arn, region, unrecoverable=False):
    """Describe what teardown action would be taken (for dry-run logging)."""
    resource_id = _extract_id(arn)

    if not unrecoverable:
        recoverable = {
            "ec2:instance": f"snapshot the volumes of EC2 instance {resource_id}, then terminate it",
            "ec2:volume": f"snapshot EBS volume {resource_id}, then delete it",
            "rds:instance": f"delete RDS instance {resource_id} with a final snapshot, automated backups retained",
            "s3:bucket": f"delete S3 bucket {resource_id} only if it is empty",
            "elasticache:cluster": f"delete ElastiCache cluster {resource_id} with a final snapshot",
            "ecr:repository": f"delete ECR repository {resource_id} only if it holds no images",
            "secretsmanager:secret": f"schedule secret {resource_id} for deletion with a 30-day recovery window",
            "kms:key": f"schedule KMS key {resource_id} for deletion in 30 days",
        }
        if resource_type in recoverable:
            return recoverable[resource_type]

    actions = {
        "ec2:instance": f"terminate EC2 instance {resource_id}",
        "ec2:volume": f"delete EBS volume {resource_id}",
        "ec2:security-group": f"delete security group {resource_id}",
        "ec2:security-group-rule": f"revoke security group rule {resource_id}",
        "ec2:eip": f"release EIP {resource_id}",
        "ec2:natgateway": f"delete NAT gateway {resource_id}",
        "ec2:vpc-flow-log": f"delete VPC flow log {resource_id}",
        "rds:instance": f"delete RDS instance {resource_id}",
        "rds:cluster-pg": f"delete RDS cluster parameter group {resource_id}",
        "ecs:service": f"delete ECS service {resource_id}",
        "lambda:function": f"delete Lambda function {resource_id}",
        "s3:bucket": f"delete S3 bucket {resource_id} and all objects",
        "elasticache:cluster": f"delete ElastiCache cluster {resource_id}",
        "elbv2:application": f"delete ALB {resource_id}",
        "elbv2:network": f"delete NLB {resource_id}",
        "ssm:parameter": f"delete SSM parameter {resource_id}",
        "ecr:repository": f"delete ECR repository {resource_id}",
        "events:rule": f"delete EventBridge rule {resource_id}",
        "logs:log-group": f"delete CloudWatch log group {resource_id}",
        "secretsmanager:secret": f"delete secret {resource_id}",
        "kms:key": f"schedule KMS key {resource_id} for deletion",
        "iam:role": f"delete IAM role {resource_id}",
        "iam:user": f"delete IAM user {resource_id}",
        "iam:policy": f"delete IAM policy {resource_id}",
        "route53:hostedzone": f"delete Route53 hosted zone {resource_id}",
    }

    action = actions.get(resource_type)
    if action:
        return action

    if arn and "elasticloadbalancing" in arn and "loadbalancer" in arn:
        return f"delete load balancer {resource_id}"

    return f"skip (no handler for '{resource_type}')"


_EVENT_RULE_ARN_RE = re.compile(
    r"^arn:aws[a-z-]*:events:[a-z0-9-]+:\d{12}:rule/(?:(?P<bus>[A-Za-z0-9._-]{1,256})/)?(?P<name>[A-Za-z0-9._-]{1,64})$"
)


def _parse_event_rule_arn(arn):
    """(event bus name, rule name) of an EventBridge rule ARN.

    `rule/<name>` is a rule on the default bus; `rule/<bus>/<name>` a rule on
    a custom bus. Any other shape is refused, so a delete never reaches a rule
    on a bus other than the one the ARN names.
    """
    match = _EVENT_RULE_ARN_RE.match(arn or "")
    if not match:
        raise ValueError(f"Not an EventBridge rule ARN of a supported form: {arn}")
    return match.group("bus") or "default", match.group("name")


def _extract_id(arn):
    """Extract the resource identifier from an ARN."""
    if not arn:
        return ""
    # arn:aws:service:region:account:type/name or :type:name
    parts = arn.split("/")
    if len(parts) > 1:
        return parts[-1]
    parts = arn.split(":")
    if len(parts) > 5:
        return parts[-1]
    return arn


def _extract_after(arn, marker):
    """Extract everything after a marker string in an ARN (e.g. ':parameter/', ':repository/')."""
    idx = arn.find(marker)
    if idx >= 0:
        return arn[idx + len(marker) :]
    return _extract_id(arn)


def _ssm_parameter_name(arn, name_hint=""):
    """Parameter name for an SSM parameter ARN.

    The ARN drops the leading "/" of a hierarchical name. A name with more
    than one level always starts with "/"; a one-level name keeps the form the
    scanner recorded when that agrees with the ARN.
    """
    marker = ":parameter/"
    if marker not in (arn or ""):
        raise ValueError(f"Not an SSM parameter ARN: {arn}")
    suffix = arn.split(marker, 1)[1]
    if not suffix:
        raise ValueError(f"Not an SSM parameter ARN: {arn}")
    if isinstance(name_hint, str) and name_hint and name_hint.lstrip("/") == suffix:
        return name_hint
    return "/" + suffix if "/" in suffix else suffix


def _extract_log_group_name(arn):
    """Extract log group name — may be a raw name (starts with /) or a full ARN."""
    if not arn.startswith("arn:"):
        return arn
    # arn:aws:logs:region:account:log-group:name:*
    name = _extract_after(arn, ":log-group:")
    return name.rstrip(":*")

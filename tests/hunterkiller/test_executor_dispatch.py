"""Per-type teardown dispatch: which API call, with which identifier, for a given ARN."""

from unittest.mock import MagicMock

import executor
import pytest

ACCOUNT = "123456789012"
REGION = "us-west-2"


@pytest.fixture()
def clients(monkeypatch):
    """Record every boto3 client the executor creates, by service name."""
    made = {}

    def client(name, **kwargs):
        made.setdefault(name, MagicMock(name=name))
        return made[name]

    fake = MagicMock()
    fake.client.side_effect = client
    monkeypatch.setattr(executor, "boto3", fake)
    return made


class TestEventBridgeRule:
    def test_default_bus_rule(self, clients):
        arn = f"arn:aws:events:{REGION}:{ACCOUNT}:rule/nightly-scan"
        clients.setdefault("events", MagicMock()).list_targets_by_rule.return_value = {"Targets": [{"Id": "t1"}]}
        executor._execute_teardown("events:rule", arn, REGION)
        events = clients["events"]
        events.list_targets_by_rule.assert_called_once_with(Rule="nightly-scan", EventBusName="default")
        events.remove_targets.assert_called_once_with(
            Rule="nightly-scan", EventBusName="default", Ids=["t1"], Force=True
        )
        events.delete_rule.assert_called_once_with(Name="nightly-scan", EventBusName="default", Force=True)

    def test_custom_bus_rule_is_deleted_on_its_own_bus(self, clients):
        arn = f"arn:aws:events:{REGION}:{ACCOUNT}:rule/partner-bus/nightly-scan"
        clients.setdefault("events", MagicMock()).list_targets_by_rule.return_value = {"Targets": []}
        executor._execute_teardown("events:rule", arn, REGION)
        events = clients["events"]
        events.list_targets_by_rule.assert_called_once_with(Rule="nightly-scan", EventBusName="partner-bus")
        events.delete_rule.assert_called_once_with(Name="nightly-scan", EventBusName="partner-bus", Force=True)

    @pytest.mark.parametrize(
        "arn",
        [
            f"arn:aws:events:{REGION}:{ACCOUNT}:rule/a/b/c",
            f"arn:aws:events:{REGION}:{ACCOUNT}:rule/",
            f"arn:aws:events:{REGION}:{ACCOUNT}:event-bus/partner-bus",
            f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:nightly-scan",
            "nightly-scan",
            "",
        ],
    )
    def test_other_shapes_are_refused(self, clients, arn):
        with pytest.raises(ValueError):
            executor._execute_teardown("events:rule", arn, REGION)
        assert "events" not in clients or not clients["events"].delete_rule.called


class TestSsmParameter:
    @pytest.mark.parametrize(
        "arn, hint, name",
        [
            (f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/app/db/password", "", "/app/db/password"),
            (f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/app/db/password", "/app/db/password", "/app/db/password"),
            (f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/flat-name", "", "flat-name"),
            (f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/flat-name", "/flat-name", "/flat-name"),
            (f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/flat-name", "/other", "flat-name"),
        ],
    )
    def test_name_keeps_its_leading_slash(self, clients, arn, hint, name):
        executor._execute_teardown("ssm:parameter", arn, REGION, name_hint=hint)
        clients["ssm"].delete_parameter.assert_called_once_with(Name=name)

    @pytest.mark.parametrize("arn", [f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/", "flat-name", ""])
    def test_malformed_arn_refused(self, clients, arn):
        with pytest.raises(ValueError):
            executor._execute_teardown("ssm:parameter", arn, REGION)


class TestAlreadyGone:
    def _error(self, code):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": code, "Message": "x"}}, "Op")

    def test_rejected_ec2_call_is_a_failure_not_a_teardown(self, clients):
        clients.setdefault("ec2", MagicMock()).delete_security_group.side_effect = self._error("InvalidParameterValue")
        record = {
            "resource_arn": f"arn:aws:ec2:{REGION}:{ACCOUNT}:security-group/sg-0123456789abcdef0",
            "resource_type": "ec2:security-group",
            "region": REGION,
        }
        [result] = executor.execute_teardowns([record], dry_run=False)
        assert result["success"] is False

    def test_missing_parameter_counts_as_gone(self, clients):
        clients.setdefault("ssm", MagicMock()).delete_parameter.side_effect = self._error("ParameterNotFound")
        record = {
            "resource_arn": f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/app/x",
            "resource_type": "ssm:parameter",
            "region": REGION,
        }
        [result] = executor.execute_teardowns([record], dry_run=False)
        assert result["success"] is True


P = f"{REGION}:{ACCOUNT}"

# type, ARN, client, destructive method, the identifier keyword arguments it must get
WELL_FORMED = [
    (
        "ec2:instance",
        f"arn:aws:ec2:{P}:instance/i-0123456789abcdef0",
        "ec2",
        "terminate_instances",
        {"InstanceIds": ["i-0123456789abcdef0"]},
    ),
    (
        "ec2:volume",
        f"arn:aws:ec2:{P}:volume/vol-0123456789abcdef0",
        "ec2",
        "delete_volume",
        {"VolumeId": "vol-0123456789abcdef0"},
    ),
    (
        "ec2:security-group",
        f"arn:aws:ec2:{P}:security-group/sg-0123456789abcdef0",
        "ec2",
        "delete_security_group",
        {"GroupId": "sg-0123456789abcdef0"},
    ),
    (
        "ec2:eip",
        f"arn:aws:ec2:{P}:elastic-ip/eipalloc-0123456789abcdef0",
        "ec2",
        "release_address",
        {"AllocationId": "eipalloc-0123456789abcdef0"},
    ),
    (
        "ec2:natgateway",
        f"arn:aws:ec2:{P}:natgateway/nat-0123456789abcdef0",
        "ec2",
        "delete_nat_gateway",
        {"NatGatewayId": "nat-0123456789abcdef0"},
    ),
    (
        "ec2:vpc-flow-log",
        f"arn:aws:ec2:{P}:vpc-flow-log/fl-0123456789abcdef0",
        "ec2",
        "delete_flow_logs",
        {"FlowLogIds": ["fl-0123456789abcdef0"]},
    ),
    (
        "rds:cluster-pg",
        f"arn:aws:rds:{P}:cluster-pg:app-params",
        "rds",
        "delete_db_cluster_parameter_group",
        {"DBClusterParameterGroupName": "app-params"},
    ),
    (
        "ecs:service",
        f"arn:aws:ecs:{P}:service/main-cluster/web",
        "ecs",
        "delete_service",
        {"cluster": "main-cluster", "service": "web", "force": True},
    ),
    ("lambda:function", f"arn:aws:lambda:{P}:function:worker", "lambda", "delete_function", {"FunctionName": "worker"}),
    (
        "elbv2:application",
        f"arn:aws:elasticloadbalancing:{P}:loadbalancer/app/web/0123456789abcdef",
        "elbv2",
        "delete_load_balancer",
        {"LoadBalancerArn": f"arn:aws:elasticloadbalancing:{P}:loadbalancer/app/web/0123456789abcdef"},
    ),
    ("ssm:parameter", f"arn:aws:ssm:{P}:parameter/app/token", "ssm", "delete_parameter", {"Name": "/app/token"}),
    (
        "ecr:repository",
        f"arn:aws:ecr:{P}:repository/team/app",
        "ecr",
        "delete_repository",
        {"repositoryName": "team/app", "force": False},
    ),
    (
        "events:rule",
        f"arn:aws:events:{P}:rule/nightly",
        "events",
        "delete_rule",
        {"Name": "nightly", "EventBusName": "default", "Force": True},
    ),
    (
        "logs:log-group",
        f"arn:aws:logs:{P}:log-group:/aws/lambda/worker",
        "logs",
        "delete_log_group",
        {"logGroupName": "/aws/lambda/worker"},
    ),
    (
        "logs:log-group",
        f"arn:aws:logs:{P}:log-group:/aws/lambda/worker:*",
        "logs",
        "delete_log_group",
        {"logGroupName": "/aws/lambda/worker"},
    ),
    (
        "secretsmanager:secret",
        f"arn:aws:secretsmanager:{P}:secret:app/key-AbCdEf",
        "secretsmanager",
        "delete_secret",
        {"SecretId": f"arn:aws:secretsmanager:{P}:secret:app/key-AbCdEf", "RecoveryWindowInDays": 30},
    ),
    (
        "kms:key",
        f"arn:aws:kms:{P}:key/12345678-90ab-cdef-1234-567890abcdef",
        "kms",
        "schedule_key_deletion",
        {"KeyId": "12345678-90ab-cdef-1234-567890abcdef", "PendingWindowInDays": 30},
    ),
    ("iam:role", f"arn:aws:iam::{ACCOUNT}:role/service/app-runner", "iam", "delete_role", {"RoleName": "app-runner"}),
    ("iam:user", f"arn:aws:iam::{ACCOUNT}:user/ci-bot", "iam", "delete_user", {"UserName": "ci-bot"}),
    (
        "iam:policy",
        f"arn:aws:iam::{ACCOUNT}:policy/app-read",
        "iam",
        "delete_policy",
        {"PolicyArn": f"arn:aws:iam::{ACCOUNT}:policy/app-read"},
    ),
    (
        "route53:hostedzone",
        "arn:aws:route53:::hostedzone/ZEXAMPLE01",
        "route53",
        "delete_hosted_zone",
        {"Id": "ZEXAMPLE01"},
    ),
]


class TestTypesWithLookups:
    def test_rds_instance(self, clients):
        rds = clients.setdefault("rds", MagicMock())
        rds.describe_db_instances.return_value = {"DBInstances": [{}]}
        executor._execute_teardown("rds:instance", f"arn:aws:rds:{P}:db:orders", REGION)
        rds.describe_db_instances.assert_called_once_with(DBInstanceIdentifier="orders")
        assert rds.delete_db_instance.call_args.kwargs["DBInstanceIdentifier"] == "orders"

    def test_cache_cluster(self, clients):
        cache = clients.setdefault("elasticache", MagicMock())
        cache.describe_cache_clusters.return_value = {"CacheClusters": [{"Engine": "redis"}]}
        executor._execute_teardown("elasticache:cluster", f"arn:aws:elasticache:{P}:cluster:sessions", REGION)
        assert cache.delete_cache_cluster.call_args.kwargs["CacheClusterId"] == "sessions"

    def test_security_group_rule(self, clients):
        ec2 = clients.setdefault("ec2", MagicMock())
        ec2.describe_security_group_rules.return_value = {
            "SecurityGroupRules": [{"GroupId": "sg-0123456789abcdef0", "IsEgress": False}]
        }
        executor._execute_teardown(
            "ec2:security-group-rule", f"arn:aws:ec2:{P}:security-group-rule/sgr-0123456789abcdef0", REGION
        )
        ec2.describe_security_group_rules.assert_called_once_with(SecurityGroupRuleIds=["sgr-0123456789abcdef0"])
        ec2.revoke_security_group_ingress.assert_called_once_with(
            GroupId="sg-0123456789abcdef0", SecurityGroupRuleIds=["sgr-0123456789abcdef0"]
        )

    def test_s3_bucket(self, monkeypatch):
        fake = MagicMock()
        bucket = fake.resource.return_value.Bucket.return_value
        bucket.object_versions.limit.return_value = []
        monkeypatch.setattr(executor, "boto3", fake)
        executor._execute_teardown("s3:bucket", "arn:aws:s3:::empty-bucket", REGION)
        fake.resource.return_value.Bucket.assert_called_once_with("empty-bucket")
        bucket.delete.assert_called_once_with()


def _quiet_lookups(clients):
    """Answer the lookups some handlers make before the removal."""
    ec2 = clients.setdefault("ec2", MagicMock())
    ec2.describe_instances.return_value = {"Reservations": []}
    ec2.describe_snapshots.return_value = {
        "Snapshots": [{"SnapshotId": "snap-0123456789abcdef0", "State": "completed"}]
    }
    clients.setdefault("events", MagicMock()).list_targets_by_rule.return_value = {"Targets": []}
    iam = clients.setdefault("iam", MagicMock())
    for method, key in (
        ("list_attached_role_policies", "AttachedPolicies"),
        ("list_role_policies", "PolicyNames"),
        ("list_instance_profiles_for_role", "InstanceProfiles"),
        ("list_access_keys", "AccessKeyMetadata"),
        ("list_attached_user_policies", "AttachedPolicies"),
        ("list_user_policies", "PolicyNames"),
        ("list_groups_for_user", "Groups"),
        ("list_mfa_devices", "MFADevices"),
        ("list_policy_versions", "Versions"),
    ):
        getattr(iam, method).return_value = {key: []}
    iam.list_entities_for_policy.return_value = {"PolicyRoles": [], "PolicyUsers": [], "PolicyGroups": []}
    clients.setdefault("route53", MagicMock()).get_paginator.return_value.paginate.return_value = [
        {"ResourceRecordSets": []}
    ]


class TestEveryHandledType:
    @pytest.mark.parametrize(
        "rtype, arn, service, method, kwargs", WELL_FORMED, ids=[f"{w[0]}:{i}" for i, w in enumerate(WELL_FORMED)]
    )
    def test_well_formed_arn_reaches_the_right_call_with_its_identifier(
        self, clients, rtype, arn, service, method, kwargs
    ):
        _quiet_lookups(clients)
        executor._execute_teardown(rtype, arn, REGION)
        call = getattr(clients[service], method)
        call.assert_called_once()
        assert call.call_args.kwargs == kwargs

    @pytest.mark.parametrize(
        "rtype, arn",
        [
            ("ec2:instance", f"arn:aws:ec2:{P}:instance/"),
            ("ec2:instance", f"arn:aws:ec2:{P}:volume/vol-0123456789abcdef0"),
            ("ec2:instance", "i-0123456789abcdef0"),
            ("ec2:volume", f"arn:aws:ec2:{P}:volume/vol-0123456789abcdef0/extra"),
            ("ec2:security-group", f"arn:aws:ec2:{P}:security-group/default"),
            ("ec2:eip", f"arn:aws:ec2:{P}:elastic-ip/unknown"),
            ("ec2:security-group-rule", f"arn:aws:ec2:{P}:security-group-rule/"),
            ("rds:instance", f"arn:aws:rds:{P}:db:"),
            ("rds:cluster-pg", f"arn:aws:rds:{P}:cluster-pg:../x"),
            ("ecs:service", f"arn:aws:ecs:{P}:service/web"),
            ("lambda:function", f"arn:aws:lambda:{P}:function:worker:PROD"),
            ("s3:bucket", "arn:aws:s3:::Bad_Bucket"),
            ("s3:bucket", "arn:aws:s3:::bucket/key"),
            ("elasticache:cluster", f"arn:aws:elasticache:{P}:cluster:"),
            ("elbv2:application", f"arn:aws:elasticloadbalancing:{P}:targetgroup/web/0123456789abcdef"),
            ("ssm:parameter", f"arn:aws:ssm:{P}:parameter/"),
            ("ecr:repository", f"arn:aws:ecr:{P}:repository/"),
            ("logs:log-group", "/aws/lambda/worker"),
            ("secretsmanager:secret", f"arn:aws:secretsmanager:{P}:secret:"),
            ("kms:key", f"arn:aws:kms:{P}:key/alias-name"),
            ("kms:key", f"arn:aws:kms:{P}:alias/app"),
            ("iam:role", f"arn:aws:iam::{ACCOUNT}:role/"),
            ("iam:policy", "arn:aws:iam::aws:policy/AdministratorAccess"),
            ("iam:user", f"arn:aws:iam::{ACCOUNT}:group/admins"),
            ("route53:hostedzone", "arn:aws:route53:::hostedzone/../other"),
            ("ec2:instance", f"arn:aws:ec2:{REGION}:12345:instance/i-0123456789abcdef0"),
            ("ec2:instance", f"arn:aws:ec2:evil.example:{ACCOUNT}:instance/i-0123456789abcdef0"),
            ("unknown:type", f"arn:aws:ec2:{P}:instance/i-0123456789abcdef0"),
        ],
    )
    def test_malformed_arn_is_refused_before_any_client(self, clients, rtype, arn):
        with pytest.raises(ValueError):
            executor._execute_teardown(rtype, arn, REGION)
        assert clients == {}

    def test_record_with_a_mismatched_arn_fails_and_calls_nothing(self, clients):
        record = {
            "resource_arn": f"arn:aws:iam::{ACCOUNT}:role/app",
            "resource_type": "ec2:instance",
            "region": REGION,
        }
        [result] = executor.execute_teardowns([record], dry_run=False)
        assert result["success"] is False
        assert clients == {}

#!/usr/bin/env python3
"""Build a synthetic estate for the demo dashboard.

Writes, into --out (default: ./data beside this file):

  compliance-report.json   a scan report (schema 1.0) for the invented company "acme"
  previous-report.json     the report one day earlier, for the changes view
  terraform.tfstate        a Terraform state for the invented platform repository
  enforcement-state.json   warning and approval records, as the notifier writes them

Everything is invented: account 123456789012, region us-east-1, *.example
domains. The output is the same for the same --as-of date.

    python3 generate.py                      # as of today
    python3 generate.py --as-of 2030-03-14   # fixed date
"""

import argparse
import json
import os
import random
from datetime import UTC, datetime, timedelta

ACCOUNT = "123456789012"
REGION = "us-east-1"
PROJECT = "acme"
ENVIRONMENT = "dev"
GIT = "https://git.acme.example/acme"

# repository slug -> (IaC tool, lifecycle, where its state lives)
REPOS = {
    "platform-infra": (
        "terraform",
        "persistent",
        {"type": "s3_tfstate", "bucket": "acme-tf-state", "key": "platform-infra/dev/terraform.tfstate"},
    ),
    "storefront-api": (
        "terraform",
        "persistent",
        {"type": "s3_tfstate", "bucket": "acme-tf-state", "key": "storefront-api/dev/terraform.tfstate"},
    ),
    "checkout-service": ("serverless", "persistent", {"type": "cfn_stack", "stack_names": ["checkout-service-dev"]}),
    "data-pipeline": (
        "pulumi",
        "persistent",
        {"type": "pulumi_s3", "bucket": "acme-pulumi-state", "project": "data-pipeline"},
    ),
    "marketing-site": ("cdk", "persistent", {"type": "cfn_stack", "stack_names": ["marketing-site-dev"]}),
    "load-test-lab": (
        "terraform",
        "temporary",
        {"type": "s3_tfstate", "bucket": "acme-tf-state", "key": "load-test-lab/dev/terraform.tfstate"},
    ),
    "search-prototype": (
        "terraform",
        "temporary",
        {"type": "s3_tfstate", "bucket": "acme-tf-state", "key": "search-prototype/dev/terraform.tfstate"},
    ),
}

EC2_PRICE = {"t3.micro": 7.59, "t3.small": 15.18, "t3.medium": 30.37, "m5.large": 70.08, "c5.xlarge": 124.10}
FIXED = {
    "ec2:natgateway": 32.85,
    "elbv2:application": 16.43,
    "elasticache:cluster": 12.41,
    "kms:key": 1.00,
    "secretsmanager:secret": 0.40,
    "route53:hostedzone": 0.50,
    "dynamodb:table": 0.50,
    "lambda:function": 0.0,
    "ec2:security-group": 0.0,
    "ec2:vpc": 0.0,
    "ec2:subnet": 0.0,
    "iam:role": 0.0,
    "acm:certificate": 0.0,
    "ecs:cluster": 0.0,
}
UNPRICED = {
    "sns:topic",
    "sqs:queue",
    "apigateway:api",
    "cloudtrail:trail",
    "events:rule",
    "states:statemachine",
    "cloudfront:distribution",
}


class Estate:
    def __init__(self, now, seed=7):
        self.now = now
        self.rnd = random.Random(seed)
        self.resources = []
        self.tf_platform = []  # (module, tf_type, tf_name, resource)
        self._n = 0

    def _hex(self, n):
        return "".join(self.rnd.choice("0123456789abcdef") for _ in range(n))

    def arn(self, rtype, name):
        a, r = ACCOUNT, REGION
        if rtype == "ec2:instance":
            return f"arn:aws:ec2:{r}:{a}:instance/i-0{self._hex(16)}"
        if rtype == "ec2:volume":
            return f"arn:aws:ec2:{r}:{a}:volume/vol-0{self._hex(16)}"
        if rtype == "ec2:security-group":
            return f"arn:aws:ec2:{r}:{a}:security-group/sg-0{self._hex(16)}"
        if rtype == "ec2:vpc":
            return f"arn:aws:ec2:{r}:{a}:vpc/vpc-0{self._hex(16)}"
        if rtype == "ec2:subnet":
            return f"arn:aws:ec2:{r}:{a}:subnet/subnet-0{self._hex(16)}"
        if rtype == "ec2:natgateway":
            return f"arn:aws:ec2:{r}:{a}:natgateway/nat-0{self._hex(16)}"
        if rtype == "ec2:eip":
            return f"arn:aws:ec2:{r}:{a}:elastic-ip/eipalloc-0{self._hex(16)}"
        if rtype == "s3:bucket":
            return f"arn:aws:s3:::{name}"
        if rtype == "iam:role":
            return f"arn:aws:iam::{a}:role/{name}"
        if rtype == "route53:hostedzone":
            return f"arn:aws:route53:::hostedzone/Z0{self._hex(12).upper()}"
        if rtype == "cloudfront:distribution":
            return f"arn:aws:cloudfront::{a}:distribution/E{self._hex(12).upper()}"
        if rtype == "lambda:function":
            return f"arn:aws:lambda:{r}:{a}:function:{name}"
        if rtype in ("rds:db", "rds:instance"):
            return f"arn:aws:rds:{r}:{a}:db:{name}"
        if rtype == "ecs:service":
            return f"arn:aws:ecs:{r}:{a}:service/acme-dev/{name}"
        if rtype == "ecs:cluster":
            return f"arn:aws:ecs:{r}:{a}:cluster/{name}"
        if rtype == "elasticache:cluster":
            return f"arn:aws:elasticache:{r}:{a}:cluster:{name}"
        if rtype in ("elbv2:application", "elasticloadbalancing:loadbalancer"):
            return f"arn:aws:elasticloadbalancing:{r}:{a}:loadbalancer/app/{name}/{self._hex(16)}"
        if rtype == "dynamodb:table":
            return f"arn:aws:dynamodb:{r}:{a}:table/{name}"
        if rtype == "logs:log-group":
            return f"arn:aws:logs:{r}:{a}:log-group:{name}"
        if rtype == "secretsmanager:secret":
            return f"arn:aws:secretsmanager:{r}:{a}:secret:{name}-{self._hex(6)}"
        if rtype == "kms:key":
            return (
                f"arn:aws:kms:{r}:{a}:key/{self._hex(8)}-{self._hex(4)}-{self._hex(4)}-{self._hex(4)}-{self._hex(12)}"
            )
        if rtype == "ssm:parameter":
            return f"arn:aws:ssm:{r}:{a}:parameter{name}"
        if rtype == "ecr:repository":
            return f"arn:aws:ecr:{r}:{a}:repository/{name}"
        if rtype == "acm:certificate":
            return f"arn:aws:acm:{r}:{a}:certificate/{self._hex(8)}-{self._hex(4)}-{self._hex(4)}-{self._hex(4)}-{self._hex(12)}"
        service = rtype.split(":")[0]
        return f"arn:aws:{service}:{r}:{a}:{name}"

    def tags(self, repo, service=None, **extra):
        tool, lifecycle, _ = REPOS[repo]
        t = {
            "Project": PROJECT,
            "Environment": ENVIRONMENT,
            "ManagedBy": tool,
            "Lifecycle": lifecycle,
            "Repository": f"{GIT}/{repo}",
        }
        if service:
            t["Service"] = service
        t.update(extra)
        return t

    def add(self, rtype, name, repo=None, tags=None, cost="auto", state="active", days=None, tf=None, **extra):
        """Add one resource. repo=None means no repository claims it."""
        if tags is None:
            tags = self.tags(repo, extra.pop("service", None)) if repo else {}
        else:
            extra.pop("service", None)
        tags = dict(tags)
        tags.setdefault("Name", name)
        if cost == "auto":
            if rtype in UNPRICED:
                cost = None
            elif rtype == "ec2:instance":
                cost = EC2_PRICE[extra.get("instance_type", "t3.small")] if state == "running" else 0.0
            elif rtype in FIXED:
                cost = FIXED[rtype]
            else:
                cost = 0.0
        days = days if days is not None else self.rnd.randint(40, 420)
        res = {
            "arn": self.arn(rtype, name),
            "resource_type": rtype,
            "name": name,
            "region": "global" if rtype in ("iam:role", "route53:hostedzone", "cloudfront:distribution") else REGION,
            "state": state,
            "tags": tags,
            "estimated_monthly_cost": cost,
            "instance_type": extra.get("instance_type", ""),
            "instance_class": extra.get("instance_class", ""),
            "created_at": (self.now - timedelta(days=days)).isoformat(),
            "created_by": extra.get("created_by"),
            "running_duration_days": days,
            "inferred_purpose": extra.get("purpose"),
            "attached_to": None,
            "_repo": repo,
            "_in_state": extra.get("in_state", repo is not None),
        }
        self.resources.append(res)
        if tf:
            self.tf_platform.append((*tf, res))
        return res


def build(now):
    e = Estate(now)
    add = e.add

    # --- platform-infra: network, shared services and the governance tooling itself
    p = "platform-infra"
    add("ec2:vpc", "acme-dev", p, service="network", tf=("module.network", "aws_vpc", "main"))
    for az in "ab":
        add(
            "ec2:subnet",
            f"acme-dev-public-{az}",
            p,
            service="network",
            tf=("module.network", "aws_subnet", f"public_{az}"),
        )
        add(
            "ec2:subnet",
            f"acme-dev-private-{az}",
            p,
            service="network",
            tf=("module.network", "aws_subnet", f"private_{az}"),
        )
    add(
        "ec2:natgateway",
        "acme-dev-nat",
        p,
        service="network",
        state="available",
        tf=("module.network", "aws_nat_gateway", "main"),
    )
    add(
        "logs:log-group",
        "/acme/dev/vpc-flow",
        p,
        service="network",
        cost=2.14,
        tf=("module.network", "aws_cloudwatch_log_group", "flow"),
    )
    for n in ("alb", "ecs-tasks", "database", "cache", "bastion"):
        add(
            "ec2:security-group",
            f"acme-dev-{n}",
            p,
            service="network",
            tf=("module.network", "aws_security_group", n.replace("-", "_")),
        )
    add(
        "elasticloadbalancing:loadbalancer",
        "acme-dev-alb",
        p,
        cost=16.43,
        service="edge",
        tf=("module.edge", "aws_lb", "main"),
    )
    add(
        "acm:certificate",
        "dev.acme.example",
        p,
        service="edge",
        state="ISSUED",
        tf=("module.edge", "aws_acm_certificate", "main"),
    )
    add("route53:hostedzone", "dev.acme.example", p, service="edge", tf=("module.edge", "aws_route53_zone", "main"))
    add("ecs:cluster", "acme-dev", p, service="compute", tf=("module.cluster", "aws_ecs_cluster", "main"))
    add(
        "logs:log-group",
        "/acme/dev/ecs",
        p,
        service="compute",
        cost=4.71,
        tf=("module.cluster", "aws_cloudwatch_log_group", "ecs"),
    )
    add("iam:role", "acme-dev-ecs-execution", p, service="compute", tf=("module.cluster", "aws_iam_role", "execution"))
    add("iam:role", "acme-dev-ecs-task", p, service="compute", tf=("module.cluster", "aws_iam_role", "task"))
    add(
        "kms:key",
        "acme-dev-data",
        p,
        service="security",
        state="Enabled",
        tf=("module.security", "aws_kms_key", "data"),
    )
    add("cloudtrail:trail", "acme-dev-trail", p, service="security", tf=("module.security", "aws_cloudtrail", "main"))
    add(
        "s3:bucket",
        "acme-dev-cloudtrail-logs",
        p,
        service="security",
        cost=1.92,
        tf=("module.security", "aws_s3_bucket", "trail"),
    )
    add(
        "sns:topic",
        "acme-dev-governance-alerts",
        p,
        service="governance",
        tf=("module.budgets", "aws_sns_topic", "alerts"),
    )
    add(
        "lambda:function",
        "acme-dev-lifecycle-manager",
        p,
        service="governance",
        tf=("module.budgets", "aws_lambda_function", "lifecycle"),
    )
    add(
        "lambda:function",
        "acme-dev-compliance-scanner",
        p,
        service="governance",
        tf=("module.scanner", "aws_lambda_function", "scanner"),
    )
    add(
        "s3:bucket",
        "acme-dev-compliance-reports",
        p,
        service="governance",
        cost=0.38,
        tf=("module.scanner", "aws_s3_bucket", "reports"),
    )
    add(
        "dynamodb:table",
        "acme-dev-compliance-report-data",
        p,
        service="governance",
        tf=("module.scanner", "aws_dynamodb_table", "report"),
    )
    add(
        "events:rule",
        "acme-dev-nightly-scan",
        p,
        service="governance",
        tf=("module.scanner", "aws_cloudwatch_event_rule", "nightly"),
    )
    add(
        "lambda:function",
        "acme-dev-hunterkiller",
        p,
        service="governance",
        tf=("module.enforcement", "aws_lambda_function", "notifier"),
    )
    add(
        "dynamodb:table",
        "acme-dev-hunterkiller-state",
        p,
        service="governance",
        tf=("module.enforcement", "aws_dynamodb_table", "state"),
    )
    add(
        "lambda:function",
        "acme-dev-stack-reaper-reconciler",
        p,
        service="governance",
        tf=("module.expiry_teardown", "aws_lambda_function", "reconciler"),
    )
    add(
        "dynamodb:table",
        "acme-dev-stack-reaper-state",
        p,
        service="governance",
        tf=("module.expiry_teardown", "aws_dynamodb_table", "state"),
    )
    add(
        "lambda:function",
        "acme-dev-compliance-dashboard",
        p,
        service="governance",
        tf=("module.dashboard", "aws_lambda_function", "dashboard"),
    )
    add(
        "dynamodb:table",
        "acme-dev-compliance-overrides",
        p,
        service="governance",
        tf=("module.dashboard", "aws_dynamodb_table", "overrides"),
    )
    add("s3:bucket", "acme-tf-state", p, service="state", cost=0.21, tf=("module.bootstrap", "aws_s3_bucket", "state"))
    add("dynamodb:table", "acme-tf-lock", p, service="state", tf=("module.bootstrap", "aws_dynamodb_table", "lock"))
    add(
        "ec2:instance",
        "acme-dev-bastion",
        p,
        service="access",
        state="running",
        instance_type="t3.micro",
        tf=("module.bastion", "aws_instance", "main"),
    )
    add(
        "ec2:volume",
        "acme-dev-bastion-root",
        p,
        service="access",
        state="in-use",
        cost=0.76,
        tf=("module.bastion", "aws_ebs_volume", "root"),
    )
    for n in ("acme-dev-alerts-high", "acme-dev-alerts-low"):
        add("sns:topic", n, p, service="monitoring", tf=("module.alarms", "aws_sns_topic", n.rsplit("-", 1)[1]))
    for n in ("alb-5xx", "ecs-cpu", "rds-storage"):
        add(
            "cloudwatch:alarm",
            f"acme-dev-{n}",
            p,
            service="monitoring",
            cost=0.10,
            state="OK",
            tf=("module.alarms", "aws_cloudwatch_metric_alarm", n.replace("-", "_")),
        )

    for n in ("alb-arn", "vpc-id", "private-subnets", "ecs-cluster", "compliance-contract", "protected-resources"):
        add(
            "ssm:parameter",
            f"/acme/dev/{n}",
            p,
            service="platform",
            tf=("module.platform_params", "aws_ssm_parameter", n.replace("-", "_")),
        )
    for slug in REPOS:
        add("ssm:parameter", f"/acme/dev/iac-registry/{slug}", slug, service="registry")
    for n, mod in (
        ("compliance-scanner", "scanner"),
        ("compliance-dashboard", "dashboard"),
        ("hunterkiller", "enforcement"),
        ("stack-reaper-reconciler", "expiry_teardown"),
    ):
        add(
            "logs:log-group",
            f"/aws/lambda/acme-dev-{n}",
            p,
            service="governance",
            cost=round(e.rnd.uniform(0.05, 0.9), 2),
            tf=(f"module.{mod}", "aws_cloudwatch_log_group", "lambda"),
        )

    # --- storefront-api: a container service with a database and a cache
    s = "storefront-api"
    add("ecs:service", "storefront-api", s, service="storefront", state="running", cost=58.40)
    add("ecs:service", "storefront-worker", s, service="storefront", state="running", cost=29.20)
    add(
        "rds:db",
        "storefront-dev",
        s,
        service="storefront",
        state="available",
        cost=52.56,
        instance_class="db.t3.medium",
    )
    add("elasticache:cluster", "storefront-dev-cache", s, service="storefront", state="available")
    add("ecr:repository", "acme/storefront-api", s, service="storefront", cost=0.64)
    add("ecr:repository", "acme/storefront-worker", s, service="storefront", cost=0.31)
    for n in ("storefront-db-credentials", "storefront-session-key", "storefront-payments-token"):
        add("secretsmanager:secret", n, s, service="storefront")
    for n in ("/acme/dev/storefront-api", "/acme/dev/storefront-worker"):
        add("logs:log-group", n, s, service="storefront", cost=round(e.rnd.uniform(1.5, 6), 2))
    add("ec2:security-group", "storefront-dev-service", s, service="storefront")
    add("iam:role", "storefront-dev-task", s, service="storefront")
    add("s3:bucket", "acme-dev-storefront-uploads", s, service="storefront", cost=7.83)
    add("sqs:queue", "storefront-dev-orders", s, service="storefront")
    add("sqs:queue", "storefront-dev-orders-dlq", s, service="storefront")
    add("dynamodb:table", "storefront-dev-carts", s, service="storefront", cost=3.12)
    add("cloudwatch:alarm", "storefront-dev-latency", s, service="storefront", cost=0.10, state="OK")

    # --- checkout-service: Serverless Framework, one CloudFormation stack
    c = "checkout-service"
    for n in ("create-order", "capture-payment", "refund", "webhook", "receipt-mailer", "reconcile"):
        add("lambda:function", f"checkout-service-dev-{n}", c, service="checkout")
        add(
            "logs:log-group",
            f"/aws/lambda/checkout-service-dev-{n}",
            c,
            service="checkout",
            cost=round(e.rnd.uniform(0.1, 1.4), 2),
        )
    add("apigateway:api", "dev-checkout-service", c, service="checkout")
    add("dynamodb:table", "checkout-dev-orders", c, service="checkout", cost=4.85)
    add("dynamodb:table", "checkout-dev-idempotency", c, service="checkout", cost=0.92)
    add("sqs:queue", "checkout-dev-events", c, service="checkout")
    add("iam:role", "checkout-service-dev-us-east-1-lambdaRole", c, service="checkout")
    add("s3:bucket", "checkout-service-dev-deployment", c, service="checkout", cost=0.04)

    # --- data-pipeline: Pulumi, state in S3
    d = "data-pipeline"
    for n in ("acme-dev-raw-events", "acme-dev-curated", "acme-dev-athena-results"):
        add("s3:bucket", n, d, service="pipeline", cost=round(e.rnd.uniform(3, 41), 2))
    for n in ("ingest", "transform", "export"):
        add("lambda:function", f"pipeline-dev-{n}", d, service="pipeline")
    add("states:statemachine", "pipeline-dev-nightly", d, service="pipeline")
    add("events:rule", "pipeline-dev-nightly-trigger", d, service="pipeline")
    batch = add(
        "ec2:instance",
        "pipeline-dev-batch-worker",
        d,
        service="pipeline",
        state="running",
        instance_type="m5.large",
        tags={**e.tags(d, "pipeline"), "Lifecycle": "experiment"},
        days=96,
    )
    add("ec2:volume", "pipeline-dev-batch-scratch", d, service="pipeline", state="in-use", cost=19.00)
    add("iam:role", "pipeline-dev-lambda", d, service="pipeline")
    add("kms:key", "pipeline-dev", d, service="pipeline", state="Enabled")
    add("s3:bucket", "acme-pulumi-state", d, service="state", cost=0.09)

    # --- marketing-site: CDK
    m = "marketing-site"
    add("s3:bucket", "acme-dev-marketing-site", m, service="marketing", cost=0.47)
    add("cloudfront:distribution", "www.dev.acme.example", m, service="marketing")
    add("lambda:function", "marketing-site-dev-redirects", m, service="marketing")
    add("lambda:function", "marketing-site-dev-contact-form", m, service="marketing")
    add("acm:certificate", "www.dev.acme.example", m, service="marketing", state="ISSUED")
    add("iam:role", "marketing-site-dev-edge", m, service="marketing")
    add("logs:log-group", "/aws/lambda/marketing-site-dev-contact-form", m, service="marketing", cost=0.12)

    # --- load-test-lab: a temporary stack whose expiry has passed
    lab = "load-test-lab"
    expired = (now - timedelta(days=3)).replace(hour=18, minute=0, second=0, microsecond=0)
    lab_tags = {
        **e.tags(lab, "load-test"),
        "ExpiresAt": expired.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Contact": "perf-team@acme.example",
    }
    lab_res = []
    for i in range(1, 5):
        lab_res.append(
            add(
                "ec2:instance", f"load-gen-{i}", lab, tags=lab_tags, state="running", instance_type="c5.xlarge", days=17
            )
        )
        add("ec2:volume", f"load-gen-{i}-root", lab, tags=lab_tags, state="in-use", cost=7.60, days=17)
    add("ec2:security-group", "load-test-lab", lab, tags=lab_tags, days=17)
    lab_res.append(
        add("elasticloadbalancing:loadbalancer", "load-test-target", lab, tags=lab_tags, cost=16.43, days=17)
    )
    add("s3:bucket", "acme-dev-load-test-results", lab, tags=lab_tags, cost=1.14, days=17)

    # --- search-prototype: a temporary stack that expires in five days
    sp = "search-prototype"
    soon = (now + timedelta(days=5)).replace(hour=12, minute=0, second=0, microsecond=0)
    sp_tags = {
        **e.tags(sp, "search"),
        "ExpiresAt": soon.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Contact": "search-team@acme.example",
    }
    sp_res = [
        add(
            "ec2:instance",
            "search-prototype-node",
            sp,
            tags=sp_tags,
            state="running",
            instance_type="t3.medium",
            days=9,
        ),
        add("ec2:volume", "search-prototype-data", sp, tags=sp_tags, state="in-use", cost=9.50, days=9),
    ]
    add("ec2:security-group", "search-prototype", sp, tags=sp_tags, days=9)
    add("lambda:function", "search-prototype-indexer", sp, tags=sp_tags, days=9)
    add("s3:bucket", "acme-dev-search-prototype", sp, tags=sp_tags, cost=0.22, days=9)

    # --- resources no repository claims
    def untagged(rtype, name, why_cost="auto", who=None, days=None, **kw):
        r = add(rtype, name, None, cost=why_cost, created_by=who, days=days, **kw)
        return r

    jenkins = untagged(
        "ec2:instance",
        "jenkins-old",
        who="m.rivera",
        days=412,
        state="running",
        instance_type="m5.large",
        purpose="CI server",
    )
    jroot = untagged("ec2:volume", "jenkins-old-data", 47.50, who="m.rivera", days=412, state="in-use")
    vol1 = untagged("ec2:volume", "vol-unattached-1", 19.00, days=203, state="available")
    vol2 = untagged("ec2:volume", "vol-unattached-2", 9.50, days=131, state="available")
    eip = untagged("ec2:eip", "eipalloc-unattached", 3.65, days=131, state="unassociated")
    wizard = untagged("ec2:security-group", "launch-wizard-3", who="j.okafor", days=131)
    testfn = untagged("lambda:function", "test-fn", who="j.okafor", days=58)
    nat = untagged("ec2:natgateway", "nat-debugging", who="s.lindqvist", days=44, state="available")
    copy = add(
        "rds:instance",
        "analytics-copy",
        None,
        tags={"Name": "analytics-copy", "Owner": "data-team", "Environment": "dev"},
        cost=105.12,
        state="available",
        instance_class="db.m5.large",
        created_by="s.lindqvist",
        days=77,
        purpose="Database copy",
    )
    export = untagged("s3:bucket", "acme-tmp-export", 2.31, who="s.lindqvist", days=77)

    # --- tagged resources with a defect
    hotfix = add(
        "ec2:security-group",
        "storefront-hotfix",
        s,
        service="storefront",
        in_state=False,
        created_by="m.rivera",
        days=12,
    )
    patched = add(
        "lambda:function",
        "checkout-service-dev-manual-replay",
        c,
        service="checkout",
        in_state=False,
        created_by="j.okafor",
        days=6,
    )
    ansible = add(
        "ec2:instance",
        "metrics-relay",
        p,
        tags={**e.tags(p, "monitoring"), "ManagedBy": "ansible"},
        state="running",
        instance_type="t3.small",
        in_state=False,
        days=160,
    )
    badrepo = add("sqs:queue", "pipeline-dev-retries", d, tags={**e.tags(d, "pipeline"), "Repository": "data-pipeline"})
    norepo_tags = e.tags(s, "storefront")
    del norepo_tags["Repository"]
    norepo = add("dynamodb:table", "storefront-dev-feature-flags", s, tags=norepo_tags, cost=0.50)
    staging_db = add(
        "rds:instance",
        "storefront-preview",
        s,
        tags={**e.tags(s, "storefront"), "Lifecycle": "experiment"},
        state="available",
        cost=26.28,
        instance_class="db.t3.small",
        days=64,
    )
    preview = add(
        "ecs:service",
        "storefront-preview",
        s,
        tags={**e.tags(s, "storefront"), "Lifecycle": "experiment"},
        state="running",
        cost=14.60,
        days=64,
    )
    demo_cache = add(
        "elasticache:cluster",
        "demo-cache",
        s,
        tags={**e.tags(s, "storefront"), "Lifecycle": "experiment"},
        state="available",
        cost=6.20,
        days=30,
    )
    temp_noexp = add(
        "ec2:instance",
        "search-prototype-loader",
        sp,
        tags={k: v for k, v in sp_tags.items() if k != "ExpiresAt"},
        state="running",
        instance_type="t3.medium",
        days=48,
    )
    nolife_tags = e.tags(d, "pipeline")
    del nolife_tags["Lifecycle"]
    nolife = add("ec2:volume", "pipeline-dev-archive", d, tags=nolife_tags, state="in-use", cost=28.50, days=140)

    def finding(cat, sev, r, issue, rec, **extra):
        out = {
            "category": cat,
            "severity": sev,
            "resource_arn": r["arn"],
            "resource_type": r["resource_type"],
            "resource_name": r["name"],
            "region": r["region"],
            "state": r["state"],
            "issue": issue,
            "tags": r["tags"],
            "created_at": r["created_at"],
            "created_by": r["created_by"],
            "running_duration_days": r["running_duration_days"],
            "estimated_monthly_cost": r["estimated_monthly_cost"],
            "inferred_purpose": r["inferred_purpose"],
            "attached_to": None,
            "recommendation": rec,
        }
        if r["_repo"]:
            out["managed_by_repo_slug"] = r["_repo"]
        out.update(extra)
        return out

    required = ["Project", "Environment", "ManagedBy", "Lifecycle", "Repository"]
    tag, drift, sched, life = [], [], [], []

    for r in (jenkins, jroot, vol1, wizard, testfn, export):
        tag.append(
            finding(
                "tag_compliance",
                "HIGH",
                r,
                f"Missing required tags: {', '.join(required)}",
                f"Add missing tags: {', '.join(required)}",
                missing_tags=required,
                existing_tags=r["tags"],
            )
        )
    miss = ["Project", "ManagedBy", "Lifecycle", "Repository"]
    tag.append(
        finding(
            "tag_compliance",
            "HIGH",
            copy,
            f"Missing required tags: {', '.join(miss)}",
            f"Add missing tags: {', '.join(miss)}",
            missing_tags=miss,
            existing_tags=copy["tags"],
        )
    )
    tag.append(
        finding(
            "tag_compliance",
            "MEDIUM",
            norepo,
            "Missing required tags: Repository",
            "Add missing tags: Repository",
            missing_tags=["Repository"],
            existing_tags=norepo["tags"],
        )
    )
    tag.append(
        finding(
            "tag_compliance",
            "MEDIUM",
            ansible,
            "ManagedBy tag is 'ansible' (expected one of: cdk, cloudformation, copilot, manual, pulumi, sam, serverless, terraform)",
            "Set ManagedBy to the IaC tool that manages this resource (e.g. 'terraform', 'serverless', 'pulumi').",
        )
    )
    tag.append(
        finding(
            "tag_compliance",
            "MEDIUM",
            badrepo,
            "Repository tag 'data-pipeline' is not a valid URL (expected https:// or git@ scheme)",
            "Set Repository to the full git repo URL, e.g. 'https://github.com/your-org/your-repo'.",
        )
    )

    issue = "Resource exists in AWS but not in Terraform state (possible click-ops)"
    high = "Investigate: this resource appears to be manually created. Import into Terraform state or remove if unused."
    drift.append(
        finding(
            "drift_detection",
            "HIGH",
            jenkins,
            issue,
            "Investigate: running EC2 instance not in Terraform. Import with `terraform import` or terminate if unused.",
            confidence="HIGH",
        )
    )
    drift.append(
        finding(
            "drift_detection",
            "HIGH",
            copy,
            issue,
            "Investigate: RDS instance not in Terraform. This has ongoing cost. Import or decommission.",
            confidence="HIGH",
        )
    )
    for r in (nat, eip, vol2, hotfix):
        drift.append(finding("drift_detection", "HIGH", r, issue, high, confidence="HIGH"))
    med = "Review: this resource may have been created manually or by another automation tool. Verify ownership."
    for r in (patched, ansible):
        drift.append(finding("drift_detection", "MEDIUM", r, issue, med, confidence="MEDIUM"))

    srec = "Add a Schedule tag (e.g., 'businesshours') to enable off-hours shutdown, or set Lifecycle=persistent to opt out of scheduling."
    for r in (batch, staging_db, preview, demo_cache):
        cost = r["estimated_monthly_cost"]
        sched.append(
            finding(
                "schedule_compliance",
                "MEDIUM" if cost > 10 else "LOW",
                r,
                f"Schedulable resource running without schedule tags (cost: ${cost:.2f}/mo, running: {r['running_duration_days']} days)",
                srec,
            )
        )

    for r in lab_res[:3]:
        life.append(
            finding(
                "lifecycle_compliance",
                "HIGH",
                r,
                f"Resource expired 3 days ago (ExpiresAt: {r['tags']['ExpiresAt']})",
                "Delete this resource or update ExpiresAt to extend its lifecycle.",
            )
        )
    for r in sp_res:
        life.append(
            finding(
                "lifecycle_compliance",
                "INFO",
                r,
                f"Resource expires in 5 days (ExpiresAt: {r['tags']['ExpiresAt']})",
                "Extend ExpiresAt if still needed, or plan for teardown.",
            )
        )
    life.append(
        finding(
            "lifecycle_compliance",
            "MEDIUM",
            temp_noexp,
            "Temporary resource running for 48 days without ExpiresAt tag",
            "Add an ExpiresAt tag or change Lifecycle to persistent if this resource is needed long-term.",
        )
    )
    life.append(
        finding(
            "lifecycle_compliance",
            "LOW",
            nolife,
            "Resource running for 140 days without Lifecycle tag (est. $28.50/mo)",
            "Add Lifecycle=persistent if intentional, or Lifecycle=temporary with ExpiresAt if short-lived.",
        )
    )

    return e, {
        "tag_compliance": tag,
        "drift_detection": drift,
        "schedule_compliance": sched,
        "lifecycle_compliance": life,
    }


def report_from(e, findings, now):
    resources = [{k: v for k, v in r.items() if not k.startswith("_")} for r in e.resources]
    all_f = [f for fs in findings.values() for f in fs]
    by_severity = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in all_f:
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1
    type_counts = {}
    for r in resources:
        type_counts[r["resource_type"]] = type_counts.get(r["resource_type"], 0) + 1
    in_state = sum(1 for r in e.resources if r["_in_state"])
    entries = []
    for slug, (tool, _, loc) in REPOS.items():
        entries.append(
            {
                "repo_slug": slug,
                "repo_url": f"{GIT}/{slug}",
                "iac_type": tool,
                "state_location": loc,
                "last_deploy": (now - timedelta(days=e.rnd.randint(1, 20), hours=e.rnd.randint(0, 20))).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "deployer": "github-actions",
                "environment": ENVIRONMENT,
                "version": "1.0",
                "resource_count": sum(1 for r in e.resources if r["_repo"] == slug),
            }
        )
    service_costs = {
        "Amazon Elastic Compute Cloud - Compute": 742.18,
        "EC2 - Other": 171.40,
        "Amazon Relational Database Service": 189.52,
        "Amazon Elastic Container Service": 104.11,
        "Amazon Virtual Private Cloud": 68.77,
        "Amazon Simple Storage Service": 61.03,
        "Amazon Elastic Load Balancing": 33.90,
        "Amazon ElastiCache": 18.94,
        "AmazonCloudWatch": 24.66,
        "Amazon DynamoDB": 11.27,
        "AWS Lambda": 3.48,
        "AWS Key Management Service": 2.00,
        "AWS Secrets Manager": 1.20,
        "Amazon Route 53": 0.50,
        "AWS CloudTrail": 2.35,
    }
    protected = {
        "version": "1.0",
        "resources": [
            {
                "arn_pattern": "arn:aws:s3:::acme-tf-state",
                "category": "terraform_state",
                "action": "never_destroy",
                "reason": "Terraform state for every repository",
            },
            {
                "arn_pattern": f"arn:aws:dynamodb:{REGION}:*:table/acme-tf-lock",
                "category": "terraform_state",
                "action": "never_destroy",
                "reason": "Terraform state lock table",
            },
            {
                "arn_pattern": "arn:aws:s3:::acme-pulumi-state",
                "category": "terraform_state",
                "action": "never_destroy",
                "reason": "Pulumi state",
            },
            {
                "arn_pattern": "arn:aws:s3:::acme-dev-cloudtrail-logs",
                "category": "data",
                "action": "never_destroy",
                "reason": "Audit log archive",
            },
            {
                "arn_pattern": "arn:aws:s3:::acme-dev-compliance-reports",
                "category": "data",
                "action": "never_destroy",
                "reason": "Scan report history",
            },
            {
                "arn_pattern": "arn:aws:s3:::acme-dev-storefront-uploads",
                "category": "data",
                "action": "never_destroy",
                "reason": "Customer uploads",
            },
        ],
    }
    return {
        "report_version": "1.0",
        "generated_at": now.replace(hour=3, minute=0, second=41, microsecond=0).isoformat(),
        "scan_duration_seconds": 38.2,
        "account_id": ACCOUNT,
        "project": PROJECT,
        "environment": ENVIRONMENT,
        "regions_scanned": [REGION, "us-west-2"],
        "regions_with_resources": [REGION],
        "errors": [],
        "summary": {
            "total_resources_discovered": len(resources),
            "total_findings": len(all_f),
            "findings_by_severity": by_severity,
            "findings_by_category": {k: len(v) for k, v in findings.items()},
            "excluded_resources": 61,
        },
        "discovered_resources": resources,
        "service_costs": service_costs,
        "region_costs": {REGION: round(sum(service_costs.values()), 2)},
        "resource_type_counts": type_counts,
        "region_type_counts": {REGION: type_counts},
        "drift_detection": {
            "terraform_managed": in_state,
            "unmanaged": len(resources) - in_state,
            "findings": findings["drift_detection"],
        },
        "tag_compliance": {"findings": findings["tag_compliance"]},
        "schedule_compliance": {"findings": findings["schedule_compliance"]},
        "lifecycle_compliance": {"findings": findings["lifecycle_compliance"]},
        "multi_state_detection": {"state_files": [], "lock_tables": [], "notes": []},
        "other_iac_analysis": [],
        "iac_registry": {
            "schema_version": "1.0",
            "entries": entries,
            "total_external_managed": sum(1 for r in e.resources if r["_repo"] not in (None, "platform-infra")),
        },
        "protected_resources": {"config": protected, "findings_annotated": 0},
        "termination_events": [],
    }


def previous_report(report, now):
    """The report one day earlier: two findings not yet present, one since fixed."""
    prev = json.loads(json.dumps(report))
    prev["generated_at"] = (datetime.fromisoformat(report["generated_at"]) - timedelta(days=1)).isoformat()
    new_names = {"checkout-service-dev-manual-replay", "nat-debugging"}
    for cat in ("drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"):
        prev[cat]["findings"] = [f for f in prev[cat]["findings"] if f["resource_name"] not in new_names]
    prev["discovered_resources"] = [r for r in prev["discovered_resources"] if r["name"] not in new_names]
    gone = {
        "arn": f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/i-0"
        + "".join(random.Random(11).choice("0123456789abcdef") for _ in range(16)),
        "resource_type": "ec2:instance",
        "name": "benchmark-scratch",
        "region": REGION,
        "state": "running",
        "tags": {"Name": "benchmark-scratch"},
        "estimated_monthly_cost": 124.10,
        "instance_type": "c5.xlarge",
        "instance_class": "",
    }
    prev["discovered_resources"].append(gone)
    prev["drift_detection"]["findings"].append(
        {
            "category": "drift_detection",
            "severity": "HIGH",
            "resource_arn": gone["arn"],
            "resource_type": "ec2:instance",
            "resource_name": "benchmark-scratch",
            "region": REGION,
            "state": "running",
            "issue": "Resource exists in AWS but not in Terraform state (possible click-ops)",
            "tags": gone["tags"],
            "estimated_monthly_cost": 124.10,
            "recommendation": "Investigate: running EC2 instance not in Terraform. Import with `terraform import` or terminate if unused.",
        }
    )
    return prev


TF_ATTR = {
    "aws_vpc": lambda r: {"cidr_block": "10.20.0.0/16"},
    "aws_lb": lambda r: {
        "dns_name": "acme-dev-alb-1234567890.us-east-1.elb.example",
        "internal": False,
        "load_balancer_type": "application",
    },
    "aws_instance": lambda r: {"instance_type": r["instance_type"], "private_ip": "10.20.10.14"},
    "aws_lambda_function": lambda r: {"function_name": r["name"], "runtime": "python3.12", "memory_size": 512},
    "aws_s3_bucket": lambda r: {"bucket": r["name"]},
    "aws_dynamodb_table": lambda r: {"name": r["name"], "billing_mode": "PAY_PER_REQUEST"},
}


def tfstate(e):
    vpc_id = None
    subnets = {}
    out = []
    for module, tf_type, tf_name, r in e.tf_platform:
        rid = r["arn"].split("/")[-1] if "/" in r["arn"] else r["arn"].split(":")[-1]
        attrs = {"arn": r["arn"], "id": rid, "tags": r["tags"], "tags_all": r["tags"], "name": r["name"]}
        attrs.update(TF_ATTR.get(tf_type, lambda _r: {})(r))
        if tf_type == "aws_vpc":
            vpc_id = rid
        if tf_type == "aws_subnet":
            public = "public" in tf_name
            az = tf_name[-1]
            idx = len(subnets)
            attrs.update(
                {
                    "vpc_id": vpc_id,
                    "cidr_block": f"10.20.{idx * 10}.0/24",
                    "availability_zone": f"{REGION}{az}",
                    "map_public_ip_on_launch": public,
                }
            )
            subnets[tf_name] = rid
        if tf_type in ("aws_security_group", "aws_nat_gateway", "aws_lb", "aws_instance"):
            attrs["vpc_id"] = vpc_id
        if tf_type == "aws_nat_gateway":
            attrs["subnet_id"] = subnets.get("public_a")
        if tf_type == "aws_instance":
            attrs["subnet_id"] = subnets.get("public_a")
        if tf_type == "aws_lb":
            attrs["subnets"] = [subnets.get("public_a"), subnets.get("public_b")]
        out.append(
            {
                "module": module,
                "mode": "managed",
                "type": tf_type,
                "name": tf_name,
                "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
                "instances": [{"schema_version": 0, "attributes": attrs}],
            }
        )
    return {
        "version": 4,
        "terraform_version": "1.9.8",
        "serial": 212,
        "lineage": "00000000-0000-4000-8000-000000000000",
        "outputs": {},
        "resources": out,
    }


def enforcement_state(report, now):
    """Records as the enforcement notifier writes them after a scan, one per warned resource."""
    by_arn = {}
    for cat in ("drift_detection", "tag_compliance", "lifecycle_compliance"):
        for f in report[cat]["findings"]:
            if f["severity"] == "HIGH":
                by_arn.setdefault(f["resource_arn"], []).append(f)
    warned_at = now.replace(hour=3, minute=1, second=12, microsecond=0)
    batch = warned_at.strftime("%Y%m%dT%H%M%S")
    records = []
    for arn, fs in by_arn.items():
        f = fs[0]
        tags = f.get("tags") or f.get("existing_tags") or {}
        contact = tags.get("Contact")
        rec = {
            "resource_arn": arn,
            "status": "warned",
            "batch_id": batch,
            "warned_at": warned_at.isoformat(),
            "grace_deadline": (warned_at + timedelta(hours=24)).isoformat(),
            "contact_email": contact or "cloud-admins@acme.example",
            "contact_source": "tag:Contact" if contact else "admin_fallback",
            "resource_type": f["resource_type"],
            "resource_name": f["resource_name"],
            "region": f["region"],
            "estimated_monthly_cost": str(
                f["estimated_monthly_cost"] if f["estimated_monthly_cost"] is not None else ""
            ),
            "issues": [x["issue"] for x in fs],
            "scan_date": warned_at.strftime("%Y-%m-%d"),
            "tags": tags,
            "approval_status": "pending",
            "approved_by": "",
            "approved_at": "",
        }
        if f["resource_name"] in ("vol-unattached-1", "eipalloc-unattached"):
            rec.update(
                approval_status="approved",
                approved_by="ops-lead@acme.example",
                approved_at=(warned_at + timedelta(hours=5)).isoformat(),
            )
        if "ExpiresAt" in tags:
            rec.update(approval_status="approved", approved_by="auto:expired", approved_at=warned_at.isoformat())
        records.append(rec)
    return records


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(here, "data"))
    ap.add_argument("--as-of", help="YYYY-MM-DD; default today (UTC)")
    args = ap.parse_args()
    now = datetime.strptime(args.as_of, "%Y-%m-%d").replace(tzinfo=UTC, hour=9) if args.as_of else datetime.now(UTC)

    e, findings = build(now)
    report = report_from(e, findings, now)
    os.makedirs(args.out, exist_ok=True)

    def dump(name, obj):
        with open(os.path.join(args.out, name), "w") as fh:
            json.dump(obj, fh, indent=1, sort_keys=False)
            fh.write("\n")

    dump("compliance-report.json", report)
    dump("previous-report.json", previous_report(report, now))
    dump("terraform.tfstate", tfstate(e))
    dump("enforcement-state.json", enforcement_state(report, now))
    s = report["summary"]
    print(
        f"{s['total_resources_discovered']} resources, {s['total_findings']} findings {s['findings_by_severity']} -> {args.out}"
    )


if __name__ == "__main__":
    main()

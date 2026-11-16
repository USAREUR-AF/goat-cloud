"""Tests for scripts/compliance-lint: values, patterns and rules of the contract."""

import importlib.machinery
import importlib.util
import os

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load():
    loader = importlib.machinery.SourceFileLoader(
        "compliance_lint_under_test", os.path.join(_ROOT, "scripts", "compliance-lint")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


lint = _load()
CONTRACT = lint._DEFAULT_CONTRACT

TAGS = {
    "Project": "example",
    "Environment": "dev",
    "ManagedBy": "serverless",
    "Lifecycle": "persistent",
    "Repository": "https://git.example/example-org/example-repo",
}

# The contract as published before version 1.0.
OLD_CONTRACT = {
    "version": "1.0",
    "required_tags": ["Project", "Environment", "ManagedBy", "Lifecycle", "Repository"],
    "valid_managed_by": ["terraform", "serverless"],
    "valid_lifecycle": ["persistent", "temporary"],
    "tag_validation": {
        "Repository": {"pattern": "^(https://|git@)"},
        "ManagedBy": {"enum": ["terraform", "serverless"]},
    },
}


def check(**changes):
    tags = {k: v for k, v in {**TAGS, **changes}.items() if v is not None}
    return lint.check_values(tags, CONTRACT, "tags")


def test_valid_tags():
    assert check() == []


def test_required_keys_of_both_contract_shapes():
    assert lint.required_keys(CONTRACT) == ["Project", "Environment", "ManagedBy", "Lifecycle", "Repository"]
    without_aliases = {k: v for k, v in CONTRACT.items() if k != "required_tags"}
    assert lint.required_keys(without_aliases) == ["Project", "Environment", "ManagedBy", "Lifecycle", "Repository"]
    assert lint.required_keys(OLD_CONTRACT) == OLD_CONTRACT["required_tags"]


def test_invalid_managed_by():
    assert check(ManagedBy="clickops") == [
        f"tags: invalid ManagedBy value 'clickops'. Valid: {', '.join(CONTRACT['allowed']['managed_by'])}"
    ]


@pytest.mark.parametrize("value", ["opentofu", "infrastructure-manager"])
def test_managed_by_values_added_in_1_0(value):
    assert check(ManagedBy=value) == []


def test_invalid_lifecycle():
    assert len(check(Lifecycle="forever")) == 1


@pytest.mark.parametrize("lifecycle", ["temporary", "experiment"])
def test_lifecycle_that_requires_an_expiry(lifecycle):
    assert check(Lifecycle=lifecycle) == [f"tags: Lifecycle={lifecycle} requires ExpiresAt"]
    assert check(Lifecycle=lifecycle, ExpiresAt="2030-01-31T00:00:00Z") == []


@pytest.mark.parametrize("value", ["2030-01-31", "2030-01-31T00:00:00", "20300131t0000z"])
def test_expiry_outside_the_pattern(value):
    findings = check(Lifecycle="temporary", ExpiresAt=value)
    assert len(findings) == 1 and "ExpiresAt" in findings[0] and "does not match" in findings[0]


def test_repository_outside_the_pattern():
    assert len(check(Repository="example-repo")) == 1


def test_protected_value():
    assert check(Protected="true") == []
    assert len(check(Protected="yes")) == 1


def test_environment_list_of_a_deployment():
    contract = {**CONTRACT, "allowed": {**CONTRACT["allowed"], "environment": ["dev", "live"]}}
    assert lint.check_values({**TAGS, "Environment": "sandbox"}, contract, "tags") == [
        "tags: invalid Environment value 'sandbox'. Valid: dev, live"
    ]
    assert lint.check_values(TAGS, contract, "tags") == []


def test_value_that_is_not_a_literal_is_not_checked():
    assert check(ManagedBy={"Ref": "Tool"}, ExpiresAt={"Fn::Sub": "${Expiry}"}, Lifecycle="temporary") == []


def test_contract_published_before_1_0_is_still_read():
    assert lint.check_values(TAGS, OLD_CONTRACT, "tags") == []
    findings = lint.check_values({**TAGS, "ManagedBy": "clickops", "Repository": "x"}, OLD_CONTRACT, "tags")
    assert len(findings) == 2
    assert "keys" not in OLD_CONTRACT


def test_serverless_file(tmp_path):
    path = tmp_path / "serverless.yml"
    tags = "\n".join(f"    {k}: {v}" for k, v in {**TAGS, "Lifecycle": "experiment"}.items())
    path.write_text(f"provider:\n  stackTags:\n{tags}\n  tags:\n{tags}\n", encoding="utf-8")
    assert lint.lint_serverless(str(path), CONTRACT) == [
        "provider.stackTags: Lifecycle=experiment requires ExpiresAt",
        "provider.tags: Lifecycle=experiment requires ExpiresAt",
    ]


def test_cloudformation_file(tmp_path):
    path = tmp_path / "template.yaml"
    tags = "\n".join(f"        - Key: {k}\n          Value: {v}" for k, v in {**TAGS, "ManagedBy": "clickops"}.items())
    path.write_text(
        f"Resources:\n  Bucket:\n    Type: AWS::S3::Bucket\n    Properties:\n      Tags:\n{tags}\n", encoding="utf-8"
    )
    findings = lint.lint_cloudformation(str(path), CONTRACT)
    assert len(findings) == 1 and findings[0].startswith("Resource Bucket: invalid ManagedBy value 'clickops'")

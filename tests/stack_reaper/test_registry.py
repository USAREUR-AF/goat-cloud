"""The stack reaper reads the IaC registry through the shared loader."""

import json
from unittest.mock import MagicMock

import iac_registry
import pytest
import reaper_index
import reconciler_index


class _FakeSSM:
    """get_parameters_by_path over a dict of {name: value}."""

    def __init__(self, params):
        self._params = params

    def get_paginator(self, name):
        assert name == "get_parameters_by_path"
        return self

    def paginate(self, Path, Recursive, WithDecryption):
        prefix = Path if Path.endswith("/") else Path + "/"
        yield {"Parameters": [{"Name": n, "Value": v} for n, v in self._params.items() if n.startswith(prefix)]}


def _entry(repo):
    return json.dumps({"repo_url": f"https://github.com/acme/{repo}", "iac_type": "terraform"})


PARAMS = {
    "/acme/prod/iac-registry/platform-infra": _entry("platform-infra"),
    "/acme/prod/iac-registry/example-repo": _entry("example-repo"),
    "/acme/dev/iac-registry/example-repo-dev": _entry("example-repo-dev"),
    "/acme/dev/iac-registry/broken": "{not json",
    "/acme/prod/protected-resources": "[]",
}


@pytest.fixture
def ssm(monkeypatch):
    monkeypatch.setattr(iac_registry.boto3, "client", lambda service, **kwargs: _FakeSSM(PARAMS))


def _slugs(entries):
    return sorted(e["repo_slug"] for e in entries)


class TestSharedLoader:
    def test_both_lambdas_use_the_shared_loader(self):
        assert reconciler_index.load_iac_registry is iac_registry.load_iac_registry
        assert reaper_index.load_iac_registry is iac_registry.load_iac_registry

    def test_single_prefix(self, ssm, monkeypatch):
        monkeypatch.setenv("IAC_REGISTRY_PREFIX", "/acme/prod/iac-registry")
        assert _slugs(reconciler_index.load_iac_registry("acme", "prod")) == ["example-repo", "platform-infra"]

    def test_prefix_from_project_and_environment(self, ssm, monkeypatch):
        monkeypatch.delenv("IAC_REGISTRY_PREFIX", raising=False)
        assert _slugs(reconciler_index.load_iac_registry("acme", "dev")) == ["example-repo-dev"]

    def test_wildcard_reads_every_registered_environment(self, ssm, monkeypatch):
        monkeypatch.setenv("IAC_REGISTRY_PREFIX", "/acme/*/iac-registry")
        assert _slugs(reaper_index.load_iac_registry("acme", "prod")) == [
            "example-repo",
            "example-repo-dev",
            "platform-infra",
        ]

    def test_comma_list_and_duplicates(self, ssm, monkeypatch):
        monkeypatch.setenv("IAC_REGISTRY_PREFIX", "/acme/prod/iac-registry, /acme/*/iac-registry")
        entries = reconciler_index.load_iac_registry()
        assert len(entries) == 3

    def test_no_prefix_returns_nothing(self, ssm, monkeypatch):
        monkeypatch.delenv("IAC_REGISTRY_PREFIX", raising=False)
        assert reconciler_index.load_iac_registry() == []


class TestReconcilerUsesTheRegistry:
    def test_platform_repository_is_never_scheduled(self, monkeypatch):
        registry = [
            {"repo_slug": reconciler_index.PLATFORM_REPO_SLUG, "repo_url": "https://github.com/acme/platform-infra"},
            {
                "repo_slug": "example-repo",
                "repo_url": "https://github.com/acme/example-repo",
                "iac_type": "terraform",
                "lifecycle": "temporary",
                "expires_at": "2020-01-01T00:00:00Z",
            },
        ]
        queried = []
        monkeypatch.setattr(reconciler_index, "load_iac_registry", lambda project, environment: registry)
        monkeypatch.setattr(reconciler_index, "boto3", MagicMock())
        monkeypatch.setattr(reconciler_index, "resources_of_repository", lambda tagging, url: queried.append(url) or [])
        monkeypatch.setattr(reconciler_index, "_update_state", lambda *args, **kwargs: None)
        monkeypatch.setattr(reconciler_index, "_delete_schedule_if_exists", lambda *args, **kwargs: None)
        for name in ("_cleanup_orphan_schedules", "_cleanup_orphans", "_write_job_status"):
            if hasattr(reconciler_index, name):
                monkeypatch.setattr(reconciler_index, name, lambda *args, **kwargs: 0)

        stats = reconciler_index.handler({}, None)

        assert queried == ["https://github.com/acme/example-repo"]
        assert stats["skipped"] == 2
        assert stats["scheduled"] == 0

    def test_empty_registry_does_nothing(self, monkeypatch):
        monkeypatch.setattr(reconciler_index, "load_iac_registry", lambda project, environment: [])
        boto = MagicMock()
        monkeypatch.setattr(reconciler_index, "boto3", boto)
        assert reconciler_index.handler({}, None)["scheduled"] == 0
        boto.client.assert_not_called()


class TestEntryThatIsNotAnObject:
    @pytest.mark.parametrize("value", ["[]", '"x"', "7", "null"])
    def test_is_skipped_and_the_rest_of_the_prefix_still_loads(self, monkeypatch, value):
        params = {
            "/acme/prod/iac-registry/a-broken": value,
            "/acme/prod/iac-registry/b-good": _entry("b-good"),
        }
        monkeypatch.setattr(iac_registry.boto3, "client", lambda service, **kwargs: _FakeSSM(params))
        monkeypatch.setenv("IAC_REGISTRY_PREFIX", "/acme/prod/iac-registry")
        assert _slugs(iac_registry.load_iac_registry()) == ["b-good"]

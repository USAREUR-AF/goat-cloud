"""IaC registry prefix discovery.

Coverage must be DERIVED from what has registered, never hand-enumerated:
an environment missing from a hand-typed list has its Terraform-managed
resources reported as suspected click-ops.
"""

from iac_registry import discover_registry_prefixes, normalize_entry


class _FakePaginator:
    def __init__(self, params):
        self._params = params

    def paginate(self, Path, Recursive, WithDecryption):
        assert Path == "/acme"
        assert Recursive is True
        # Two pages, to exercise pagination.
        mid = len(self._params) // 2
        yield {"Parameters": [{"Name": n} for n in self._params[:mid]]}
        yield {"Parameters": [{"Name": n} for n in self._params[mid:]]}


class _FakeSSM:
    def __init__(self, params):
        self._params = params

    def get_paginator(self, name):
        assert name == "get_parameters_by_path"
        return _FakePaginator(self._params)


class _ExplodingSSM:
    def get_paginator(self, name):
        raise RuntimeError("ssm unavailable")


ALL_PARAMS = [
    "/acme/prod/iac-registry/example-repo",
    "/acme/prod/iac-registry/platform-infra",
    "/acme/qa/iac-registry/example-repo-qa",
    "/acme/dev/iac-registry/example-repo-dev",
    "/acme/staging/iac-registry/example-repo-staging",
    "/acme/sandbox/iac-registry/example-repo-sandbox",
    # Non-registry parameters under the same project prefix must be ignored.
    "/acme/prod/compliance-contract",
    "/acme/prod/protected-resources",
    "/acme/prod/job-status/nightly",
]


def test_discovers_every_registered_environment():
    prefixes = discover_registry_prefixes(_FakeSSM(ALL_PARAMS), "/acme/*/iac-registry")
    assert prefixes == [
        "/acme/dev/iac-registry",
        "/acme/prod/iac-registry",
        "/acme/qa/iac-registry",
        "/acme/sandbox/iac-registry",
        "/acme/staging/iac-registry",
    ]


def test_ignores_non_registry_parameters():
    only_other = [p for p in ALL_PARAMS if "/iac-registry/" not in p]
    assert discover_registry_prefixes(_FakeSSM(only_other), "/acme/*/iac-registry") == []


def test_registry_path_without_a_slug_yields_no_prefix():
    # A bare /<project>/<env>/iac-registry parameter names no repo; it must not
    # become a prefix, or the loader would read a path with nothing under it.
    bare = ["/acme/prod/iac-registry"]
    assert discover_registry_prefixes(_FakeSSM(bare), "/acme/*/iac-registry") == []


def test_ssm_failure_is_a_graceful_no_op():
    assert discover_registry_prefixes(_ExplodingSSM(), "/acme/*/iac-registry") == []


def test_pattern_without_a_tail_yields_no_prefix():
    assert discover_registry_prefixes(_FakeSSM(ALL_PARAMS), "/acme/*") == []


def test_entry_owner_and_contact_are_optional():
    entry = normalize_entry({"repo_url": "https://git.example/example-org/example-repo", "iac_type": "terraform"})
    assert entry["owner"] is None and entry["contact"] is None


def test_entry_owner_and_contact_are_read():
    entry = normalize_entry({"repo_url": "x", "owner": " platform-team ", "contact": "platform-team@example.invalid"})
    assert entry["owner"] == "platform-team"
    assert entry["contact"] == "platform-team@example.invalid"


def test_entry_empty_or_non_text_owner_and_contact_read_as_none():
    entry = normalize_entry({"repo_url": "x", "owner": "", "contact": ["a@example.invalid"]})
    assert entry["owner"] is None and entry["contact"] is None


def test_entry_lifecycle_and_expires_at_are_optional():
    entry = normalize_entry({"repo_url": "x", "iac_type": "terraform"})
    assert entry["lifecycle"] is None and entry["expires_at"] is None


def test_entry_lifecycle_and_expires_at_are_read():
    entry = normalize_entry({"repo_url": "x", "lifecycle": " temporary ", "expires_at": "2030-12-01T00:00:00Z"})
    assert entry["lifecycle"] == "temporary"
    assert entry["expires_at"] == "2030-12-01T00:00:00Z"


def test_entry_non_text_lifecycle_reads_as_none():
    entry = normalize_entry({"repo_url": "x", "lifecycle": 3, "expires_at": ""})
    assert entry["lifecycle"] is None and entry["expires_at"] is None

"""Workspace ownership follows IaC-registry registration, not bucket.

A repository can keep its state in another repository's bucket. Deciding
ownership by bucket alone would attribute its resources to the bucket's owner;
the registry entry names the repository that owns them.
"""

from unittest.mock import patch

from scanners.multi_state_detector import analyze_discovered_states

OURS = "https://github.com/acme/platform-infra"
SISTER = "https://github.com/acme/example-services"
BUCKET = "platform-infra-tf-state"

STATE_FILES = [
    {"bucket": BUCKET, "key": "aws/prod/terraform.tfstate", "last_modified": "2026-09-02"},
    {"bucket": BUCKET, "key": "aws/example-services/terraform.tfstate", "last_modified": "2026-09-02"},
    {"bucket": "acme-example-repo-tf-state", "key": "dev/terraform.tfstate", "last_modified": "2026-08-09"},
]

REGISTRY = {
    f"{BUCKET}/aws/prod/terraform.tfstate": OURS,
    f"{BUCKET}/aws/example-services/terraform.tfstate": SISTER,
    "acme-example-repo-tf-state/dev/terraform.tfstate": "https://github.com/acme/example-repo",
}


def _analyze(**kwargs):
    with patch("utils.state_parser.load_terraform_state_arns", return_value=set()):
        return analyze_discovered_states(STATE_FILES, [], **kwargs)


def _by_key(analyses, fragment):
    return next(a for a in analyses if fragment in a["state_path"])


def test_sister_repo_in_our_bucket_is_not_ours():
    out = _analyze(known_state_bucket=BUCKET, registry_state_paths=REGISTRY, own_repo_url=OURS)
    services = _by_key(out, "aws/example-services/")
    assert services["is_ours"] is False
    assert services["recommendation"] == f"Registered IaC. Managed by {SISTER}."


def test_our_own_registered_workspace_is_still_ours():
    out = _analyze(known_state_bucket=BUCKET, registry_state_paths=REGISTRY, own_repo_url=OURS)
    assert _by_key(out, "aws/prod/")["is_ours"] is True


def test_other_bucket_is_never_ours():
    out = _analyze(known_state_bucket=BUCKET, registry_state_paths=REGISTRY, own_repo_url=OURS)
    assert _by_key(out, "dev/")["is_ours"] is False


def test_unregistered_state_in_our_bucket_still_falls_back_to_bucket():
    # A state file nobody has registered keeps bucket as its only ownership
    # signal, so it reads as ours rather than becoming a drift-finding storm.
    out = _analyze(known_state_bucket=BUCKET, registry_state_paths={}, own_repo_url=OURS)
    assert _by_key(out, "aws/example-services/")["is_ours"] is True
    assert _by_key(out, "aws/prod/")["is_ours"] is True


def test_no_registry_and_no_repo_url_behaves_as_before():
    out = _analyze(known_state_bucket=BUCKET)
    assert _by_key(out, "aws/prod/")["is_ours"] is True
    assert _by_key(out, "dev/")["is_ours"] is False

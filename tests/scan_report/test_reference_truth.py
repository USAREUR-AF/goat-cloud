"""The reference truth files against the repository's own contract, offline and with no mapping.

Each record below carries what the matching case of ``reference/<cloud>/cases``
writes on its resource. Grading them with ``contract/contract.yaml`` and
checking the result against ``reference/<cloud>/truth.yaml`` proves that the
contract, the cases and the truth file agree, with no cloud access.

Neither test is ever skipped: a missing truth file, or a contract that lacks
what a case needs, fails the test.
"""

from pathlib import Path

from goatcloud import contract, grade, report, verify

REPO = Path(__file__).resolve().parents[2]
SUB = "00000000-0000-0000-0000-000000000000"

AZURE = {
    "Project": "acme",
    "Environment": "dev",
    "ManagedBy": "terraform",
    "Lifecycle": "persistent",
    "Repository": "https://git.example/acme/example-repo",
}
AZURE_CASES = {
    "case-1-conforming": AZURE,
    "case-2-missing-environment": {k: v for k, v in AZURE.items() if k != "Environment"},
    "case-3-expired": {**AZURE, "Lifecycle": "temporary", "ExpiresAt": "2020-01-01T00:00:00Z"},
    "case-4-expiry-unreadable": {**AZURE, "Lifecycle": "temporary", "ExpiresAt": "next-week"},
    "case-5-invalid-managed-by": {**AZURE, "ManagedBy": "chef"},
    "case-6-protected": {**AZURE, "Protected": "true"},
    "case-7-untaggable-member": AZURE,
    "case-8-temporary-no-expiry": {**AZURE, "Lifecycle": "temporary"},
    "case-9-experiment-no-expiry": {**AZURE, "Lifecycle": "experiment"},
}

GCP = {
    "workload": "acme",
    "owner": "platform",
    "environment": "dev",
    "lifecycle": "persistent",
    "managed-by": "terraform",
}
GCP_CASES = {
    "case-1-conforming": GCP,
    "case-2-missing-environment": {k: v for k, v in GCP.items() if k != "environment"},
    "case-3-expired": {**GCP, "lifecycle": "temporary", "expires-at": "20200101t0000z"},
    "case-4-expiry-unreadable": {**GCP, "lifecycle": "temporary", "expires-at": "next-week"},
    "case-5-invalid-managed-by": {**GCP, "managed-by": "chef"},
    "case-6-protected": {**GCP, "protected": "true"},
}


def azure_inventory():
    return {
        "platform": "azure",
        "scopes": [
            {"scope": SUB, "scope_type": "subscription", "name": "example-subscription", "state": None, "tags": {}}
        ],
        "resources": [
            {
                "resource_id": f"/subscriptions/{SUB}/resourcegroups/ref-{name}",
                "platform": "azure",
                "scope": SUB,
                "location": "westeurope",
                "resource_type": "microsoft.resources/subscriptions/resourcegroups",
                "name": f"ref-{name}",
                "container": f"ref-{name}",
                "tags": tags,
                "created_at": None,
            }
            for name, tags in AZURE_CASES.items()
        ],
    }


def gcp_inventory():
    def record(resource_id, resource_type, labels, how):
        return {
            "resource_id": resource_id,
            "platform": "gcp",
            "scope": "example-project",
            "location": "global",
            "resource_type": resource_type,
            "name": resource_id.rsplit("/", 1)[-1],
            "tags": labels,
            "created_at": None,
            "grade": how,
        }

    topics = "//pubsub.googleapis.com/projects/example-project/topics"
    account = "//iam.googleapis.com/projects/example-project/serviceAccounts"
    return {
        "platform": "gcp",
        "scopes": [
            {"scope": "example-project", "scope_type": "project", "name": "example-project", "state": None, "tags": {}}
        ],
        "resources": [
            *[
                # the Google provider adds this label to everything it creates
                record(
                    f"{topics}/ref-{name}",
                    "pubsub.googleapis.com/Topic",
                    {**labels, "goog-terraform-provisioned": "true"},
                    "labels+ownership",
                )
                for name, labels in GCP_CASES.items()
            ],
            record(
                f"{account}/ref-case-7-unlabelable@example-project.iam.gserviceaccount.com",  # oss-allow: invented project
                "iam.googleapis.com/ServiceAccount",
                {},
                "ownership",
            ),
        ],
    }


def check(cloud, inventory):
    truth_path = REPO / "reference" / cloud / "truth.yaml"
    assert truth_path.exists(), f"reference/{cloud}/truth.yaml is missing"
    resolved = contract.load(cloud)
    truth = verify.load_truth(str(truth_path))
    graded = grade.GRADERS[truth.get("grade", "units")](inventory, resolved)
    return verify.verify(truth, report.build(inventory, resolved, graded))


def test_azure_reference_cases_with_no_mapping():
    assert "experiment" in {rule.equals for rule in contract.load("azure").rules}
    outcome = check("azure", azure_inventory())
    assert outcome["mismatches"] == []
    assert (outcome["counts"]["FP"], outcome["counts"]["FN"]) == (0, 0)
    assert outcome["uncovered_units"] == []


def test_gcp_reference_cases_with_no_mapping():
    resolved = contract.load("gcp")
    assert resolved.key("project") == "workload"
    assert "owner" in resolved.required
    outcome = check("gcp", gcp_inventory())
    assert outcome["mismatches"] == []
    assert (outcome["counts"]["FP"], outcome["counts"]["FN"]) == (0, 0)
    assert outcome["uncovered_units"] == []

"""Writes the synthetic inventories under fixtures/. Run after editing; tests read the JSON files.

Each inventory is a truth set: one unit conforms, and every other unit breaks
exactly one rule. The expected results are in fixtures/<platform>_truth.yaml.
Timestamps assume the scan runs at 2030-06-15T00:00:00Z.
"""

import json
from pathlib import Path

SUB = "00000000-0000-0000-0000-000000000000"
REPO = "https://git.example/acme/example-repo"
FULL = {
    "Project": "acme",
    "Environment": "dev",
    "ManagedBy": "terraform",
    "Lifecycle": "persistent",
    "Repository": REPO,
}
RG_TYPE = "microsoft.resources/subscriptions/resourcegroups"


def azure(name, rtype, group, tags, inherited=None, location="westeurope"):
    return {
        "resource_id": f"/subscriptions/{SUB}/resourcegroups/{group}/providers/{rtype}/{name.lower()}",
        "platform": "azure",
        "scope": SUB,
        "location": location,
        "resource_type": rtype,
        "name": name,
        "kind": None,
        "container": group,
        "tags": tags,
        "inherited_tags": inherited or {},
        "state": None,
        "provisioning_state": "Succeeded",
        "created_at": None,
        "estimated_monthly_cost": None,
    }


def azure_group(name, tags):
    record = azure(name, RG_TYPE, name, tags)
    record["resource_id"] = f"/subscriptions/{SUB}/resourcegroups/{name}"
    return record


def without(tags, key):
    return {k: v for k, v in tags.items() if k != key}


AZURE = {
    "schema_version": "2.0",
    "platform": "azure",
    "generated_at": "2030-06-15T00:00:00+00:00",
    "scopes": [
        {"scope": SUB, "scope_type": "subscription", "name": "example-subscription", "state": "Enabled", "tags": {}}
    ],
    "resources": [
        azure_group("acme-conforming", FULL),
        azure_group("acme-missing-environment", without(FULL, "Environment")),
        azure_group("acme-missing-all", {}),
        azure_group("acme-expired", {**FULL, "Lifecycle": "temporary", "ExpiresAt": "2030-06-01T00:00:00Z"}),
        azure_group("acme-expiry-unreadable", {**FULL, "Lifecycle": "temporary", "ExpiresAt": "next-week"}),
        azure_group("acme-invalid-value", {**FULL, "ManagedBy": "chef"}),
        azure_group("acme-protected", {**FULL, "Protected": "true"}),
        azure_group("acme-temporary", {**FULL, "Lifecycle": "temporary"}),
        azure_group("acme-expiring", {**FULL, "Lifecycle": "temporary", "ExpiresAt": "2030-06-20T00:00:00Z"}),
        azure_group("acme-mixed", {k.lower(): v for k, v in FULL.items()}),
        # resources of acme-mixed: what they carry themselves is information
        azure("acme-vnet", "microsoft.network/virtualnetworks", "acme-mixed", {}, FULL),
        azure(
            "acme-vm",
            "microsoft.compute/virtualmachines",
            "acme-mixed",
            {"project": "acme", "ManagedBy": "chef"},
            FULL,
        ),
        azure("acme-assignment", "microsoft.authorization/roleassignments", "acme-mixed", {}, FULL, "global"),
        azure("acme-widget", "example.provider/widgets", "acme-mixed", {}, FULL),
        # a resource of a group that conforms nowhere
        azure("acme-disk", "microsoft.compute/disks", "acme-missing-all", {}),
    ],
}


def gcp(project, name, rtype, labels, grade="labels+ownership", created_at=None, location="europe-west3"):
    service = rtype.split(".")[0]
    return {
        "resource_id": f"//{service}.googleapis.com/projects/{project}/{name}",
        "platform": "gcp",
        "scope": project,
        "location": location,
        "resource_type": rtype,
        "name": name.rsplit("/", 1)[-1],
        "tags": labels,
        "state": None,
        "created_at": created_at,
        "grade": grade,
        "estimated_monthly_cost": None,
    }


def gcp_project(project, labels, created_at="2029-01-01T00:00:00Z"):
    record = gcp(project, project, "cloudresourcemanager.googleapis.com/Project", labels, created_at=created_at)
    record["resource_id"] = f"//cloudresourcemanager.googleapis.com/projects/{project}"
    record["location"] = "global"
    return record


GCP_FULL = {
    "workload": "acme",
    "environment": "dev",
    "managed-by": "terraform",
    "lifecycle": "persistent",
    "owner": "platform",
}
GCP_PROJECTS = {
    "example-conforming": GCP_FULL,
    "example-missing-environment": without(GCP_FULL, "environment"),
    "example-expired": {**GCP_FULL, "lifecycle": "temporary", "expires-at": "20300601t0000z"},
    "example-expiry-unreadable": {**GCP_FULL, "lifecycle": "temporary", "expires-at": "next-week"},
    "example-invalid-value": {**GCP_FULL, "managed-by": "chef"},
    "example-protected": {**GCP_FULL, "protected": "true"},
    "example-mixed": GCP_FULL,
    # the key names of another deployment
    "example-other-keys": {"app_name": "acme", "env": "dev", "owner": "platform", "cost_center": "cc-0000"},
    # created five minutes before the scan
    "example-new": without(GCP_FULL, "environment"),
}

GCP = {
    "schema_version": "2.0",
    "platform": "gcp",
    "generated_at": "2030-06-15T00:00:00+00:00",
    "scopes": [
        {"scope": p, "scope_type": "project", "name": p, "state": "ACTIVE", "tags": labels}
        for p, labels in GCP_PROJECTS.items()
    ],
    "resources": [
        *[
            gcp_project(p, labels, "2030-06-14T23:55:00Z" if p == "example-new" else "2029-01-01T00:00:00Z")
            for p, labels in GCP_PROJECTS.items()
        ],
        gcp("example-mixed", "zones/europe-west3-a/instances/acme-vm", "compute.googleapis.com/Instance", GCP_FULL),
        gcp("example-mixed", "buckets/acme-bucket", "storage.googleapis.com/Bucket", {}),
        gcp("example-mixed", "services/compute.googleapis.com", "serviceusage.googleapis.com/Service", {}, "none"),
        gcp("example-mixed", "serviceAccounts/acme", "iam.googleapis.com/ServiceAccount", {}, "ownership"),
        # a type the adapter has no class for
        gcp("example-mixed", "widgets/acme-widget", "example.googleapis.com/Widget", {}),
    ],
    "unclassified_types": {"example.googleapis.com/Widget": 1},
}

if __name__ == "__main__":
    here = Path(__file__).parent / "fixtures"
    for name, document in (("azure_inventory.json", AZURE), ("gcp_inventory.json", GCP)):
        (here / name).write_text(json.dumps(document, indent=2) + "\n")

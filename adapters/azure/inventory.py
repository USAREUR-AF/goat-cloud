"""Read-only Azure inventory through Azure Resource Graph.

Lists every resource in the given subscriptions, or under the given management
groups, as report schema 2.0 records. Two tables are read: ``resources`` for
the resources and ``resourcecontainers`` for subscriptions and resource groups.
The only Azure operation used is the Resource Graph query
(``POST /providers/Microsoft.ResourceGraph/resources``), which needs read
access to the resources it returns and changes nothing.

Run it:

    python -m adapters.azure.inventory --subscription <id> --out inventory.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from adapters.http import DEFAULT_MAX_PAGES, bearer_request, safe_opener
from adapters.schema import SCHEMA_VERSION, name_from_id

PLATFORM = "azure"

# Resource Graph returns at most 1000 rows per request.
PAGE_SIZE = 1000

RESOURCE_GROUP_TYPE = "microsoft.resources/subscriptions/resourcegroups"
SUBSCRIPTION_TYPE = "microsoft.resources/subscriptions"

# Every query projects `id` and orders by it: paging needs a stable order and
# at least one output column that is not dynamic, or no $skipToken is returned.
RESOURCES_QUERY = """
resources
| project id, name, type, kind, location, subscriptionId, resourceGroup, tags,
    powerState = tostring(properties.extended.instanceView.powerState.code),
    diskState = tostring(properties.diskState),
    provisioningState = tostring(properties.provisioningState),
    timeCreated = tostring(properties.timeCreated),
    creationTime = tostring(properties.creationTime)
| order by id asc
""".strip()

RESOURCE_GROUPS_QUERY = f"""
resourcecontainers
| where type =~ '{RESOURCE_GROUP_TYPE}'
| project id, name, type, location, subscriptionId, tags,
    provisioningState = tostring(properties.provisioningState)
| order by id asc
""".strip()

SUBSCRIPTIONS_QUERY = f"""
resourcecontainers
| where type =~ '{SUBSCRIPTION_TYPE}'
| project id, name, subscriptionId, tags, subscriptionState = tostring(properties.state)
| order by id asc
""".strip()

# A page function takes a Resource Graph request body and returns the response
# body, both in the REST shape ("$skipToken", "resultTruncated", "data").
PageFn = Callable[[dict[str, Any]], dict[str, Any]]


class InventoryError(RuntimeError):
    """The inventory could not be read completely."""


def sdk_page_fn(credential: Any = None) -> PageFn:
    """Return a page function backed by azure-mgmt-resourcegraph.

    The SDKs are imported here so that the rest of the module, and its tests,
    need no Azure package.
    """
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.resourcegraph import ResourceGraphClient
    from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions

    client = ResourceGraphClient(credential or DefaultAzureCredential())

    def page(body: dict[str, Any]) -> dict[str, Any]:
        options = body.get("options", {})
        response = client.resources(
            QueryRequest(
                query=body["query"],
                subscriptions=body.get("subscriptions"),
                management_groups=body.get("managementGroups"),
                options=QueryRequestOptions(
                    result_format="objectArray",
                    top=options.get("$top"),
                    skip_token=options.get("$skipToken"),
                ),
            )
        )
        return {
            "totalRecords": response.total_records,
            "count": response.count,
            "resultTruncated": response.result_truncated,
            "$skipToken": response.skip_token,
            "data": response.data,
        }

    return page


# Resource Graph holds no creation time for a resource group. Resource Manager
# returns it on the resource group list when asked with $expand=createdTime.
ARM_ENDPOINT = "https://management.azure.com"
ARM_API_VERSION = "2021-04-01"
ARM_TOKEN_SCOPE = "https://management.azure.com/.default"

# An ARM get function takes a URL and returns the JSON response body.
ArmGetFn = Callable[[str], dict[str, Any]]


def sdk_arm_get_fn(credential: Any = None, *, opener: Any = None) -> ArmGetFn:
    """Return an ARM get function that signs each request with a token from ``credential``.

    Standard library HTTP; azure-identity supplies the token, from the default
    credential chain when no credential is given. The token is held in memory
    only, is sent only to Resource Manager, and is never forwarded on a
    redirect: the default opener refuses redirects, and the token travels in an
    unredirected header for any opener.
    """
    opener = opener or safe_opener()
    holder = {"credential": credential}

    def get(url: str) -> dict[str, Any]:
        if not url.startswith(ARM_ENDPOINT + "/"):
            raise InventoryError("Refusing to send the Resource Manager token to another host.")
        if holder["credential"] is None:
            from azure.identity import DefaultAzureCredential

            holder["credential"] = DefaultAzureCredential()
        token = holder["credential"].get_token(ARM_TOKEN_SCOPE).token
        with opener(bearer_request(url, token), timeout=60) as response:
            return json.load(response)

    return get


def resource_group_list_url(subscription: str) -> str:
    """The Resource Manager URL that lists a subscription's resource groups with their creation time."""
    return (
        f"{ARM_ENDPOINT}/subscriptions/{subscription}/resourcegroups?api-version={ARM_API_VERSION}&$expand=createdTime"
    )


def resource_group_created_times(
    arm_get: ArmGetFn,
    subscription: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    incomplete: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Map resource group name, lowercased, to its ``createdTime``; follows ``nextLink`` to the end.

    A group whose row carries no ``createdTime`` is left out. After
    ``max_pages`` pages the list stops: the reason is appended to
    ``incomplete``, or ``InventoryError`` is raised without one.
    """
    created: dict[str, str] = {}
    url: str | None = resource_group_list_url(subscription)
    seen: set[str] = set()
    while url:
        if url in seen:
            raise InventoryError("Resource Manager returned the same nextLink twice.")
        if len(seen) >= max_pages:
            reason = f"Resource Manager resource group list stopped at the page cap of {max_pages} pages."
            if incomplete is None:
                raise InventoryError(reason)
            incomplete.append({"scope": subscription, "reason": reason})
            break
        seen.add(url)
        body = arm_get(url)
        for group in body.get("value") or []:
            if group.get("name") and group.get("createdTime"):
                created[group["name"].lower()] = group["createdTime"]
        url = body.get("nextLink") or None
    return created


def _scope_body(subscriptions: Iterable[str] | None, management_groups: Iterable[str] | None) -> dict[str, Any]:
    subs = [s.lower() for s in subscriptions or []]
    groups = list(management_groups or [])
    if bool(subs) == bool(groups):
        raise ValueError("Give subscriptions or management groups, one of the two.")
    return {"subscriptions": subs} if subs else {"managementGroups": groups}


def query_rows(
    page: PageFn,
    query: str,
    *,
    subscriptions: Iterable[str] | None = None,
    management_groups: Iterable[str] | None = None,
    page_size: int = PAGE_SIZE,
    incomplete: list[dict[str, Any]] | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> Iterator[dict[str, Any]]:
    """Yield every row of a query, following ``$skipToken`` until it is absent.

    When Resource Graph signals that rows are left out, or ``max_pages`` pages
    have been read and more remain, the reason is appended to ``incomplete``
    and the rows read so far stand. Without an ``incomplete`` list the same
    condition raises ``InventoryError``.
    """
    scope = _scope_body(subscriptions, management_groups)

    def left_out(reason: str) -> None:
        if incomplete is None:
            raise InventoryError(reason)
        incomplete.append({"scope": None, "reason": reason})

    skip_token: str | None = None
    seen = 0
    pages = 0
    while True:
        if pages >= max_pages:
            left_out(f"Resource Graph query stopped at the page cap of {max_pages} pages; the inventory is incomplete.")
            return
        pages += 1
        options: dict[str, Any] = {"resultFormat": "objectArray", "$top": page_size}
        if skip_token:
            options["$skipToken"] = skip_token
        response = page({**scope, "query": query, "options": options})
        rows = response.get("data") or []
        if not isinstance(rows, list):
            raise InventoryError("Resource Graph did not return rows in objectArray format.")
        yield from rows
        seen += len(rows)

        skip_token = response.get("$skipToken")
        if skip_token:
            if not rows:
                left_out("Resource Graph returned a $skipToken with an empty page.")
                return
            continue
        total = response.get("totalRecords")
        if total is not None and seen < total:
            left_out(f"Resource Graph returned {seen} of {total} rows and no $skipToken; the inventory is incomplete.")
        elif str(response.get("resultTruncated")).lower() == "true":
            left_out("Resource Graph reported resultTruncated and no $skipToken; the inventory is incomplete.")
        return


def _tags(value: Any) -> dict[str, str]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _state(row: dict[str, Any]) -> str | None:
    """Run state, for the types that expose one in Resource Graph.

    Virtual machines: the power state code with its "PowerState/" prefix
    removed ("running", "deallocated"). Managed disks: the disk state
    ("attached", "unattached"). Every other type: None.
    """
    power = _text(row.get("powerState"))
    if power:
        return power.split("/", 1)[-1].lower()
    disk = _text(row.get("diskState"))
    return disk.lower() if disk else None


def _resource_group_key(subscription_id: str, name: str) -> tuple[str, str]:
    return subscription_id.lower(), name.lower()


def to_record(row: dict[str, Any], group_tags: dict[tuple[str, str], dict[str, str]]) -> dict[str, Any]:
    """Turn one ``resources`` row into a schema 2.0 record."""
    scope = (row.get("subscriptionId") or "").lower()
    resource_group = (row.get("resourceGroup") or "").lower() or None
    inherited = group_tags.get(_resource_group_key(scope, resource_group), {}) if resource_group else {}
    return {
        "resource_id": row["id"].lower(),
        "platform": PLATFORM,
        "scope": scope,
        "location": _text(row.get("location")),
        "resource_type": row["type"].lower(),
        "name": row.get("name") or name_from_id(row["id"]),
        "kind": _text(row.get("kind")),
        "container": resource_group,
        "tags": _tags(row.get("tags")),
        "inherited_tags": dict(inherited),
        "state": _state(row),
        "provisioning_state": _text(row.get("provisioningState")),
        "created_at": _text(row.get("timeCreated")) or _text(row.get("creationTime")),
        "estimated_monthly_cost": None,
    }


def resource_group_record(row: dict[str, Any]) -> dict[str, Any]:
    """Turn one resource group row into a schema 2.0 record."""
    return {
        "resource_id": row["id"].lower(),
        "platform": PLATFORM,
        "scope": (row.get("subscriptionId") or "").lower(),
        "location": _text(row.get("location")),
        "resource_type": RESOURCE_GROUP_TYPE,
        "name": row.get("name") or name_from_id(row["id"]),
        "kind": None,
        "container": (row.get("name") or "").lower() or None,
        "tags": _tags(row.get("tags")),
        "inherited_tags": {},
        "state": None,
        "provisioning_state": _text(row.get("provisioningState")),
        "created_at": None,
        "estimated_monthly_cost": None,
    }


def discover(
    page: PageFn,
    *,
    subscriptions: Iterable[str] | None = None,
    management_groups: Iterable[str] | None = None,
    include_resource_groups: bool = True,
    page_size: int = PAGE_SIZE,
    arm_get: ArmGetFn | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> dict[str, Any]:
    """Read the inventory and return the schema 2.0 document.

    ``incomplete`` in the document lists what the inventory is known to lack:
    a subscription that was asked for and not returned, and a query whose
    result Resource Graph cut short. It is empty when nothing is known to lack.

    With ``arm_get``, each resource group record gets ``created_at`` from one
    Resource Manager list per subscription. Without it, or when Resource
    Manager refuses that list (HTTP 403), ``created_at`` stays None.
    """
    subscriptions = list(subscriptions or [])
    management_groups = list(management_groups or [])
    incomplete: list[dict[str, Any]] = []
    scope = {
        "subscriptions": subscriptions,
        "management_groups": management_groups,
        "page_size": page_size,
        "incomplete": incomplete,
        "max_pages": max_pages,
    }

    scopes = [
        {
            "scope": (row.get("subscriptionId") or "").lower(),
            "scope_type": "subscription",
            "name": row.get("name"),
            "state": _text(row.get("subscriptionState")),
            "tags": _tags(row.get("tags")),
        }
        for row in query_rows(page, SUBSCRIPTIONS_QUERY, **scope)
    ]

    returned = {entry["scope"] for entry in scopes}
    for subscription in subscriptions:
        if subscription.lower() not in returned:
            incomplete.append(
                {
                    "scope": subscription.lower(),
                    "reason": "asked for and not returned by Resource Graph: no read access to it, or it does not exist",
                }
            )

    group_rows = list(query_rows(page, RESOURCE_GROUPS_QUERY, **scope))
    group_tags = {
        _resource_group_key(row.get("subscriptionId") or "", row.get("name") or ""): _tags(row.get("tags"))
        for row in group_rows
    }

    resources = [resource_group_record(row) for row in group_rows] if include_resource_groups else []
    if arm_get is not None and resources:
        created: dict[tuple[str, str], str] = {}
        for subscription in sorted({record["scope"] for record in resources}):
            try:
                times = resource_group_created_times(arm_get, subscription, max_pages=max_pages, incomplete=incomplete)
            except Exception as error:
                if getattr(error, "code", None) != 403:
                    raise
                continue
            created.update({(subscription, name): moment for name, moment in times.items()})
        for record in resources:
            record["created_at"] = created.get((record["scope"], record["container"]))
    resources.extend(to_record(row, group_tags) for row in query_rows(page, RESOURCES_QUERY, **scope))

    return {
        "schema_version": SCHEMA_VERSION,
        "platform": PLATFORM,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scopes": scopes,
        "incomplete": incomplete,
        "resources": resources,
    }


def tag_coverage(resources: Iterable[dict[str, Any]], names: Iterable[str]) -> dict[str, int]:
    """Count the resources that carry each tag name, compared without regard to case as Azure does."""
    counts = {name: 0 for name in names}
    for resource in resources:
        present = {key.lower() for key in resource["tags"]}
        for name in counts:
            if name.lower() in present:
                counts[name] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m adapters.azure.inventory",
        description="Read-only Azure inventory through Azure Resource Graph, as report schema 2.0 records.",
    )
    parser.add_argument("--subscription", action="append", default=[], help="subscription id; repeat for several")
    parser.add_argument(
        "--management-group", action="append", default=[], help="management group id; repeat for several"
    )
    parser.add_argument("--out", required=True, help="file to write, or - for standard output")
    parser.add_argument("--no-resource-groups", action="store_true", help="leave resource groups out of the records")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"stop each paged list after this many pages and report the inventory incomplete (default {DEFAULT_MAX_PAGES})",
    )
    parser.add_argument(
        "--az-cli",
        action="store_true",
        help="sign in with the Azure CLI account that owns the first --subscription, "
        "instead of the default credential chain",
    )
    args = parser.parse_args(argv)
    if bool(args.subscription) == bool(args.management_group):
        parser.error("give --subscription or --management-group, one of the two")

    credential = None
    if args.az_cli:
        from azure.identity import AzureCliCredential

        credential = AzureCliCredential(subscription=args.subscription[0] if args.subscription else None)

    document = discover(
        sdk_page_fn(credential),
        arm_get=sdk_arm_get_fn(credential),
        subscriptions=args.subscription,
        management_groups=args.management_group,
        include_resource_groups=not args.no_resource_groups,
        max_pages=args.max_pages,
    )
    text = json.dumps(document, indent=2, sort_keys=True)
    if args.out == "-":
        sys.stdout.write(text + "\n")
    else:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"{len(document['resources'])} records from {len(document['scopes'])} subscriptions -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Read-only Google Cloud inventory through Cloud Asset Inventory.

Calls ``searchAllResources`` over an organization, a folder or a project and
returns one record per resource in report schema 2.0:

    resource_id    full resource name, ``//compute.googleapis.com/projects/...``
    platform       ``"gcp"``
    scope          project id (see ``_resolve_scope``)
    location       ``global``, a region or a zone; ``None`` when not reported
    resource_type  asset type, ``compute.googleapis.com/Instance``
    name           display name; the last segment of the resource name when
                   the resource has none
    tags           the resource's labels as found; ``{}`` when it has none
    state          as reported by the resource's own API; ``None`` when absent
    created_at     RFC 3339 timestamp; ``None`` when absent
    estimated_monthly_cost  ``None``: this adapter reads no cost source
    grade          ``labels+ownership``, ``ownership`` or ``none``, from the
                   asset type's class in ``resource_types.yaml``

The module makes one kind of API call and writes nothing to the cloud.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from adapters.http import DEFAULT_MAX_PAGES, bearer_request, safe_opener
from adapters.schema import SCHEMA_VERSION, name_from_id

PLATFORM = "gcp"
MAX_PAGE_SIZE = 500  # the API's cap on pageSize
PROJECT_ASSET_TYPE = "cloudresourcemanager.googleapis.com/Project"

_SCOPE = re.compile(r"^(organizations/[0-9]+|folders/[0-9]+|projects/[^/\s]+)$")
_PROJECT_SEGMENT = re.compile(r"/projects/([^/]+)")
_EXPIRES_AT = re.compile(r"^([0-9]{4})([0-9]{2})([0-9]{2})t([0-9]{2})([0-9]{2})z$")

# One request in, one response page out. The request carries scope, query,
# asset_types, page_size and page_token; the page is the REST response body:
# {"results": [ResourceSearchResult...], "nextPageToken": "..."}.
PageFetcher = Callable[[dict[str, Any]], dict[str, Any]]


# ---------------------------------------------------------------------------
# Asset type classes
# ---------------------------------------------------------------------------

RESOURCE_TYPES_FILE = Path(__file__).with_name("resource_types.yaml")


class TypeClasses:
    """The asset type classes of ``resource_types.yaml`` and the grade each class gets."""

    def __init__(self, document: dict[str, Any]):
        self.grades: dict[str, str] = dict(document["grades"])
        self.default_class: str = document["default_class"]
        self.types: dict[str, str] = dict(document.get("types") or {})
        unknown = {cls for cls in [self.default_class, *self.types.values()] if cls not in self.grades}
        if unknown:
            raise ValueError(f"resource types file names a class with no grade: {sorted(unknown)}")

    def is_classified(self, asset_type: str) -> bool:
        return asset_type in self.types

    def class_of(self, asset_type: str) -> str:
        return self.types.get(asset_type, self.default_class)

    def grade(self, asset_type: str) -> str:
        return self.grades[self.class_of(asset_type)]


@cache
def load_type_classes(path: Path = RESOURCE_TYPES_FILE) -> TypeClasses:
    """Read the type classes. PyYAML is imported here, on first use."""
    import yaml

    with open(path, encoding="utf-8") as handle:
        return TypeClasses(yaml.safe_load(handle))


# ---------------------------------------------------------------------------
# Expiry label
# ---------------------------------------------------------------------------


def decode_expires_at(value: str) -> datetime:
    """Decode an ``expires-at`` label value, ``yyyymmddthhmmz``, to a UTC datetime.

    ``20261201t0000z`` is 2026-12-01T00:00:00Z. Raises ``ValueError`` for a
    value in any other form or one that names an impossible date; a caller
    reports that as an invalid expiry, never as no expiry.
    """
    match = _EXPIRES_AT.fullmatch(value)
    if not match:
        raise ValueError(f"not an expires-at label value (expected yyyymmddthhmmz): {value!r}")
    year, month, day, hour, minute = (int(part) for part in match.groups())
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def encode_expires_at(moment: datetime) -> str:
    """Encode a timezone-aware datetime as an ``expires-at`` label value. Seconds are dropped."""
    if moment.tzinfo is None:
        raise ValueError("expires-at needs a timezone-aware datetime")
    return moment.astimezone(UTC).strftime("%Y%m%dt%H%Mz")


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------


def iter_search_results(
    scope: str,
    fetch_page: PageFetcher,
    *,
    query: str = "",
    asset_types: Iterable[str] = (),
    page_size: int = MAX_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    incomplete: list[dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield every ResourceSearchResult in ``scope``, following ``nextPageToken`` to the end.

    After ``max_pages`` pages with more remaining, the search stops: the reason
    is appended to ``incomplete``, or ``RuntimeError`` is raised without one.
    """
    if not _SCOPE.fullmatch(scope):
        raise ValueError(
            f"scope must be organizations/<number>, folders/<number> or projects/<id or number>: {scope!r}"
        )
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    request: dict[str, Any] = {
        "scope": scope,
        "query": query,
        "asset_types": list(asset_types),
        "page_size": page_size,
        "page_token": "",
    }
    seen_tokens: set[str] = set()
    pages = 0
    while True:
        if pages >= max_pages:
            reason = f"{scope}: searchAllResources stopped at the page cap of {max_pages} pages"
            if incomplete is None:
                raise RuntimeError(reason)
            incomplete.append({"scope": None, "reason": reason})
            return
        pages += 1
        page = fetch_page(dict(request))
        yield from page.get("results") or []
        token = page.get("nextPageToken") or ""
        if not token:
            return
        if token in seen_tokens:
            raise RuntimeError("searchAllResources returned the same nextPageToken twice")
        seen_tokens.add(token)
        request["page_token"] = token


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def _project_number(result: dict[str, Any]) -> str | None:
    """The number from the result's ``project`` field, which is always ``projects/<number>``."""
    project = result.get("project") or ""
    if project.startswith("projects/"):
        return project.removeprefix("projects/") or None
    return None


def _project_id_in_name(result: dict[str, Any]) -> str | None:
    """The project id inside the full resource name, when the name carries one.

    A project id starts with a letter and a project number is all digits, so a
    ``projects/<segment>`` that is not all digits is an id.
    """
    match = _PROJECT_SEGMENT.search(result.get("name") or "")
    if match and not match.group(1).isdigit():
        return match.group(1)
    return None


def project_ids_by_number(results: Iterable[dict[str, Any]], scope: str = "") -> dict[str, str]:
    """Map project number to project id from what the results themselves show.

    Three sources, none of which is another API call:

    1. a result whose resource name carries the project id and whose
       ``project`` field carries the number;
    2. a Project asset's ``additionalAttributes.projectId``;
    3. a ``projects/<id>`` scope, where every result is in that one project.
    """
    mapping: dict[str, str] = {}
    scope_id = scope.removeprefix("projects/") if scope.startswith("projects/") else ""
    for result in results:
        number = _project_number(result)
        if not number:
            continue
        project_id = _project_id_in_name(result)
        if not project_id and result.get("assetType") == PROJECT_ASSET_TYPE:
            project_id = (result.get("additionalAttributes") or {}).get("projectId")
        if not project_id and scope_id and not scope_id.isdigit():
            project_id = scope_id
        if project_id:
            mapping.setdefault(number, project_id)
    return mapping


def _resolve_scope(result: dict[str, Any], ids_by_number: dict[str, str]) -> str:
    """The record's ``scope``.

    The project id when it can be established: from the resource name, else
    from the number-to-id map. A resource in a project whose id nothing in the
    result set reveals keeps the project number. A resource that belongs to no
    project (an organization, a folder, an organization policy) has its
    ``organizations/<number>``, or an empty string when the result names none.
    """
    project_id = _project_id_in_name(result)
    if project_id:
        return project_id
    number = _project_number(result)
    if number:
        return ids_by_number.get(number, number)
    return result.get("organization") or ""


def to_record(
    result: dict[str, Any],
    ids_by_number: dict[str, str] | None = None,
    type_classes: TypeClasses | None = None,
) -> dict[str, Any]:
    """Convert one ResourceSearchResult (REST field names) to a schema 2.0 record."""
    resource_id = result["name"]
    type_classes = type_classes or load_type_classes()
    return {
        "resource_id": resource_id,
        "platform": PLATFORM,
        "scope": _resolve_scope(result, ids_by_number or {}),
        "location": result.get("location") or None,
        "resource_type": result.get("assetType") or "",
        "name": result.get("displayName") or name_from_id(resource_id),
        "tags": dict(result.get("labels") or {}),
        "state": result.get("state") or None,
        "created_at": result.get("createTime") or None,
        "estimated_monthly_cost": None,
        "grade": type_classes.grade(result.get("assetType") or ""),
    }


def project_scopes(results: Iterable[dict[str, Any]], ids_by_number: dict[str, str]) -> list[dict[str, Any]]:
    """One entry per project the results belong to: its id, display name, state and labels.

    Name, state and labels come from the project's own asset and are ``None``,
    ``None`` and ``{}`` when the scan did not return it (for example under an
    asset type filter).
    """
    scopes: dict[str, dict[str, Any]] = {}
    for result in results:
        number = _project_number(result)
        project_id = _project_id_in_name(result) or (ids_by_number.get(number, number) if number else None)
        if not project_id:
            continue
        entry = scopes.setdefault(
            project_id, {"scope": project_id, "scope_type": "project", "name": None, "state": None, "tags": {}}
        )
        if result.get("assetType") == PROJECT_ASSET_TYPE:
            entry["name"] = result.get("displayName") or None
            entry["state"] = result.get("state") or None
            entry["tags"] = dict(result.get("labels") or {})
    return [scopes[key] for key in sorted(scopes)]


def _unresolved_numbers(results: Iterable[dict[str, Any]], ids_by_number: dict[str, str]) -> set[str]:
    """Project numbers whose records would carry the number as ``scope``."""
    return {
        number
        for result in results
        if not _project_id_in_name(result) and (number := _project_number(result)) and number not in ids_by_number
    }


# HTTP status of a refused search, and what it means for the inventory. The
# REST transport raises urllib.error.HTTPError and the client library raises
# google.api_core.exceptions.GoogleAPICallError; both carry the status as
# ``code``.
_REFUSALS = {
    403: "the caller cannot search this scope",
    429: "the search was throttled",
}


def discover(
    scope: str,
    fetch_page: PageFetcher | None = None,
    *,
    query: str = "",
    asset_types: Iterable[str] = (),
    page_size: int = MAX_PAGE_SIZE,
    type_classes: TypeClasses | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> dict[str, Any]:
    """Read the inventory of ``scope`` and return the schema 2.0 document.

    All pages are read before any record is built, because a project's id may
    only appear on a later page than a resource that shows its number.

    Under a ``query`` or ``asset_types`` filter the results may show a
    project's number and nothing that gives its id. The projects of the scope
    are then searched once more, as Project assets, to complete the map. An
    unfiltered scan already holds them and makes no second search.
    """
    fetch_page = fetch_page or client_page_fetcher()
    type_classes = type_classes or load_type_classes()
    asset_types = list(asset_types)
    incomplete: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    try:
        for result in iter_search_results(
            scope,
            fetch_page,
            query=query,
            asset_types=asset_types,
            page_size=page_size,
            max_pages=max_pages,
            incomplete=incomplete,
        ):
            results.append(result)
    except Exception as error:
        reason = _REFUSALS.get(getattr(error, "code", None))
        if reason is None:
            raise
        incomplete.append({"scope": None, "reason": f"{scope}: {reason} after {len(results)} results"})
    ids_by_number = project_ids_by_number(results, scope)

    if (query or asset_types) and _unresolved_numbers(results, ids_by_number):
        projects = iter_search_results(
            scope,
            fetch_page,
            asset_types=[PROJECT_ASSET_TYPE],
            page_size=page_size,
            max_pages=max_pages,
            incomplete=incomplete,
        )
        for number, project_id in project_ids_by_number(projects).items():
            ids_by_number.setdefault(number, project_id)

    for number in sorted(_unresolved_numbers(results, ids_by_number)):
        incomplete.append({"scope": number, "reason": "the project number could not be resolved to a project id"})

    unclassified = Counter(
        asset_type for result in results if not type_classes.is_classified(asset_type := result.get("assetType") or "")
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": PLATFORM,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scopes": project_scopes(results, ids_by_number),
        "unclassified_types": dict(sorted(unclassified.items())),
        "incomplete": incomplete,
        "resources": [to_record(result, ids_by_number, type_classes) for result in results],
    }


def list_resources(
    scope: str,
    fetch_page: PageFetcher | None = None,
    *,
    query: str = "",
    asset_types: Iterable[str] = (),
    page_size: int = MAX_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """Return a schema 2.0 record for every resource in ``scope``."""
    return discover(scope, fetch_page, query=query, asset_types=asset_types, page_size=page_size)["resources"]


# ---------------------------------------------------------------------------
# Transport: the google-cloud-asset client
# ---------------------------------------------------------------------------


def client_page_fetcher(client: Any = None) -> PageFetcher:
    """A ``PageFetcher`` backed by ``google.cloud.asset_v1.AssetServiceClient``.

    The client is created from Application Default Credentials when none is
    given. The import is deferred so everything above works, and is tested,
    without the client library installed.
    """
    from google.cloud import asset_v1

    client = client or asset_v1.AssetServiceClient()

    def fetch_page(request: dict[str, Any]) -> dict[str, Any]:
        pager = client.search_all_resources(request=asset_v1.SearchAllResourcesRequest(**request))
        page = next(iter(pager.pages))
        return json.loads(type(page).to_json(page))

    return fetch_page


# ---------------------------------------------------------------------------
# Transport: plain REST, standard library only
# ---------------------------------------------------------------------------

REST_ENDPOINT = "https://cloudasset.googleapis.com/v1"


def rest_request_url(request: dict[str, Any]) -> str:
    """The searchAllResources URL for one request."""
    params: list[tuple[str, str]] = [("pageSize", str(request["page_size"]))]
    if request.get("query"):
        params.append(("query", request["query"]))
    params.extend(("assetTypes", asset_type) for asset_type in request.get("asset_types") or [])
    if request.get("page_token"):
        params.append(("pageToken", request["page_token"]))
    return f"{REST_ENDPOINT}/{request['scope']}:searchAllResources?{urllib.parse.urlencode(params)}"


def rest_page_fetcher(access_token: str, quota_project: str | None = None, *, opener: Any = None) -> PageFetcher:
    """A ``PageFetcher`` that calls the REST endpoint with an OAuth access token.

    ``quota_project`` is sent as ``x-goog-user-project``; a user credential
    needs it, a service account does not. The token is held in memory only and
    is never forwarded on a redirect: the default opener refuses redirects, and
    the token travels in an unredirected header for any opener.
    """
    opener = opener or safe_opener()
    headers = {"x-goog-user-project": quota_project} if quota_project else {}

    def fetch_page(request: dict[str, Any]) -> dict[str, Any]:
        with opener(bearer_request(rest_request_url(request), access_token, headers), timeout=60) as response:
            return json.load(response)

    return fetch_page


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None, fetch_page: PageFetcher | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m adapters.gcp.inventory",
        description="List every resource in a Google Cloud organization, folder or project, with its labels.",
    )
    parser.add_argument("--scope", required=True, help="organizations/<number>, folders/<number> or projects/<id>")
    parser.add_argument("--out", default="-", help="file to write; - for standard output (default)")
    parser.add_argument("--asset-type", action="append", default=[], help="limit to an asset type; repeatable")
    parser.add_argument("--query", default="", help="a searchAllResources query, for example labels.role:iac-registry")
    parser.add_argument("--page-size", type=int, default=MAX_PAGE_SIZE)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"stop the search after this many pages and report the inventory incomplete (default {DEFAULT_MAX_PAGES})",
    )
    args = parser.parse_args(argv)

    document = discover(
        args.scope,
        fetch_page,
        query=args.query,
        asset_types=args.asset_type,
        page_size=args.page_size,
        max_pages=args.max_pages,
    )
    resources = document["resources"]
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"{len(resources)} records from {len(document['scopes'])} projects -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

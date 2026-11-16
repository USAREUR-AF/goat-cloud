"""A bearer token never follows a redirect, and every pagination loop stops at a page cap."""

import email.message
import io
import json
import urllib.error
import urllib.request
import urllib.response

import pytest

from adapters.azure import inventory as azure
from adapters.gcp import inventory as gcp
from adapters.http import bearer_request, safe_opener

TOKEN = "example-token-value"


class FakeHttps(urllib.request.BaseHandler):
    """Answers https requests: the first with a redirect to another host, later ones with JSON.

    Ordered before urllib's own handlers, so no request leaves the process.
    """

    handler_order = 100

    def __init__(self, location, body=None):
        self.location = location
        self.body = body or {}
        self.requests = []

    def https_open(self, request):
        self.requests.append(request)
        headers = email.message.Message()
        if len(self.requests) == 1:
            headers["Location"] = self.location
            response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
            response.msg = "Found"
            return response
        headers["Content-Type"] = "application/json"
        response = urllib.response.addinfourl(
            io.BytesIO(json.dumps(self.body).encode()), headers, request.full_url, 200
        )
        response.msg = "OK"
        return response

    http_open = https_open


def authorization(request):
    """The Authorization header as the request would send it, redirected or not."""
    merged = {**request.headers, **request.unredirected_hdrs}
    return {k.lower(): v for k, v in merged.items()}.get("authorization")


class TestBearerRequest:
    def test_a_following_opener_does_not_forward_the_token_to_another_host(self):
        fake = FakeHttps("https://other.example/collect")
        opener = urllib.request.build_opener(fake)  # follows redirects, as urlopen does
        with opener.open(bearer_request("https://management.azure.com/x", TOKEN), timeout=5):
            pass
        first, second = fake.requests
        assert authorization(first) == f"Bearer {TOKEN}"
        assert second.full_url == "https://other.example/collect"
        assert authorization(second) is None

    def test_a_following_opener_does_not_forward_the_token_to_http(self):
        fake = FakeHttps("http://management.azure.com/x")
        opener = urllib.request.build_opener(fake)
        with opener.open(bearer_request("https://management.azure.com/x", TOKEN), timeout=5):
            pass
        assert authorization(fake.requests[1]) is None

    def test_the_safe_opener_refuses_every_redirect(self):
        fake = FakeHttps("https://other.example/collect")
        with pytest.raises(urllib.error.HTTPError, match="refusing to follow a redirect to 'other.example'"):
            safe_opener(fake)(bearer_request("https://management.azure.com/x", TOKEN), timeout=5)
        assert len(fake.requests) == 1


class FakeCredential:
    def get_token(self, scope):
        return type("Token", (), {"token": TOKEN})()


def test_azure_arm_get_sends_no_token_after_a_redirect():
    fake = FakeHttps("https://other.example/collect", {"value": []})
    get = azure.sdk_arm_get_fn(FakeCredential(), opener=urllib.request.build_opener(fake).open)
    get(azure.resource_group_list_url("00000000-0000-0000-0000-000000000000"))
    assert authorization(fake.requests[0]) == f"Bearer {TOKEN}"
    assert [authorization(r) for r in fake.requests[1:]] == [None]


def test_azure_arm_get_refuses_a_redirect_by_default(monkeypatch):
    fake = FakeHttps("https://other.example/collect")
    monkeypatch.setattr(azure, "safe_opener", lambda: safe_opener(fake))
    get = azure.sdk_arm_get_fn(FakeCredential())
    with pytest.raises(urllib.error.HTTPError):
        get(azure.resource_group_list_url("00000000-0000-0000-0000-000000000000"))
    assert len(fake.requests) == 1


def test_gcp_rest_fetcher_sends_no_token_after_a_redirect():
    fake = FakeHttps("https://other.example/collect", {"results": []})
    fetch = gcp.rest_page_fetcher(TOKEN, "example-project", opener=urllib.request.build_opener(fake).open)
    fetch({"scope": "projects/example-project", "page_size": 10})
    assert authorization(fake.requests[0]) == f"Bearer {TOKEN}"
    assert authorization(fake.requests[1]) is None


def test_gcp_rest_fetcher_refuses_a_redirect_by_default(monkeypatch):
    fake = FakeHttps("https://other.example/collect")
    monkeypatch.setattr(gcp, "safe_opener", lambda: safe_opener(fake))
    fetch = gcp.rest_page_fetcher(TOKEN)
    with pytest.raises(urllib.error.HTTPError):
        fetch({"scope": "projects/example-project", "page_size": 10})
    assert len(fake.requests) == 1


# ---------------------------------------------------------------------------
# Page cap
# ---------------------------------------------------------------------------


def endless_graph(request):
    """A Resource Graph that always has another page."""
    return {"data": [{"id": "x"}], "$skipToken": f"t{request['options'].get('$skipToken', '')}x"}


def test_azure_query_stops_at_the_page_cap_and_reports_it():
    incomplete = []
    rows = list(
        azure.query_rows(endless_graph, azure.RESOURCES_QUERY, subscriptions=["s"], incomplete=incomplete, max_pages=3)
    )
    assert len(rows) == 3
    assert incomplete == [
        {
            "scope": None,
            "reason": "Resource Graph query stopped at the page cap of 3 pages; the inventory is incomplete.",
        }
    ]


def test_azure_query_without_an_incomplete_list_raises_at_the_page_cap():
    with pytest.raises(azure.InventoryError, match="page cap"):
        list(azure.query_rows(endless_graph, azure.RESOURCES_QUERY, subscriptions=["s"], max_pages=2))


def test_azure_resource_group_list_stops_at_the_page_cap():
    calls = []

    def arm_get(url):
        calls.append(url)
        return {"value": [], "nextLink": f"{azure.ARM_ENDPOINT}/next/{len(calls)}"}

    incomplete = []
    azure.resource_group_created_times(arm_get, "s", max_pages=2, incomplete=incomplete)
    assert len(calls) == 2
    assert incomplete[0]["scope"] == "s" and "page cap of 2" in incomplete[0]["reason"]


def test_gcp_search_stops_at_the_page_cap_and_reports_it():
    calls = []

    def fetch_page(request):
        calls.append(request)
        return {"results": [], "nextPageToken": f"t{len(calls)}"}

    document = gcp.discover("projects/example-project", fetch_page, max_pages=4)
    assert len(calls) == 4
    assert document["incomplete"] == [
        {"scope": None, "reason": "projects/example-project: searchAllResources stopped at the page cap of 4 pages"}
    ]


def test_the_default_page_cap_is_generous():
    from adapters.http import DEFAULT_MAX_PAGES

    assert DEFAULT_MAX_PAGES >= 10_000

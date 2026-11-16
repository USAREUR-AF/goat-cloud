"""Azure Resource Graph inventory adapter, against synthetic responses in the REST shape."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adapters import schema
from adapters.azure import inventory

FIXTURES = Path(__file__).parent / "fixtures"
SUBSCRIPTION = "00000000-0000-0000-0000-000000000000"
PAGE_2_TOKEN = "c3ludGhldGljLXBhZ2UtMg=="
RG_ID = f"/subscriptions/{SUBSCRIPTION}/resourcegroups/example-app-rg"


def load(name):
    return json.loads((FIXTURES / name).read_text())


class FakeGraph:
    """Answers the three inventory queries from fixtures and records each request body."""

    def __init__(self, **overrides):
        self.requests = []
        self.responses = {
            "subscriptions": load("subscriptions.json"),
            "resource_groups": load("resource_groups.json"),
            "resources_page1": load("resources_page1.json"),
            "resources_page2": load("resources_page2.json"),
        }
        self.responses.update(overrides)

    def __call__(self, body):
        self.requests.append(body)
        query = body["query"]
        if query == inventory.SUBSCRIPTIONS_QUERY:
            return self.responses["subscriptions"]
        if query == inventory.RESOURCE_GROUPS_QUERY:
            return self.responses["resource_groups"]
        assert query == inventory.RESOURCES_QUERY
        token = body["options"].get("$skipToken")
        if token is None:
            return self.responses["resources_page1"]
        assert token == PAGE_2_TOKEN
        return self.responses["resources_page2"]

    def resource_requests(self):
        return [r for r in self.requests if r["query"] == inventory.RESOURCES_QUERY]


@pytest.fixture
def graph():
    return FakeGraph()


@pytest.fixture
def document(graph):
    return inventory.discover(graph, subscriptions=[SUBSCRIPTION])


def by_name(document):
    return {r["name"]: r for r in document["resources"]}


class TestPaging:
    def test_follows_skip_token_to_the_last_page(self, graph, document):
        names = [r["name"] for r in document["resources"] if r["resource_type"] != inventory.RESOURCE_GROUP_TYPE]
        assert names == ["example-vm_OsDisk", "Example-VM", "example-vnet", "zone.example", "examplestate"]

        first, second = graph.resource_requests()
        assert "$skipToken" not in first["options"]
        assert second["options"]["$skipToken"] == PAGE_2_TOKEN

    def test_every_request_asks_for_object_rows_and_the_maximum_page(self, graph, document):
        for request in graph.requests:
            assert request["options"]["resultFormat"] == "objectArray"
            assert request["options"]["$top"] == 1000
            assert request["subscriptions"] == [SUBSCRIPTION]
            assert "managementGroups" not in request

    def test_queries_can_be_paged(self):
        # No $skipToken is returned for a query with limit/take/sample, or with only dynamic columns.
        for query in (inventory.RESOURCES_QUERY, inventory.RESOURCE_GROUPS_QUERY, inventory.SUBSCRIPTIONS_QUERY):
            assert "| order by id asc" in query
            assert "project id," in query
            for operator in ("limit", "take", "sample"):
                assert f"| {operator} " not in query

    def test_result_cut_short_is_reported_and_the_rows_read_stand(self):
        page = load("resources_page1.json")
        del page["$skipToken"]
        page["resultTruncated"] = "true"
        document = inventory.discover(FakeGraph(resources_page1=page), subscriptions=[SUBSCRIPTION])
        assert document["incomplete"] == [
            {
                "scope": None,
                "reason": "Resource Graph returned 3 of 5 rows and no $skipToken; the inventory is incomplete.",
            }
        ]
        assert len([r for r in document["resources"] if r["resource_type"] != inventory.RESOURCE_GROUP_TYPE]) == 3
        assert schema.validate_document(document) == []

    def test_result_truncated_flag_alone_is_reported(self):
        page = load("subscriptions.json")
        page["resultTruncated"] = "true"
        document = inventory.discover(FakeGraph(subscriptions=page), subscriptions=[SUBSCRIPTION])
        assert [entry["reason"] for entry in document["incomplete"]] == [
            "Resource Graph reported resultTruncated and no $skipToken; the inventory is incomplete."
        ]

    def test_token_with_empty_page_is_reported(self):
        page = load("resources_page1.json")
        page["data"] = []
        document = inventory.discover(FakeGraph(resources_page1=page), subscriptions=[SUBSCRIPTION])
        assert [entry["reason"] for entry in document["incomplete"]] == [
            "Resource Graph returned a $skipToken with an empty page."
        ]

    def test_query_rows_alone_raises_on_a_cut_result(self):
        page = load("resources_page1.json")
        del page["$skipToken"]
        with pytest.raises(inventory.InventoryError, match="3 of 5 rows"):
            list(inventory.query_rows(FakeGraph(resources_page1=page), inventory.RESOURCES_QUERY, subscriptions=["s"]))

    def test_subscription_asked_for_and_not_returned_is_reported(self):
        other = "11111111-1111-1111-1111-11111111AAAA"
        document = inventory.discover(FakeGraph(), subscriptions=[SUBSCRIPTION, other])
        assert document["incomplete"] == [
            {
                "scope": other.lower(),
                "reason": "asked for and not returned by Resource Graph: no read access to it, or it does not exist",
            }
        ]

    def test_complete_inventory_reports_nothing(self, document):
        assert document["incomplete"] == []

    def test_table_format_is_refused(self):
        page = load("subscriptions.json")
        page["data"] = {"columns": [], "rows": []}
        with pytest.raises(inventory.InventoryError, match="objectArray"):
            inventory.discover(FakeGraph(subscriptions=page), subscriptions=[SUBSCRIPTION])


class TestScope:
    def test_management_group_scope(self, graph):
        inventory.discover(graph, management_groups=["example-mg"])
        for request in graph.requests:
            assert request["managementGroups"] == ["example-mg"]
            assert "subscriptions" not in request

    def test_subscription_ids_are_sent_lowercased(self, graph):
        inventory.discover(graph, subscriptions=["ABCDEF00-0000-0000-0000-000000000000"])
        assert graph.requests[0]["subscriptions"] == ["abcdef00-0000-0000-0000-000000000000"]

    @pytest.mark.parametrize("scope", [{}, {"subscriptions": [SUBSCRIPTION], "management_groups": ["example-mg"]}])
    def test_exactly_one_kind_of_scope(self, graph, scope):
        with pytest.raises(ValueError):
            inventory.discover(graph, **scope)

    def test_document_lists_the_subscriptions_read(self, document):
        assert document["schema_version"] == "2.0"
        assert document["platform"] == "azure"
        assert document["scopes"] == [
            {
                "scope": SUBSCRIPTION,
                "scope_type": "subscription",
                "name": "example-subscription",
                "state": "Enabled",
                "tags": {},
            }
        ]


class TestRecord:
    def test_record_fields(self, document):
        assert by_name(document)["Example-VM"] == {
            "resource_id": f"{RG_ID}/providers/microsoft.compute/virtualmachines/example-vm",
            "platform": "azure",
            "scope": SUBSCRIPTION,
            "location": "westeurope",
            "resource_type": "microsoft.compute/virtualmachines",
            "name": "Example-VM",
            "kind": None,
            "container": "example-app-rg",
            "tags": {"project": "example", "Owner": "platform-team"},
            "inherited_tags": {
                "Project": "example",
                "Environment": "dev",
                "ManagedBy": "terraform",
                "Lifecycle": "persistent",
                "Repository": "https://git.example/example-org/example-repo",
            },
            "state": "deallocated",
            "provisioning_state": "Succeeded",
            "created_at": "2030-01-02T03:04:06.0000000Z",
            "estimated_monthly_cost": None,
        }

    def test_document_is_a_valid_schema_document(self, document):
        assert schema.validate_document(document) == []
        assert document["schema_version"] == schema.SCHEMA_VERSION

    def test_every_record_is_a_valid_schema_record(self, document):
        assert document["resources"]
        for record in document["resources"]:
            assert schema.validate_record(record) == []

    @pytest.mark.parametrize("name", [None, ""])
    def test_name_falls_back_to_the_last_id_segment(self, name):
        row = {
            "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg/providers/Example.Type/things/Thing-1",
            "name": name,
        }
        record = inventory.to_record({**row, "type": "Example.Type/things", "subscriptionId": SUBSCRIPTION}, {})
        assert record["name"] == "Thing-1"
        assert schema.validate_record(record) == []
        group = inventory.resource_group_record({"id": RG_ID, "name": name, "subscriptionId": SUBSCRIPTION})
        assert group["name"] == "example-app-rg"
        assert schema.validate_record(group) == []

    def test_resource_ids_are_lowercased_and_names_are_not(self, document):
        for record in document["resources"]:
            assert record["resource_id"] == record["resource_id"].lower()
            assert record["resource_type"] == record["resource_type"].lower()
        assert "Example-VM" in by_name(document)

    def test_null_tags_become_an_empty_dict(self, document):
        assert by_name(document)["example-vm_OsDisk"]["tags"] == {}
        assert by_name(document)["example-untagged-rg"]["tags"] == {}

    def test_tag_names_keep_their_case(self, document):
        assert set(by_name(document)["Example-VM"]["tags"]) == {"project", "Owner"}

    def test_state_only_for_types_that_expose_one(self, document):
        records = by_name(document)
        assert records["Example-VM"]["state"] == "deallocated"
        assert records["example-vm_OsDisk"]["state"] == "unattached"
        assert records["example-vnet"]["state"] is None
        assert records["examplestate"]["state"] is None

    def test_created_at_from_either_property_or_none(self, document):
        records = by_name(document)
        assert records["example-vm_OsDisk"]["created_at"] == "2030-01-02T03:04:05.0000000Z"
        assert records["examplestate"]["created_at"] == "2030-01-02T03:04:07.0000000Z"
        assert records["example-vnet"]["created_at"] is None

    def test_kind_and_global_location(self, document):
        records = by_name(document)
        assert records["examplestate"]["kind"] == "StorageV2"
        assert records["zone.example"]["location"] == "global"


class TestResourceGroupInheritance:
    def test_group_tags_are_matched_without_regard_to_case(self, document):
        # resourcecontainers names the group "Example-App-RG"; resources names it "example-app-rg".
        for name in ("Example-VM", "example-vm_OsDisk", "example-vnet"):
            assert by_name(document)[name]["inherited_tags"]["Project"] == "example"

    def test_inherited_tags_are_not_merged_into_tags(self, document):
        disk = by_name(document)["example-vm_OsDisk"]
        assert disk["tags"] == {}
        assert len(disk["inherited_tags"]) == 5

    def test_group_without_tags_gives_nothing(self, document):
        assert by_name(document)["zone.example"]["inherited_tags"] == {}

    def test_group_of_another_subscription_is_not_used(self):
        row = load("resources_page1.json")["data"][0]
        other = {("11111111-1111-1111-1111-111111111111", "example-app-rg"): {"Project": "other"}}
        assert inventory.to_record(row, other)["inherited_tags"] == {}

    def test_resource_groups_are_records(self, document):
        group = by_name(document)["Example-App-RG"]
        assert group["resource_id"] == RG_ID
        assert group["resource_type"] == "microsoft.resources/subscriptions/resourcegroups"
        assert group["container"] == "example-app-rg"
        assert group["tags"]["Project"] == "example"
        assert group["inherited_tags"] == {}

    def test_resource_groups_can_be_left_out(self, graph):
        document = inventory.discover(graph, subscriptions=[SUBSCRIPTION], include_resource_groups=False)
        assert len(document["resources"]) == 5
        assert by_name(document)["Example-VM"]["inherited_tags"]["Project"] == "example"


def test_tag_coverage_ignores_name_case(document):
    names = ["Project", "Environment", "ManagedBy", "Lifecycle", "Repository"]
    assert inventory.tag_coverage(document["resources"], names) == {
        "Project": 3,  # one resource group, "project" on the VM, "Project" on the DNS zone
        "Environment": 1,
        "ManagedBy": 1,
        "Lifecycle": 1,
        "Repository": 1,
    }


class TestCommandLine:
    def test_writes_the_document(self, graph, tmp_path, monkeypatch, capsys):
        arm = FakeArm()
        monkeypatch.setattr(inventory, "sdk_page_fn", lambda credential=None: graph)
        monkeypatch.setattr(inventory, "sdk_arm_get_fn", lambda credential=None: arm)
        out = tmp_path / "inventory.json"
        assert inventory.main(["--subscription", SUBSCRIPTION, "--out", str(out)]) == 0
        document = json.loads(out.read_text())
        assert len(document["resources"]) == 7
        assert len(arm.urls) == 2
        assert all(r["created_at"] for r in document["resources"] if r["resource_type"].endswith("/resourcegroups"))
        assert "7 records from 1 subscriptions" in capsys.readouterr().out

    def test_needs_exactly_one_kind_of_scope(self, capsys):
        with pytest.raises(SystemExit):
            inventory.main(["--out", "-"])
        with pytest.raises(SystemExit):
            inventory.main(["--subscription", SUBSCRIPTION, "--management-group", "example-mg", "--out", "-"])


ARM_PAGE_1 = (
    f"https://management.azure.com/subscriptions/{SUBSCRIPTION}/resourcegroups"
    "?api-version=2021-04-01&$expand=createdTime"
)


class FakeArm:
    """Answers the Resource Manager resource group list from fixtures and records each URL."""

    def __init__(self, error=None):
        self.urls = []
        self.error = error
        self.page1 = load("arm_resource_groups_page1.json")
        self.page2 = load("arm_resource_groups_page2.json")

    def __call__(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.page1 if url == ARM_PAGE_1 else self.page2


class HttpError(Exception):
    """Stands for urllib.error.HTTPError, which carries the status as ``code``."""

    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


class TestResourceGroupCreationTime:
    def test_resource_groups_get_created_at_from_resource_manager(self, graph):
        arm = FakeArm()
        document = inventory.discover(graph, subscriptions=[SUBSCRIPTION], arm_get=arm)
        records = by_name(document)
        assert records["Example-App-RG"]["created_at"] == "2030-01-02T03:04:05.1234567Z"
        assert records["example-untagged-rg"]["created_at"] == "2030-01-03T00:00:00.0000000Z"
        assert schema.validate_document(document) == []

    def test_one_list_per_subscription_following_next_link(self, graph):
        arm = FakeArm()
        inventory.discover(graph, subscriptions=[SUBSCRIPTION], arm_get=arm)
        assert arm.urls == [ARM_PAGE_1, arm.page1["nextLink"]]

    def test_other_records_keep_their_own_creation_time(self, graph):
        document = inventory.discover(graph, subscriptions=[SUBSCRIPTION], arm_get=FakeArm())
        assert by_name(document)["example-vm_OsDisk"]["created_at"] == "2030-01-02T03:04:05.0000000Z"
        assert by_name(document)["example-vnet"]["created_at"] is None

    def test_without_the_hook_created_at_is_none_and_no_call_is_made(self, document):
        assert by_name(document)["Example-App-RG"]["created_at"] is None

    def test_group_missing_from_the_list_or_without_the_field_is_none(self, graph):
        arm = FakeArm()
        del arm.page1["value"][0]["createdTime"]
        arm.page2["value"] = []
        document = inventory.discover(graph, subscriptions=[SUBSCRIPTION], arm_get=arm)
        assert by_name(document)["Example-App-RG"]["created_at"] is None
        assert by_name(document)["example-untagged-rg"]["created_at"] is None

    def test_refused_list_leaves_created_at_none(self, graph):
        document = inventory.discover(graph, subscriptions=[SUBSCRIPTION], arm_get=FakeArm(error=HttpError(403)))
        assert by_name(document)["Example-App-RG"]["created_at"] is None
        assert document["incomplete"] == []

    def test_any_other_error_is_raised(self, graph):
        with pytest.raises(HttpError):
            inventory.discover(graph, subscriptions=[SUBSCRIPTION], arm_get=FakeArm(error=HttpError(500)))

    def test_repeated_next_link_is_an_error(self):
        arm = FakeArm()
        arm.page2["nextLink"] = arm.page1["nextLink"]
        with pytest.raises(inventory.InventoryError, match="same nextLink twice"):
            inventory.resource_group_created_times(arm, SUBSCRIPTION)

    def test_no_call_when_resource_groups_are_left_out(self, graph):
        arm = FakeArm()
        inventory.discover(graph, subscriptions=[SUBSCRIPTION], include_resource_groups=False, arm_get=arm)
        assert arm.urls == []

    def test_list_url(self):
        assert inventory.resource_group_list_url(SUBSCRIPTION) == ARM_PAGE_1


class TestArmGetFunction:
    class Credential:
        def __init__(self):
            self.scopes = []

        def get_token(self, scope):
            self.scopes.append(scope)
            return SimpleNamespace(token="synthetic-token")

    class Response:
        def __enter__(self):
            import io

            return io.StringIO('{"value": []}')

        def __exit__(self, *exc):
            return False

    def test_sends_a_bearer_token_for_resource_manager(self):
        sent = []
        credential = self.Credential()

        def opener(request, timeout):
            sent.append(request)
            return self.Response()

        get = inventory.sdk_arm_get_fn(credential, opener=opener)
        assert get(ARM_PAGE_1) == {"value": []}
        assert credential.scopes == ["https://management.azure.com/.default"]
        assert sent[0].full_url == ARM_PAGE_1
        assert sent[0].get_method() == "GET"
        assert sent[0].get_header("Authorization") == "Bearer synthetic-token"

    def test_token_is_never_sent_to_another_host(self):
        credential = self.Credential()
        get = inventory.sdk_arm_get_fn(credential, opener=lambda request, timeout: self.Response())
        with pytest.raises(inventory.InventoryError, match="another host"):
            get("https://management.azure.com.example/subscriptions")
        assert credential.scopes == []


class TestSdkPageFunction:
    """Runs only where the Azure SDKs are installed; they are not test dependencies."""

    def test_request_and_response_mapping(self, monkeypatch):
        resourcegraph = pytest.importorskip("azure.mgmt.resourcegraph")
        pytest.importorskip("azure.identity")
        sent = []

        answer = SimpleNamespace(
            total_records=5, count=3, result_truncated="false", skip_token=PAGE_2_TOKEN, data=[{"id": "x"}]
        )

        class Client:
            def __init__(self, credential):
                self.credential = credential

            def resources(self, request):
                sent.append(request)
                return answer

        monkeypatch.setattr(resourcegraph, "ResourceGraphClient", Client)
        page = inventory.sdk_page_fn(credential=object())
        response = page(
            {
                "subscriptions": [SUBSCRIPTION],
                "query": "resources | project id | order by id asc",
                "options": {"resultFormat": "objectArray", "$top": 1000, "$skipToken": "t"},
            }
        )

        wire = sent[0].serialize()
        assert wire["subscriptions"] == [SUBSCRIPTION]
        assert wire["options"] == {
            "$skipToken": "t",
            "$top": 1000,
            "resultFormat": "objectArray",
            "allowPartialScopes": False,
        }
        assert response == {
            "totalRecords": 5,
            "count": 3,
            "resultTruncated": "false",
            "$skipToken": PAGE_2_TOKEN,
            "data": [{"id": "x"}],
        }

"""A demotion or revocation made through one container holds on every container."""

from unittest.mock import patch

import auth as auth_module
import pytest

PROMOTED = "promoted@example.com"


class _Table:
    """The overrides table as every container sees it."""

    def __init__(self):
        self.items = {}
        self.consistent_reads = 0

    def scan(self, FilterExpression, ExpressionAttributeValues, ProjectionExpression=None):
        prefix = ExpressionAttributeValues[":prefix"]
        return {"Items": [{"resource_arn": k} for k in self.items if k.startswith(prefix)]}

    def get_item(self, Key, ConsistentRead=False):
        assert ConsistentRead
        self.consistent_reads += 1
        item = self.items.get(Key["resource_arn"])
        return {"Item": item} if item else {}


class _Resource:
    def __init__(self, table):
        self._table = table

    def Table(self, name):
        return self._table


@pytest.fixture()
def table(monkeypatch):
    shared = _Table()
    monkeypatch.setenv("OVERRIDES_TABLE", "test-overrides")
    monkeypatch.setattr(auth_module, "_dynamic_admins", None)
    monkeypatch.setattr(auth_module, "_dynamic_admins_loaded_at", None)
    monkeypatch.setattr(auth_module, "_dynamic_viewers", None)
    monkeypatch.setattr(auth_module, "_dynamic_viewers_loaded_at", None)
    with patch("boto3.resource", return_value=_Resource(shared)):
        yield shared


def test_demotion_elsewhere_refuses_the_next_admin_action_at_once(table):
    table.items[f"ADMIN#{PROMOTED}"] = {"resource_arn": f"ADMIN#{PROMOTED}"}
    assert auth_module.is_admin(PROMOTED)  # this container caches the admin set
    assert auth_module.require_admin(PROMOTED) is None

    del table.items[f"ADMIN#{PROMOTED}"]  # demoted through another container

    assert auth_module.require_admin(PROMOTED) is not None


def test_static_admin_needs_no_read(table, monkeypatch):
    monkeypatch.setattr(auth_module, "ADMIN_EMAILS", ["admin@example.com"])
    assert auth_module.require_admin("admin@example.com") is None
    assert table.consistent_reads == 0


def test_failed_read_refuses(table, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("throttled")

    monkeypatch.setattr(table, "get_item", broken)
    assert auth_module.require_admin(PROMOTED) is not None


@pytest.mark.parametrize("loader, prefix", [("_get_dynamic_admins", "ADMIN#"), ("_get_dynamic_viewers", "VIEWER#")])
def test_cached_sets_expire_after_a_minute(table, loader, prefix):
    table.items[f"{prefix}{PROMOTED}"] = {"resource_arn": f"{prefix}{PROMOTED}"}
    clock = [1000.0]
    with patch.object(auth_module.time, "monotonic", side_effect=lambda: clock[0]):
        assert PROMOTED in getattr(auth_module, loader)()
        del table.items[f"{prefix}{PROMOTED}"]
        clock[0] += 30
        assert PROMOTED in getattr(auth_module, loader)()  # still cached
        clock[0] += 31
        assert PROMOTED not in getattr(auth_module, loader)()  # reloaded

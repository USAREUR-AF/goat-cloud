"""Dashboard actions do what their buttons say."""

import json
import sys
import urllib.parse
from unittest.mock import MagicMock, patch

from factories import make_alb_event
from moto import mock_aws


def _post(moto_env, path, **fields):
    event = make_alb_event(path=path, method="POST", email="admin@example.com", body=urllib.parse.urlencode(fields))
    return moto_env["handler"](event, None)


@mock_aws
class TestTriggerEnforce:
    def test_each_severity_button_triggers_with_its_severity(self, moto_env):
        actions = sys.modules["actions"]
        for value, label in (("HIGH", "high-only"), ("ALL", "all-severity")):
            with patch.object(actions, "trigger_enforcer", return_value={}) as trigger:
                resp = _post(moto_env, "/trigger/enforce", min_severity=value)
            assert resp["statusCode"] == 303
            assert resp["headers"]["Location"] == f"/?flash=enforce_{label}"
            assert trigger.call_args.kwargs["min_severity"] == value

    def test_button_without_a_value_triggers_high(self, moto_env):
        with patch.object(sys.modules["actions"], "trigger_enforcer", return_value={}) as trigger:
            resp = _post(moto_env, "/trigger/enforce")
        assert resp["statusCode"] == 303
        assert trigger.call_args.kwargs["min_severity"] == "HIGH"


@mock_aws
class TestHomeFlash:
    def test_flash_message_is_shown(self, moto_env):
        event = make_alb_event(path="/", qs={"flash": "scan_triggered"})
        event["headers"].pop("accept-encoding", None)
        resp = moto_env["handler"](event, None)
        assert "Compliance scan triggered" in resp["body"]


@mock_aws
class TestDestroyAll:
    def test_destroy_all_asks_the_executor_for_every_warned_record(self, moto_env):
        actions = sys.modules["actions"]
        client = MagicMock()
        with (
            patch.object(actions, "HK_EXECUTOR_ARMED", True),
            patch.object(actions, "HK_EXECUTOR_FUNCTION_NAME", "test-hunterkiller-executor"),
            patch.object(actions, "approve_all", return_value=0),
            patch("boto3.client", return_value=client),
        ):
            resp = _post(moto_env, "/destroy-all")
        assert resp["statusCode"] == 303
        payload = json.loads(client.invoke.call_args.kwargs["Payload"])
        assert payload["mode"] == "enforce"
        assert payload["scope"] == "all_warned"

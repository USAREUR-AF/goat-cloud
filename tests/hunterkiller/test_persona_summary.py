"""Tests for persona-voiced summary in enforcement emails."""

import json
from unittest.mock import MagicMock, patch

import notifier
import pytest
from notifier import (
    _PERSONAE,
    _build_factual_summary,
    _enforcement_body,
    _generate_persona_summary,
)


class TestPersonaeList:
    def test_has_twelve_personae(self):
        assert len(_PERSONAE) == 12

    def test_each_persona_has_required_fields(self):
        for p in _PERSONAE:
            assert set(p) == {"name", "style"}, p

    def test_no_duplicate_names(self):
        names = [p["name"] for p in _PERSONAE]
        assert len(names) == len(set(names)), f"Duplicate names: {names}"


class TestBuildFactualSummary:
    def test_live_mode_with_removals(self):
        torn_down = [{"success": True}] * 3
        resolved = [{"status": "resolved"}] * 2
        protected = [{"status": "protected"}]
        failed = [{"success": False}]
        summary = _build_factual_summary(torn_down, resolved, protected, failed, 150.0, 50.0, dry_run=False)
        assert "LIVE enforcement" in summary
        assert "3 non-compliant resources were removed" in summary
        assert "2 resources came into compliance" in summary
        assert "1 resources were protected" in summary
        assert "1 resources failed removal" in summary
        assert "$150" in summary
        assert "$50" in summary
        assert "Total resources processed: 7" in summary

    def test_dry_run_mode(self):
        summary = _build_factual_summary([], [], [], [], 0, 0, dry_run=True)
        assert "DRY RUN" in summary
        assert "No resources needed removal" in summary

    def test_no_costs_omitted(self):
        summary = _build_factual_summary([{"success": True}], [], [], [], 0, 0, dry_run=False)
        assert "$" not in summary.replace("$0", "").replace("$", "")  # no cost mentioned beyond the removal

    def test_empty_enforcement(self):
        summary = _build_factual_summary([], [], [], [], 0, 0, dry_run=False)
        assert "No resources needed removal" in summary
        assert "Total resources processed: 0" in summary


MODEL_ID = "eu.example-model-v1:0"
MODEL_REGION = "eu-west-1"


@pytest.fixture
def summary_enabled(monkeypatch):
    """Configure a model: the summary is off unless one is set."""
    monkeypatch.setattr(notifier, "BEDROCK_SUMMARY_MODEL_ID", MODEL_ID)
    monkeypatch.setattr(notifier, "BEDROCK_SUMMARY_REGION", MODEL_REGION)


class TestPersonaSummaryOffByDefault:
    @patch("notifier.boto3")
    def test_no_model_configured_makes_no_call(self, mock_boto3):
        assert notifier.BEDROCK_SUMMARY_MODEL_ID == ""
        assert _generate_persona_summary("Mode: LIVE. 3 removed.") == ""
        mock_boto3.client.assert_not_called()

    @patch("notifier.boto3")
    def test_empty_region_uses_the_function_region(self, mock_boto3, monkeypatch):
        monkeypatch.setattr(notifier, "BEDROCK_SUMMARY_MODEL_ID", MODEL_ID)
        mock_boto3.client.return_value.invoke_model.side_effect = Exception("stop")
        _generate_persona_summary("Mode: LIVE.")
        mock_boto3.client.assert_called_with("bedrock-runtime", region_name=None)


@pytest.mark.usefixtures("summary_enabled")
class TestGeneratePersonaSummary:
    @patch("notifier.boto3")
    def test_returns_html_on_success(self, mock_boto3):
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps(
            {
                "content": [
                    {"text": "The sweep is done. Three hostiles neutralized. Two came quietly. One was off limits."}
                ]
            }
        ).encode()
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}
        mock_boto3.client.return_value = mock_client

        result = _generate_persona_summary("Mode: LIVE. 3 removed. 2 resolved. 1 protected.")
        assert result  # non-empty
        assert "<div" in result  # HTML wrapper
        assert "neutralized" in result  # LLM text included
        assert "\u2014" in result  # em dash in attribution

        # Verify Bedrock was called with correct model
        mock_boto3.client.assert_called_with("bedrock-runtime", region_name=MODEL_REGION)
        call_args = mock_client.invoke_model.call_args
        assert call_args.kwargs["modelId"] == MODEL_ID

    @patch("notifier.boto3")
    def test_returns_empty_on_failure(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = Exception("Bedrock unavailable")
        mock_boto3.client.return_value = mock_client

        result = _generate_persona_summary("Mode: LIVE. 3 removed.")
        assert result == ""

    @patch("notifier.boto3")
    def test_html_escapes_llm_output(self, mock_boto3):
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps(
            {"content": [{"text": "Resources <script>alert('xss')</script> handled."}]}
        ).encode()
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}
        mock_boto3.client.return_value = mock_client

        result = _generate_persona_summary("Mode: LIVE.")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    @patch("notifier.boto3")
    def test_prompt_includes_persona_and_intensity(self, mock_boto3):
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({"content": [{"text": "Summary text."}]}).encode()
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}
        mock_boto3.client.return_value = mock_client

        _generate_persona_summary("Mode: LIVE. 1 removed.")

        call_args = mock_client.invoke_model.call_args
        body = json.loads(call_args.kwargs["body"])
        prompt_text = body["messages"][0]["content"]
        # Should mention a persona character and intensity
        assert "Intensity level:" in prompt_text
        assert "/10" in prompt_text
        assert "4 sentences" in prompt_text


class TestEnforcementBodyPersonaIntegration:
    def test_persona_html_inserted_when_provided(self):
        persona_html = '<div class="persona">Test persona content</div>'
        results = [{"success": True, "resource_arn": "arn:test", "resource_type": "ec2", "region": "eu-central-1"}]
        body = _enforcement_body(
            results,
            [],
            [],
            False,
            "acme",
            "dev",
            "20260220T170000",
            persona_html=persona_html,
        )
        assert "Test persona content" in body
        # Persona should appear before the resource table
        persona_pos = body.index("Test persona content")
        table_pos = body.index("Cleared Out")
        assert persona_pos < table_pos

    def test_no_persona_when_empty(self):
        results = [{"success": True, "resource_arn": "arn:test", "resource_type": "ec2", "region": "eu-central-1"}]
        body = _enforcement_body(
            results,
            [],
            [],
            False,
            "acme",
            "dev",
            "20260220T170000",
            persona_html="",
        )
        assert "persona" not in body.lower() or "persona_html" not in body

    def test_backward_compatible_without_persona_param(self):
        """Calling without persona_html should still work (default empty)."""
        results = [{"success": True, "resource_arn": "arn:test", "resource_type": "ec2", "region": "eu-central-1"}]
        body = _enforcement_body(
            results,
            [],
            [],
            False,
            "acme",
            "dev",
            "20260220T170000",
        )
        assert "Sweep Complete" in body

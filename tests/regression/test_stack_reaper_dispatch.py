"""Stack reaper: which repository and workflow a teardown dispatch can reach."""

import importlib.util
import json
import os
from unittest.mock import MagicMock, patch

import pytest

# The scp-style clone form, user "git" at the host, then owner/repository.
SCP_FORM = "git" + "@" + "github.com:acme/example-repo.git"


@pytest.fixture()
def dispatch():
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "modules", "aws", "stack-reaper", "lambda", "github_dispatch.py"
    )
    spec = importlib.util.spec_from_file_location("reaper_github_dispatch", os.path.normpath(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestParseRepoUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/acme/example-repo",
            "https://github.com/acme/example-repo.git",
            "https://github.com/acme/example-repo/",
            SCP_FORM,
        ],
    )
    def test_plain_urls(self, dispatch, url):
        assert dispatch._parse_repo_url(url) == ("acme", "example-repo")

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example/github.com/acme/example-repo",
            "https://github.com.evil.example/acme/example-repo",
            "http://github.com/acme/example-repo",
            "https://github.com/acme/example-repo/extra",
            "https://github.com/acme/repo?x=1",
            "https://github.com/acme/repo#frag",
            "https://github.com/acme/..",
            "https://github.com/acme/%2e%2e",
            "https://github.com/ac me/repo",
            "https://github.com/acme",
            "",
            None,
        ],
    )
    def test_refused(self, dispatch, url):
        with pytest.raises(ValueError):
            dispatch._parse_repo_url(url)


class TestDispatch:
    def test_bad_url_is_refused_before_any_credential_is_read(self, dispatch):
        with patch.object(dispatch, "_load_github_app_credentials") as load:
            result = dispatch.dispatch_teardown("https://evil.example/a/b", github_app_secret_arn="arn:x")
        assert result["success"] is False
        load.assert_not_called()

    @pytest.mark.parametrize("workflow_file", ["../other.yml", "teardown.yml/../../x", "teardown", "a b.yml"])
    def test_bad_workflow_file_refused(self, dispatch, workflow_file):
        with patch.object(dispatch, "_load_github_app_credentials") as load:
            result = dispatch.dispatch_teardown(
                "https://github.com/acme/example-repo", workflow_file=workflow_file, github_app_secret_arn="arn:x"
            )
        assert result["success"] is False
        load.assert_not_called()

    def test_installation_token_is_limited_to_the_one_repository(self, dispatch):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            resp = MagicMock()
            resp.__enter__.return_value = resp
            if req.full_url.endswith("/app/installations"):
                resp.read.return_value = json.dumps([{"id": 42, "account": {"login": "acme"}}]).encode()
            else:
                resp.read.return_value = json.dumps({"token": "t"}).encode()
            return resp

        with patch.object(dispatch.urllib.request, "urlopen", fake_urlopen):
            assert dispatch._get_installation_token("jwt", "acme", "example-repo") == "t"

        token_request = calls[1]
        assert token_request.full_url == "https://api.github.com/app/installations/42/access_tokens"
        assert json.loads(token_request.data) == {
            "repositories": ["example-repo"],
            "permissions": {"actions": "write"},
        }

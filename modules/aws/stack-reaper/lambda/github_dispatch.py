"""GitHub Actions workflow_dispatch via GitHub App authentication.

Reads GitHub App credentials (app_id + private_key) from Secrets Manager,
generates a short-lived installation token, and dispatches a workflow.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

import boto3
import rs256

# Cache credentials for the lifetime of the Lambda container
_cached_credentials = None


def _load_github_app_credentials(secret_arn):
    """Read {app_id, private_key} from Secrets Manager."""
    global _cached_credentials
    if _cached_credentials and _cached_credentials.get("_arn") == secret_arn:
        return _cached_credentials

    sm = boto3.client("secretsmanager")
    resp = sm.get_secret_value(SecretId=secret_arn)
    creds = json.loads(resp["SecretString"])
    creds["_arn"] = secret_arn
    _cached_credentials = creds
    return creds


def _base64url(data):
    """Base64url encode without padding."""
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _generate_jwt(app_id, private_key_pem):
    """Generate a JWT for GitHub App authentication (RS256).

    Signed in memory by rs256.py: the private key is never written to a file
    and no other program is run.
    """
    now = int(time.time())
    header = json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    payload = json.dumps({"iat": now - 60, "exp": now + (10 * 60), "iss": str(app_id)}).encode()

    signing_input = _base64url(header) + b"." + _base64url(payload)
    signature = rs256.sign(private_key_pem, signing_input)

    return (signing_input + b"." + _base64url(signature)).decode()


def _get_installation_token(jwt_token, owner, repo):
    """Exchange JWT for an installation token limited to one repository.

    The token can act on that repository only, with the Actions permission
    only, which is what a workflow dispatch needs.
    """
    # First, find the installation ID for this owner
    req = urllib.request.Request(
        "https://api.github.com/app/installations",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - fixed https://api.github.com URL, path parts validated
        installations = json.loads(resp.read())

    installation_id = None
    for inst in installations:
        if inst.get("account", {}).get("login", "").lower() == owner.lower():
            installation_id = inst["id"]
            break

    if not installation_id:
        raise RuntimeError(
            f"No GitHub App installation found for owner '{owner}'. "
            f"Available: {[i.get('account', {}).get('login') for i in installations]}"
        )

    # Exchange for installation token
    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{int(installation_id)}/access_tokens",
        data=json.dumps({"repositories": [repo], "permissions": {"actions": "write"}}).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - fixed https://api.github.com URL, path parts validated
        token_data = json.loads(resp.read())

    return token_data["token"]


_REPO_URL_RE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/(?P<repo>[A-Za-z0-9._-]{1,100})$"
)
_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_WORKFLOW_FILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}\.ya?ml$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


def _parse_repo_url(repo_url):
    """Extract (owner, repo) from a GitHub URL.

    Handles:
      https://github.com/acme/example-repo
      the scp-style form, git@<host>:acme/example-repo.git

    The host must be github.com and the owner and repository names must be
    plain names: the values go into an API path, so a URL with any other
    host, extra path segments, a query or dot-segments is refused.
    """
    url = (repo_url or "").strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    match = _REPO_URL_RE.match(url)
    if not match or match.group("repo") in (".", ".."):
        raise ValueError(f"Not a GitHub repository URL: {repo_url!r}")
    return match.group("owner"), match.group("repo")


def dispatch_teardown(
    repo_url,
    workflow_file="teardown.yml",
    github_app_secret_arn=None,
    ref="main",
    triggered_by="stack-reaper",
):
    """Trigger a GitHub Actions workflow_dispatch for teardown.

    Returns dict with:
      success: bool
      message: str
      status_code: int (HTTP status from GitHub API)
    """
    if not github_app_secret_arn:
        github_app_secret_arn = os.environ.get("GITHUB_APP_SECRET_ARN", "")

    if not github_app_secret_arn:
        return {
            "success": False,
            "message": "No GITHUB_APP_SECRET_ARN configured",
            "status_code": 0,
        }

    try:
        owner, repo = _parse_repo_url(repo_url)
    except ValueError as e:
        return {"success": False, "message": str(e)[:200], "status_code": 0}
    if not _WORKFLOW_FILE_RE.match(workflow_file) or not _REF_RE.match(ref) or ".." in ref:
        return {"success": False, "message": "Refused: workflow file or ref is not a plain name", "status_code": 0}

    # Allow org override — IaC registry may store URLs under a different org
    # than the one where the GitHub App is installed
    github_org = os.environ.get("GITHUB_ORG", "")
    if github_org:
        if not _NAME_RE.match(github_org):
            return {"success": False, "message": "Refused: GITHUB_ORG is not a plain owner name", "status_code": 0}
        print(f"Org override: {owner} → {github_org}")
        owner = github_org

    print(f"Dispatching teardown: {owner}/{repo} workflow={workflow_file}")

    # Authenticate as GitHub App
    creds = _load_github_app_credentials(github_app_secret_arn)
    jwt_token = _generate_jwt(creds["app_id"], creds["private_key"])
    install_token = _get_installation_token(jwt_token, owner, repo)

    # Dispatch workflow
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_file}/dispatches"
    body = json.dumps(
        {
            "ref": ref,
            "inputs": {
                "confirm": "destroy",
                "triggered_by": triggered_by,
            },
        }
    ).encode()

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"token {install_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - fixed https://api.github.com URL, path parts validated
            status = resp.status
            # 204 No Content = success for workflow_dispatch
            return {
                "success": status in (200, 201, 204),
                "message": f"Dispatched {owner}/{repo} {workflow_file} (HTTP {status})",
                "status_code": status,
            }
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        print(f"GitHub API error: HTTP {e.code} — {error_body}")
        return {
            "success": False,
            "message": f"GitHub API error: HTTP {e.code} — {error_body[:200]}",
            "status_code": e.code,
        }
    except Exception as e:
        print(f"GitHub dispatch failed: {e}")
        return {
            "success": False,
            "message": f"Dispatch failed: {str(e)[:200]}",
            "status_code": 0,
        }

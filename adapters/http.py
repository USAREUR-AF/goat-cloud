"""HTTP for the adapters: a bearer token never leaves the host it was meant for.

Standard library only. ``bearer_request`` puts the token in an unredirected
header, which urllib never copies onto a redirected request. ``safe_opener``
refuses every redirect, so a 3xx from the API is an error rather than a
second request to wherever the ``Location`` points (another host, or http).
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

# The number of pages a pagination loop reads before it stops and reports the
# inventory as incomplete. Generous: at 1000 rows a page it is ten million rows.
DEFAULT_MAX_PAGES = 10_000


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            f"refusing to follow a redirect to {urlsplit(newurl).netloc or newurl!r}",
            headers,
            fp,
        )


def safe_opener(*handlers: Any) -> Callable[..., Any]:
    """An ``open(request, timeout=...)`` function that refuses every redirect."""
    return urllib.request.build_opener(_RefuseRedirect, *handlers).open


def bearer_request(url: str, token: str, headers: dict[str, str] | None = None) -> urllib.request.Request:
    """A GET request carrying ``token`` as a bearer token that no redirect forwards."""
    request = urllib.request.Request(url, headers=dict(headers or {}))
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    return request

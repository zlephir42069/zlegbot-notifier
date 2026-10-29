"""Small urllib POST. URLs are never included in raised messages.

Redirects are not followed. A 3xx is a failed post, so a bot token or webhook
URL is not replayed to another host.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from notifier import __version__
from notifier.errors import PostError

USER_AGENT = f"notifier/{__version__}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def post_json(
    url: str,
    payload: dict,
    headers: dict[str, str] | None,
    timeout: float,
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    merged = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    request = urllib.request.Request(url, data=body, headers=merged, method="POST")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            response.read(1024)
    except urllib.error.HTTPError as exc:
        raise PostError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise PostError(f"request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise PostError("request timed out") from exc
    except OSError as exc:
        raise PostError(f"request failed: {exc.__class__.__name__}") from exc

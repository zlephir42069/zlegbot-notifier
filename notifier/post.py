"""Deliver one rendered announcement. A failed delivery raises PostError."""

from __future__ import annotations

import time

from notifier.errors import PostError
from notifier.http import post_json
from notifier.mentions import Mentions, apply_slack, discord_allowed_mentions, webhook_mentions

# Discord rejects content over 2000 characters. Slack's text field rejects
# payloads over 40000. The generic webhook cap keeps a bad template from
# posting an unbounded body.
LIMITS = {
    "discord": 2000,
    "discord_bot": 2000,
    "slack": 40_000,
    "webhook": 100_000,
}

# Tests point this at a local server. The CLI does not expose it.
DISCORD_API_BASE = "https://discord.com/api/v10"


def outbound_text(target_type: str, message: str, mentions: Mentions) -> str:
    """Text that will be posted. Slack mass-mention tags may be replaced."""
    if target_type == "slack":
        return apply_slack(message, mentions)
    return message


def build_request(target, item, message: str) -> tuple[str, dict, dict]:
    """Return `(url, json_body, extra_headers)` for one delivery."""
    mentions = item.mentions
    if target.type == "discord":
        body = {
            "content": message,
            "allowed_mentions": discord_allowed_mentions(mentions),
        }
        return target.url, body, {}
    if target.type == "discord_bot":
        url = f"{DISCORD_API_BASE}/channels/{target.channel}/messages"
        body = {
            "content": message,
            "allowed_mentions": discord_allowed_mentions(mentions),
        }
        return url, body, {"Authorization": f"Bot {target.token}"}
    if target.type == "slack":
        return target.url, {"text": outbound_text("slack", message, mentions)}, {}
    if target.type == "webhook":
        body = {
            "id": item.id,
            "due": item.due_text,
            "message": message,
            "mentions": webhook_mentions(mentions),
        }
        return target.url, body, dict(target.headers)
    raise PostError(f"unknown target type {target.type}")


def deliver(target, item, message: str, *, redactor, sleeper=time.sleep) -> None:
    """POST `message`. Sleep grows as ``backoff * 2**attempt`` between tries.

    The body is the same on every attempt. A non-2xx response, a timeout, or
    a redirect raises PostError and does not record the id.
    """
    url, payload, headers = build_request(target, item, message)
    text = _posted_text(target.type, payload)
    limit = LIMITS.get(target.type)
    if limit is not None and len(text) > limit:
        raise PostError(f"message exceeds {limit} characters")

    delay = target.backoff
    last = "post failed"
    for attempt in range(target.retries):
        try:
            post_json(url, payload, headers, target.timeout)
            return
        except PostError as exc:
            last = redactor.text(str(exc))
            if attempt + 1 >= target.retries:
                break
            sleeper(delay)
            delay *= 2
    raise PostError(last)


def _posted_text(target_type: str, payload: dict) -> str:
    if target_type in {"discord", "discord_bot"}:
        return payload.get("content", "")
    if target_type == "slack":
        return payload.get("text", "")
    return payload.get("message", "")

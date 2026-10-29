"""Redact webhook URLs and env-supplied secrets from anything we print."""

from __future__ import annotations

import re

# Incoming-webhook URLs carry a secret in the path. Strip them even when the
# caller forgot to register the value.
_WEBHOOKISH = re.compile(
    r"https?://[^\s\"']*(?:webhook|hooks\.slack\.com|discord\.com/api/webhooks)[^\s\"']*",
    re.IGNORECASE,
)

_MIN_SECRET = 6


class Redactor:
    def __init__(self) -> None:
        self._secrets: list[str] = []

    def add(self, value: str) -> None:
        if not isinstance(value, str):
            return
        text = value.strip()
        if len(text) < _MIN_SECRET:
            return
        if text not in self._secrets:
            self._secrets.append(text)

    def text(self, value: str) -> str:
        return scrub(value, self._secrets)


def scrub(value: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    out = value
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            out = out.replace(secret, "***")
    return _WEBHOOKISH.sub("***", out)

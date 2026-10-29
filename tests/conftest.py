"""Offline tests. Proxy variables are cleared so urllib stays on 127.0.0.1."""

from __future__ import annotations

import pytest

from tests.support import LocalServer

_PROXY_KEYS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "no_proxy",
    "NO_PROXY",
)


@pytest.fixture(autouse=True)
def _clear_proxy(monkeypatch):
    for key in _PROXY_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def http_server():
    running = LocalServer()
    try:
        yield running
    finally:
        running.close()

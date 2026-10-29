"""Schedule loading. Secrets stay in the environment."""

import json

import pytest

from notifier.config import load_config
from notifier.duration import parse_iso
from notifier.errors import ConfigError
from notifier.template import render


def _write(tmp_path, text, name="notifier.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _schedule(url, body, grace="24h"):
    return f"""
state: .notifier/state.json
grace: {grace}
lock_timeout: 2
targets:
  room:
    type: discord
    url: "{url}"
    retries: 1
    backoff: 0
    timeout: 5
items:
{body}
"""


def test_loads_due_offset_and_renders(tmp_path):
    path = _write(
        tmp_path,
        _schedule(
            "http://127.0.0.1:9/hook",
            """
  - id: release-1-2
    due: "2026-12-01T15:00:00Z"
    target: room
    message: "Release {version} at {due}."
    values:
      version: "1.2.0"
""",
        ),
    )
    config = load_config(path)
    item = config.items[0]
    assert item.due == parse_iso("2026-12-01T15:00:00+00:00")
    assert item.due_text == "2026-12-01T15:00:00+00:00"
    assert item.context()["version"] == "1.2.0"
    assert config.state_path == (tmp_path / ".notifier" / "state.json")
    assert "http://127.0.0.1:9/hook" in config.redactor._secrets


def test_naive_timestamp_and_duplicate_id_and_unknown_target(tmp_path):
    path = _write(
        tmp_path,
        """
items:
  - id: one
    due: "2026-12-01T15:00:00"
    message: "Hi"
    target: missing
  - id: one
    due: "2026-12-01T16:00:00Z"
    message: "Hi"
    target: missing
""",
    )
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    text = "\n".join(caught.value.messages)
    assert "needs a timezone offset" in text
    assert "duplicate item id one" in text
    assert "unknown target missing" in text


def test_unset_environment_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTIFIER_DISCORD_URL", raising=False)
    path = _write(
        tmp_path,
        """
targets:
  room:
    type: discord
    url: ${NOTIFIER_DISCORD_URL}
items: []
""",
    )
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert any("NOTIFIER_DISCORD_URL" in message for message in caught.value.messages)
    listed = load_config(path, resolve_secrets=False)
    assert listed.targets["room"].url == "${NOTIFIER_DISCORD_URL}"


def test_rejects_attribute_placeholder_unquoted_channel_and_overlong_discord(tmp_path):
    path = _write(
        tmp_path,
        """
targets:
  room:
    type: discord
    url: "http://127.0.0.1:9/hook"
  bot:
    type: discord_bot
    token: "example-bot-token"
    channel: 123
items:
  - id: note
    due: "2026-12-01T15:00:00Z"
    target: room
    message: "Hello {name.__class__}"
    values:
      name: world
  - id: long
    due: "2026-12-01T15:00:00Z"
    target: room
    message: "%s"
"""
        % ("x" * 2001,),
    )
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    text = "\n".join(caught.value.messages)
    assert "not a simple name" in text
    assert "quoted id string" in text or "must be a string" in text
    assert "exceeds 2000 characters" in text


def test_json_schedule_and_dollar_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "1.2.0")
    path = tmp_path / "notifier.json"
    path.write_text(
        json.dumps(
            {
                "targets": {
                    "hook": {"type": "webhook", "url": "http://127.0.0.1:9/hook"},
                },
                "items": [
                    {
                        "id": "note",
                        "due": "2026-12-02T09:00:00-05:00",
                        "target": "hook",
                        "message": "Version {version} literal {word}",
                        "values": {"version": "${APP_VERSION}", "word": "$${KEEP}"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    rendered = render(config.items[0].message, config.items[0].context())
    assert rendered == "Version 1.2.0 literal ${KEEP}"
    assert config.items[0].due_text == "2026-12-02T09:00:00-05:00"


def test_skip_must_name_an_item(tmp_path):
    path = _write(
        tmp_path,
        _schedule(
            "http://127.0.0.1:9/hook",
            """
  - id: note
    due: "2026-12-01T15:00:00Z"
    target: room
    message: "Hello {id}"
""",
        )
        + "skip: [missing]\n",
    )
    with pytest.raises(ConfigError, match="skip id missing"):
        load_config(path)


def test_mentions_true_and_header_newline(tmp_path):
    path = _write(
        tmp_path,
        """
targets:
  hook:
    type: webhook
    url: "http://127.0.0.1:9/hook"
    headers:
      X-Note: "fine\\nbad"
items:
  - id: note
    due: "2026-12-01T15:00:00Z"
    target: hook
    message: "Hello"
    mentions: true
""",
    )
    with pytest.raises(ConfigError, match="newline"):
        load_config(path)


def test_inline_target_and_empty_items(tmp_path):
    path = _write(
        tmp_path,
        """
items:
  - id: note
    due: "2026-12-01T15:00:00+00:00"
    message: "Hello {id}"
    target:
      type: slack
      url: "http://127.0.0.1:9/hook"
      retries: 2
      backoff: 0
""",
    )
    config = load_config(path)
    assert config.items[0].target.type == "slack"
    assert config.items[0].target.name == ""
    assert config.grace.total_seconds() == 24 * 3600
    empty = _write(tmp_path, "items: []\n", name="empty.yaml")
    assert load_config(empty).items == []

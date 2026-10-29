"""Slack, Discord bot, and generic webhook bodies."""

import json

from notifier.config import load_config
from notifier.duration import parse_iso
from notifier.post import DISCORD_API_BASE
from notifier.run import execute

NOW = "2026-12-01T15:30:00Z"


def _write(tmp_path, text):
    path = tmp_path / "notifier.yaml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)


def test_slack_strips_mass_mentions_and_opt_in_keeps_them(tmp_path, http_server, capsys):
    url = http_server.url + "/slack"
    config = _write(
        tmp_path,
        f"""
targets:
  room:
    type: slack
    url: "{url}"
    retries: 1
    backoff: 0
    timeout: 5
items:
  - id: quiet
    due: "2026-12-01T15:00:00Z"
    target: room
    message: "Hello <!channel> café"
  - id: loud
    due: "2026-12-01T15:00:00Z"
    target: room
    message: "Hello <!channel>"
    mentions: true
""",
    )
    assert execute(config, now=parse_iso(NOW)) == 0
    bodies = [json.loads(post["body"]) for post in http_server.httpd.posts]
    assert bodies[0] == {"text": "Hello channel café"}
    assert bodies[1] == {"text": "Hello <!channel>"}
    assert "café" not in capsys.readouterr().out


def test_discord_bot_sends_a_bearer_header_without_logging_the_token(tmp_path, http_server, monkeypatch, capsys):
    monkeypatch.setattr("notifier.post.DISCORD_API_BASE", http_server.url)
    token = "example-bot-token-value"
    config = _write(
        tmp_path,
        f"""
targets:
  bot:
    type: discord_bot
    token: "{token}"
    channel: "123456789012345678"
    retries: 1
    backoff: 0
    timeout: 5
items:
  - id: note
    due: "2026-12-01T15:00:00Z"
    target: bot
    message: "Ping"
    mentions:
      users: ["42"]
""",
    )
    assert execute(config, now=parse_iso(NOW)) == 0
    post = http_server.httpd.posts[0]
    assert post["path"] == "/channels/123456789012345678/messages"
    assert post["authorization"] == f"Bot {token}"
    body = json.loads(post["body"])
    assert body["allowed_mentions"] == {"parse": [], "users": ["42"]}
    captured = capsys.readouterr()
    assert token not in captured.out
    assert token not in captured.err
    assert token not in config.state_path.read_text(encoding="utf-8")
    assert DISCORD_API_BASE.startswith("https://discord.com/api/")


def test_generic_webhook_includes_the_mention_policy(tmp_path, http_server):
    url = http_server.url + "/hook"
    config = _write(
        tmp_path,
        f"""
targets:
  hook:
    type: webhook
    url: "{url}"
    retries: 1
    backoff: 0
    timeout: 5
    headers:
      Authorization: "Bearer example-webhook-token"
items:
  - id: status-ping
    due: "2026-12-01T15:00:00Z"
    target: hook
    message: "Status {{code}} at {{due}}."
    values:
      code: 200
""",
    )
    assert execute(config, now=parse_iso(NOW)) == 0
    post = http_server.httpd.posts[0]
    assert post["authorization"] == "Bearer example-webhook-token"
    assert post["content_type"] == "application/json"
    body = json.loads(post["body"])
    assert body["id"] == "status-ping"
    assert body["due"] == "2026-12-01T15:00:00+00:00"
    assert body["message"] == "Status 200 at 2026-12-01T15:00:00+00:00."
    assert body["mentions"]["enabled"] is False
    state = config.state_path.read_text(encoding="utf-8")
    assert "example-webhook-token" not in state

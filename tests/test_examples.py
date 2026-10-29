"""The shipped schedule and workflow stay generic and runnable."""

from notifier import __version__
from notifier.config import load_config
from notifier.duration import parse_iso
from notifier.run import list_text
from notifier.state import empty_state
from tests.support import REPO


def test_version_matches_metadata():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.1.0"' in text
    assert __version__ == "0.1.0"
    assert "Copyright (c) 2026 zlephir42069" in (REPO / "LICENSE").read_text(encoding="utf-8")


def test_yaml_and_json_examples_validate(monkeypatch):
    monkeypatch.setenv("NOTIFIER_DISCORD_URL", "https://example.com/discord")
    monkeypatch.setenv("NOTIFIER_DISCORD_TOKEN", "example-bot-token")
    monkeypatch.setenv("NOTIFIER_DISCORD_CHANNEL", "123456789012345678")
    monkeypatch.setenv("NOTIFIER_SLACK_URL", "https://example.com/slack")
    monkeypatch.setenv("NOTIFIER_WEBHOOK_URL", "https://example.com/hooks/notifier")
    monkeypatch.setenv("NOTIFIER_WEBHOOK_TOKEN", "example-token")
    yaml_config = load_config(REPO / "notifier.example.yaml")
    assert [item.id for item in yaml_config.items] == [
        "release-1-2",
        "weekly-note",
        "maintenance",
        "room-reminder",
    ]
    json_config = load_config(REPO / "notifier.example.json")
    assert json_config.skip == ["draft-note"]
    assert json_config.items[1].context()["code"] == 200


def test_example_list_works_without_secrets():
    config = load_config(REPO / "notifier.example.yaml", resolve_secrets=False)
    text = list_text(config, empty_state(), parse_iso("2026-11-01T00:00:00Z"))
    assert "  release-1-2  2026-12-01T15:00:00+00:00  discord" in text
    assert "  weekly-note  2026-12-02T09:00:00-05:00  slack" in text
    assert "  maintenance  2026-12-03T01:00:00+00:00  hook" in text
    assert "  room-reminder  2026-12-04T18:00:00+00:00  discord_bot" in text


def test_example_workflow_commits_state_and_has_no_inline_secret():
    text = (REPO / "examples" / "github-actions.yml").read_text(encoding="utf-8")
    assert "cron:" in text
    assert "notifier run --config notifier.yaml" in text
    assert ".notifier/state.json" in text
    assert 'git push origin "HEAD:${GITHUB_REF_NAME}"' in text
    assert "contents: write" in text
    assert "actions/checkout@v7" in text
    assert "actions/setup-python@v7" in text
    assert "secrets.NOTIFIER_DISCORD_URL" in text
    assert "https://discord.com/api/webhooks/" not in text
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'python-version: ["3.10", "3.13"]' in ci
    assert "actions/checkout@v7" in ci
    assert "actions/setup-python@v7" in ci

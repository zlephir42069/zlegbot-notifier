"""CLI exit codes. 0 ok, 1 a run failure, 2 a config error."""

import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

from notifier.cli import main
from tests.support import REPO


def _env(root):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env.pop(key, None)
    env["NOTIFIER_DISCORD_URL"] = "https://example.com/discord"
    env["NOTIFIER_DISCORD_TOKEN"] = "example-bot-token"
    env["NOTIFIER_DISCORD_CHANNEL"] = "123456789012345678"
    env["NOTIFIER_SLACK_URL"] = "https://example.com/slack"
    env["NOTIFIER_WEBHOOK_URL"] = "https://example.com/hooks/notifier"
    env["NOTIFIER_WEBHOOK_TOKEN"] = "example-token"
    return env


def _run(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "notifier", *args],
        cwd=root,
        env=_env(root),
        capture_output=True,
        text=True,
        check=False,
    )


def test_usage_and_validate(tmp_path):
    bare = _run(tmp_path)
    assert bare.returncode == 2

    missing = _run(tmp_path, "validate", "--config", "missing.yaml")
    assert missing.returncode == 2
    assert "config not found" in missing.stderr

    example = _run(tmp_path, "validate", "--config", str(REPO / "notifier.example.yaml"))
    assert example.returncode == 0, example.stderr
    assert example.stdout.strip() == "ok"

    bad = tmp_path / "notifier.yaml"
    bad.write_text("items: {}\n", encoding="utf-8")
    invalid = _run(tmp_path, "validate")
    assert invalid.returncode == 2
    assert "config error:" in invalid.stderr

    assert main([]) == 2


def test_run_dry_run_list_and_mark_sent(tmp_path, http_server):
    url = http_server.url + "/hook"
    config = tmp_path / "notifier.yaml"
    config.write_text(
        f"""
state: .notifier/state.json
grace: 1h
lock_timeout: 2
targets:
  room:
    type: discord
    url: "{url}"
    retries: 1
    backoff: 0
    timeout: 5
items:
  - id: note
    due: "2026-12-01T15:00:00Z"
    target: room
    message: "Hello {{id}}"
  - id: later
    due: "2026-12-03T15:00:00Z"
    target: room
    message: "Later"
""",
        encoding="utf-8",
    )
    dry = _run(tmp_path, "run", "--dry-run", "--now", "2026-12-01T15:30:00Z")
    assert dry.returncode == 0, dry.stderr
    assert "would post note room" in dry.stdout
    assert "dry_run=true" in dry.stdout
    assert not (tmp_path / ".notifier").exists()

    listed = _run(tmp_path, "list", "--now", "2026-12-01T15:30:00Z")
    assert listed.returncode == 0, listed.stderr
    assert "  note  2026-12-01T15:00:00+00:00  room" in listed.stdout
    assert "  later  2026-12-03T15:00:00+00:00  room" in listed.stdout

    posted = _run(tmp_path, "run", "--now", "2026-12-01T15:30:00Z")
    assert posted.returncode == 0, posted.stderr
    assert "posted note room" in posted.stdout
    body = json.loads(http_server.httpd.posts[0]["body"])
    assert body["content"] == "Hello note"
    assert body["allowed_mentions"] == {"parse": []}

    again = _run(tmp_path, "run", "--now", "2026-12-01T15:30:00Z")
    assert again.returncode == 0, again.stderr
    assert len(http_server.httpd.posts) == 1

    marked = _run(tmp_path, "mark-sent", "later", "--now", "2026-12-01T15:30:00Z")
    assert marked.returncode == 0, marked.stderr
    assert marked.stdout.strip() == "marked later"
    duplicate = _run(tmp_path, "mark-sent", "later")
    assert duplicate.returncode == 0
    assert duplicate.stdout.strip() == "already sent later"

    unknown = _run(tmp_path, "mark-sent", "missing")
    assert unknown.returncode == 2
    assert "unknown id missing" in unknown.stderr

    bad_now = _run(tmp_path, "list", "--now", "tomorrow")
    assert bad_now.returncode == 2
    assert "config error:" in bad_now.stderr


def test_list_does_not_need_secrets(tmp_path):
    config = tmp_path / "notifier.yaml"
    config.write_text(
        """
targets:
  room:
    type: discord
    url: ${NOTIFIER_DISCORD_URL}
items:
  - id: note
    due: "2026-12-01T15:00:00Z"
    target: room
    message: "Hello {id}"
""",
        encoding="utf-8",
    )
    env = _env(tmp_path)
    env.pop("NOTIFIER_DISCORD_URL")
    result = subprocess.run(
        [sys.executable, "-m", "notifier", "list", "--now", "2026-11-01T00:00:00Z"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "note" in result.stdout
    invalid = subprocess.run(
        [sys.executable, "-m", "notifier", "validate"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid.returncode == 2
    assert "NOTIFIER_DISCORD_URL" in invalid.stderr


def _console_script():
    found = shutil.which("notifier")
    if found:
        return found
    candidates = [
        Path(sysconfig.get_path("scripts")) / "notifier",
        Path.home() / ".local" / "bin" / "notifier",
    ]
    for candidate in candidates:
        if os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def test_console_script():
    script = _console_script()
    assert script is not None
    result = subprocess.run(
        [script, "validate", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "validate" in result.stdout

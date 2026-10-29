"""Posting is idempotent. Late items and failures stay unsent."""

import json
from datetime import timedelta

import pytest

from notifier.config import load_config
from notifier.duration import parse_iso
from notifier.errors import PostError
from notifier.run import execute, list_text, mark_sent
from notifier.state import empty_state


def _write(tmp_path, text):
    path = tmp_path / "notifier.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _config(tmp_path, url, items, grace="1h", skip=None, retries=1):
    skip_block = ""
    if skip:
        lines = "\n".join(f"  - {item}" for item in skip)
        skip_block = f"skip:\n{lines}\n"
    path = _write(
        tmp_path,
        f"""
state: .notifier/state.json
grace: {grace}
lock_timeout: 2
targets:
  room:
    type: discord
    url: "{url}"
    retries: {retries}
    backoff: 0
    timeout: 5
{skip_block}items:
{items}
""",
    )
    return load_config(path)


NOTE = """
  - id: note
    due: "2026-12-01T15:00:00Z"
    target: room
    message: "Hello {id} @everyone <!channel>"
"""

OTHER = """
  - id: other
    due: "2026-12-01T15:30:00Z"
    target: room
    message: "Other {id}"
"""


def test_posts_once_and_records_state(tmp_path, http_server, capsys):
    config = _config(tmp_path, http_server.url + "/hook", NOTE)
    now = parse_iso("2026-12-01T15:30:00Z")
    assert execute(config, now=now) == 0
    captured = capsys.readouterr()
    assert "posted note room" in captured.out
    assert "summary posted=1 failed=0 overdue=0 skipped=0 dry_run=false" in captured.out
    assert captured.err == ""
    assert len(http_server.httpd.posts) == 1
    body = json.loads(http_server.httpd.posts[0]["body"])
    assert body["content"] == "Hello note @everyone <!channel>"
    assert body["allowed_mentions"] == {"parse": []}
    assert set(body) == {"content", "allowed_mentions"}
    assert http_server.httpd.posts[0]["user_agent"] == "notifier/0.1.0"
    state_text = config.state_path.read_text(encoding="utf-8")
    assert "http://" not in state_text
    assert json.loads(state_text)["sent"]["note"]["via"] == "post"

    assert execute(config, now=now) == 0
    again = capsys.readouterr()
    assert "posted note" not in again.out
    assert "summary posted=0" in again.out
    assert len(http_server.httpd.posts) == 1


def test_dry_run_does_not_post_or_write(tmp_path, http_server, capsys, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("posted")

    monkeypatch.setattr("notifier.http.post_json", boom)
    config = _config(tmp_path, http_server.url + "/hook", NOTE)
    code = execute(config, dry_run=True, now=parse_iso("2026-12-01T15:30:00Z"))
    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith("dry-run\n")
    assert "would post note room" in out
    assert "dry_run=true" in out
    assert not config.state_path.parent.exists()


def test_grace_window_is_inclusive_and_overdue_does_not_post(tmp_path, http_server, capsys):
    config = _config(tmp_path, http_server.url + "/hook", NOTE, grace="1h")
    edge = parse_iso("2026-12-01T16:00:00Z")
    assert execute(config, now=edge) == 0
    assert len(http_server.httpd.posts) == 1
    capsys.readouterr()

    late_config = _config(tmp_path, http_server.url + "/hook", OTHER, grace="1h")
    # Reuse the same state file so `note` stays sent and `other` is new.
    late = execute(late_config, now=parse_iso("2026-12-01T16:30:01Z"))
    assert late == 0
    err = capsys.readouterr()
    assert "overdue other room" in err.out
    assert "summary posted=0 failed=0 overdue=1" in err.out
    assert len(http_server.httpd.posts) == 1


def test_failed_post_is_not_recorded_and_a_later_item_is(tmp_path, http_server, capsys):
    http_server.httpd.fail_remaining = 1
    config = _config(tmp_path, http_server.url + "/hook", NOTE + OTHER, grace="2h")
    code = execute(config, now=parse_iso("2026-12-01T15:45:00Z"))
    assert code == 1
    captured = capsys.readouterr()
    assert "post failed note: HTTP 500" in captured.err
    assert "posted other room" in captured.out
    sent = json.loads(config.state_path.read_text(encoding="utf-8"))["sent"]
    assert "note" not in sent
    assert "other" in sent

    code = execute(config, now=parse_iso("2026-12-01T15:45:00Z"))
    assert code == 0
    bodies = [json.loads(post["body"])["content"] for post in http_server.httpd.posts]
    # The failed attempt is recorded by the test server, then the next run posts it.
    assert bodies.count("Hello note @everyone <!channel>") == 2
    assert bodies.count("Other other") == 1


def test_retry_then_success(tmp_path, http_server):
    http_server.httpd.fail_remaining = 1
    config = _config(tmp_path, http_server.url + "/hook", NOTE, retries=2)
    delays = []
    assert execute(config, now=parse_iso("2026-12-01T15:30:00Z"), sleeper=delays.append) == 0
    assert len(http_server.httpd.posts) == 2
    assert delays == [0.0]
    assert "note" in json.loads(config.state_path.read_text(encoding="utf-8"))["sent"]


def test_redirect_is_not_followed(tmp_path, http_server, capsys):
    http_server.httpd.statuses = [302]
    config = _config(tmp_path, http_server.url + "/hook", NOTE)
    assert execute(config, now=parse_iso("2026-12-01T15:30:00Z")) == 1
    assert "HTTP 302" in capsys.readouterr().err
    assert len(http_server.httpd.posts) == 1
    assert not config.state_path.exists()


def test_skip_list_and_mark_sent(tmp_path, http_server, capsys):
    config = _config(
        tmp_path,
        http_server.url + "/hook",
        NOTE
        + """
  - id: later
    due: "2026-12-02T15:00:00Z"
    target: room
    message: "Later"
""",
        grace="2h",
        skip=["note"],
    )
    now = parse_iso("2026-12-01T15:30:00Z")
    assert execute(config, now=now) == 0
    out = capsys.readouterr().out
    assert "skipped note room" in out
    assert len(http_server.httpd.posts) == 0
    assert mark_sent(config, "later", now=now) == "marked later"
    assert mark_sent(config, "later", now=parse_iso("2026-12-03T00:00:00Z")) == "already sent later"
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["sent"]["later"]["via"] == "mark"
    assert state["sent"]["later"]["at"] == "2026-12-01T15:30:00+00:00"
    assert execute(config, now=parse_iso("2026-12-02T15:30:00Z")) == 0
    assert http_server.httpd.posts == []


def test_list_sections_and_offset(tmp_path, http_server):
    config = _config(
        tmp_path,
        http_server.url + "/hook",
        """
  - id: future
    due: "2026-12-02T09:00:00-05:00"
    target: room
    message: "Future"
  - id: ready
    due: "2026-12-01T15:00:00Z"
    target: room
    message: "Ready"
  - id: old
    due: "2026-12-01T12:00:00Z"
    target: room
    message: "Old"
  - id: held
    due: "2026-12-01T15:00:00Z"
    target: room
    message: "Held"
""",
        grace="1h",
        skip=["held"],
    )
    mark_sent(config, "future", now=parse_iso("2026-11-01T00:00:00Z"))
    text = list_text(config, json.loads(config.state_path.read_text(encoding="utf-8")), parse_iso("2026-12-01T15:30:00Z"))
    assert "upcoming:\n" in text
    assert "  ready  2026-12-01T15:00:00+00:00  room" in text
    assert "  old  2026-12-01T12:00:00+00:00  room" in text
    assert "  future  2026-11-01T00:00:00+00:00  mark" in text
    assert "  held  2026-12-01T15:00:00+00:00  room" in text
    # 09:00-05:00 is 14:00Z, which is before 15:30Z and outside the 1h grace from 14:00.
    assert "overdue:\n  future" not in text
    upcoming, due, overdue, sent, skipped = _sections(text)
    assert upcoming == []
    assert due == ["ready"]
    assert overdue == ["old"]
    assert sent == ["future"]
    assert skipped == ["held"]


def test_save_failure_stops_the_next_item(tmp_path, http_server, monkeypatch, capsys):
    config = _config(tmp_path, http_server.url + "/hook", NOTE + OTHER, grace="2h")

    def fail_replace(src, dst):
        raise OSError("disk")

    monkeypatch.setattr("notifier.state.os.replace", fail_replace)
    assert execute(config, now=parse_iso("2026-12-01T15:45:00Z")) == 1
    captured = capsys.readouterr()
    assert "sent but not recorded" in captured.err
    assert len(http_server.httpd.posts) == 1
    assert not config.state_path.exists()


def test_error_redacts_a_webhook_url(tmp_path, monkeypatch, capsys):
    secret = "https://discord.com/api/webhooks/1/super-secret-token-value"
    config = _config(tmp_path, secret, NOTE)

    def leak(url, payload, headers, timeout):
        raise PostError(f"HTTP 500 {url}")

    monkeypatch.setattr("notifier.http.post_json", leak)
    assert execute(config, now=parse_iso("2026-12-01T15:30:00Z")) == 1
    err = capsys.readouterr().err
    assert "super-secret-token-value" not in err
    assert "discord.com/api/webhooks" not in err


def test_bucket_respects_a_fixed_offset():
    from notifier.schedule import bucket

    class Item:
        def __init__(self):
            self.id = "weekly"
            self.due = parse_iso("2026-12-02T09:00:00-05:00")
            self.grace = timedelta(hours=1)

    item = Item()
    assert bucket(item, parse_iso("2026-12-02T13:59:00Z"), {}, set()) == "upcoming"
    assert bucket(item, parse_iso("2026-12-02T14:00:00Z"), {}, set()) == "due"
    assert bucket(item, parse_iso("2026-12-02T15:00:00Z"), {}, set()) == "due"
    assert bucket(item, parse_iso("2026-12-02T15:00:01Z"), {}, set()) == "overdue"
    assert bucket(item, parse_iso("2026-12-02T14:30:00Z"), {"weekly": {}}, set()) == "sent"
    assert bucket(item, parse_iso("2026-12-02T14:30:00Z"), {}, {"weekly"}) == "skipped"


def test_list_includes_sent_ids_removed_from_the_schedule(tmp_path):
    config = _config(tmp_path, "http://127.0.0.1:9/hook", NOTE)
    state = empty_state()
    state["sent"]["retired"] = {
        "at": "2026-10-01T00:00:00+00:00",
        "via": "mark",
        "target": "room",
    }
    text = list_text(config, state, parse_iso("2026-11-01T00:00:00Z"))
    assert "  retired  2026-10-01T00:00:00+00:00  mark" in text
    assert "  note  2026-12-01T15:00:00+00:00  room" in text


def _sections(text):
    current = None
    groups = {name: [] for name in ("upcoming", "due", "overdue", "sent", "skipped")}
    for line in text.splitlines():
        if line.endswith(":") and not line.startswith(" "):
            current = line[:-1]
            continue
        groups[current].append(line.split()[0])
    return groups["upcoming"], groups["due"], groups["overdue"], groups["sent"], groups["skipped"]

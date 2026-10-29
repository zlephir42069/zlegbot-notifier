"""Atomic state writes and the overlapping-run lock."""

import json
from unittest.mock import patch

import pytest

from notifier.errors import StateError
from notifier.state import StateStore


def test_save_is_atomic_and_round_trips(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.save({"sent": {"note": {"at": "2026-12-01T16:00:00+00:00", "via": "post"}}, "note": "keep"})
    assert not path.with_name(path.name + ".tmp").exists()
    loaded = store.load()
    assert loaded["sent"]["note"]["via"] == "post"
    assert loaded["note"] == "keep"

    with patch("notifier.state.os.replace", side_effect=OSError("disk")):
        with pytest.raises(StateError):
            store.save({"sent": {}})
    assert json.loads(path.read_text(encoding="utf-8"))["sent"]["note"]["via"] == "post"
    assert not path.with_name(path.name + ".tmp").exists()


def test_corrupt_state_is_an_error(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(StateError):
        StateStore(path).load()
    path.write_text('{"sent": {"note": {"at": "yesterday", "via": "post"}}}\n', encoding="utf-8")
    with pytest.raises(StateError):
        StateStore(path).load()


def test_lock_blocks_until_timeout_then_can_be_taken(tmp_path):
    path = tmp_path / "state.json"
    first = StateStore(path)
    second = StateStore(path)
    first.acquire(1)
    with pytest.raises(TimeoutError):
        second.acquire(0.25)
    first.release()
    second.acquire(1)
    second.save({"sent": {}})
    second.release()
    assert StateStore(path).load() == {"sent": {}}

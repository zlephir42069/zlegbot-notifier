"""Atomic JSON state and an exclusive file lock for overlapping cron runs."""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

from notifier.duration import parse_iso
from notifier.errors import StateError
from notifier.names import ID_RE


def empty_state() -> dict:
    return {"sent": {}}


class StateStore:
    """JSON state protected by ``fcntl.flock``.

    The lock is on a sibling ``*.lock`` file so a reader never sees a
    half-written document. Writes go to a temp file in the same directory
    and replace the destination with ``os.replace``.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")
        self._fh = None

    def acquire(self, timeout: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+", encoding="utf-8")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError(
                        f"could not lock {self.lock_path} within {timeout:g}s"
                    )
                time.sleep(0.05)
                continue
            self._fh = handle
            return

    def release(self) -> None:
        handle = self._fh
        self._fh = None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def load(self) -> dict:
        if not self.path.exists():
            return empty_state()
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"could not read state {self.path}: {exc.__class__.__name__}") from exc
        if not isinstance(data, dict):
            raise StateError(f"state file {self.path} must contain a JSON object")
        sent = data.get("sent", {})
        if not isinstance(sent, dict):
            raise StateError(f"state file {self.path} field 'sent' must be an object")
        for key, entry in sent.items():
            _check_entry(self.path, key, entry)
        data["sent"] = sent
        return data

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except OSError as exc:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise StateError(f"could not write state {self.path}: {exc.__class__.__name__}") from exc


def _check_entry(path: Path, key, entry) -> None:
    if not isinstance(key, str) or not ID_RE.fullmatch(key):
        raise StateError(f"state file {path} has an invalid sent id")
    if not isinstance(entry, dict):
        raise StateError(f"state file {path} sent {key} must be an object")
    if entry.get("via") not in {"post", "mark"}:
        raise StateError(f"state file {path} sent {key} via must be post or mark")
    stamp = entry.get("at")
    if not isinstance(stamp, str):
        raise StateError(f"state file {path} sent {key} at must be a timestamp")
    try:
        parse_iso(stamp)
    except ValueError as exc:
        raise StateError(f"state file {path} sent {key} at is not a timestamp") from exc
    if "target" in entry and not isinstance(entry["target"], str):
        raise StateError(f"state file {path} sent {key} target must be a string")

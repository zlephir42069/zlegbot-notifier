"""Post due items, list the schedule, and record ids without posting."""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from notifier.duration import as_utc, format_stamp
from notifier.errors import PostError, StateError
from notifier.post import deliver
from notifier.schedule import bucket, target_label
from notifier.state import StateStore, empty_state
from notifier.template import TemplateError, render


def execute(config, *, dry_run: bool = False, now: datetime | None = None, sleeper=None) -> int:
    """Post every due, unsent, in-window item. Returns 0, or 1 if a post or save failed.

    ``dry_run`` prints the same decisions and does not post, write state, or
    create the state directory. A failed post leaves that id unsent.
    """
    moment = as_utc(now or datetime.now(timezone.utc))
    if sleeper is None:
        import time

        sleeper = time.sleep
    if dry_run:
        try:
            state = _load_if_present(config.state_path)
        except StateError as exc:
            _err(config, f"state error: {exc}")
            return 1
        return _perform(config, state, moment, dry_run=True, store=None, sleeper=sleeper)

    store = StateStore(config.state_path)
    try:
        store.acquire(config.lock_timeout)
    except TimeoutError as exc:
        _err(config, f"state error: {exc}")
        return 1
    try:
        try:
            state = store.load()
        except StateError as exc:
            _err(config, f"state error: {exc}")
            return 1
        return _perform(config, state, moment, dry_run=False, store=store, sleeper=sleeper)
    finally:
        store.release()


def list_text(config, state: dict, now: datetime) -> str:
    """Render upcoming, due, overdue, sent, and skipped sections."""
    moment = as_utc(now)
    sent = state.get("sent", {})
    skip = set(config.skip)
    groups = {name: [] for name in ("upcoming", "due", "overdue", "skipped", "sent")}
    seen = set()
    for item in config.items:
        kind = bucket(item, moment, sent, skip)
        if kind == "sent":
            entry = sent[item.id]
            groups["sent"].append((entry["at"], item.id, entry.get("via", "")))
            seen.add(item.id)
        elif kind == "skipped":
            groups["skipped"].append((_sort_due(item), item.id, item.due_text, target_label(item)))
        else:
            groups[kind].append((_sort_due(item), item.id, item.due_text, target_label(item)))
    for key, entry in sent.items():
        if key not in seen and isinstance(entry, dict):
            groups["sent"].append((str(entry.get("at", "")), key, str(entry.get("via", ""))))

    lines: list[str] = []
    for name in ("upcoming", "due", "overdue"):
        lines.append(f"{name}:")
        for _sort, item_id, due_text, label in sorted(groups[name]):
            lines.append(f"  {item_id}  {due_text}  {label}")
    lines.append("sent:")
    for stamp, item_id, via in sorted(groups["sent"]):
        lines.append(f"  {item_id}  {stamp}  {via}")
    lines.append("skipped:")
    for _sort, item_id, due_text, label in sorted(groups["skipped"]):
        lines.append(f"  {item_id}  {due_text}  {label}")
    return "\n".join(lines) + "\n"


def mark_sent(config, item_id: str, now: datetime | None = None) -> str:
    """Record `item_id` without posting. A second call keeps the original record."""
    from notifier.names import ID_RE
    from notifier.errors import ConfigError

    if not isinstance(item_id, str) or not ID_RE.fullmatch(item_id):
        raise ConfigError(f"unknown id {item_id}")
    match = next((item for item in config.items if item.id == item_id), None)
    if match is None:
        raise ConfigError(f"unknown id {item_id}")
    moment = as_utc(now or datetime.now(timezone.utc))
    store = StateStore(config.state_path)
    try:
        store.acquire(config.lock_timeout)
    except TimeoutError as exc:
        raise StateError(str(exc)) from exc
    try:
        state = store.load()
        if item_id in state["sent"]:
            return f"already sent {item_id}"
        state["sent"][item_id] = {
            "at": format_stamp(moment),
            "target": target_label(match),
            "via": "mark",
        }
        store.save(state)
        return f"marked {item_id}"
    finally:
        store.release()


def _perform(config, state, moment, *, dry_run: bool, store, sleeper) -> int:
    sent = state["sent"]
    skip = set(config.skip)
    counts = {"posted": 0, "failed": 0, "overdue": 0, "skipped": 0}
    if dry_run:
        _out(config, "dry-run")
    for item in config.items:
        kind = bucket(item, moment, sent, skip)
        if kind in {"sent", "upcoming"}:
            continue
        label = target_label(item)
        if kind == "skipped":
            if item.due <= moment <= item.due + item.grace:
                counts["skipped"] += 1
                _out(config, f"skipped {item.id} {label}")
            continue
        if kind == "overdue":
            counts["overdue"] += 1
            _out(config, f"overdue {item.id} {label}")
            continue
        if dry_run:
            counts["posted"] += 1
            _out(config, f"would post {item.id} {label}")
            continue
        try:
            message = render(item.message, item.context())
            deliver(item.target, item, message, redactor=config.redactor, sleeper=sleeper)
        except (PostError, TemplateError) as exc:
            counts["failed"] += 1
            _err(config, f"post failed {item.id}: {exc}")
            continue
        sent[item.id] = {
            "at": format_stamp(moment),
            "target": label,
            "via": "post",
        }
        try:
            store.save(state)
        except StateError as exc:
            counts["failed"] += 1
            sent.pop(item.id, None)
            _err(config, f"state error: {exc}")
            _err(config, f"post failed {item.id}: sent but not recorded")
            break
        counts["posted"] += 1
        _out(config, f"posted {item.id} {label}")
    _out(
        config,
        "summary posted={posted} failed={failed} overdue={overdue} skipped={skipped} dry_run={dry}".format(
            posted=counts["posted"],
            failed=counts["failed"],
            overdue=counts["overdue"],
            skipped=counts["skipped"],
            dry="true" if dry_run else "false",
        ),
    )
    return 1 if counts["failed"] else 0


def _load_if_present(path) -> dict:
    if not path.exists():
        return empty_state()
    return StateStore(path).load()


def _sort_due(item) -> str:
    return as_utc(item.due).isoformat()


def _out(config, line: str) -> None:
    print(config.redactor.text(line), flush=True)


def _err(config, line: str) -> None:
    print(config.redactor.text(line), file=sys.stderr, flush=True)

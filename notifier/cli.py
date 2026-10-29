"""Command line: run, validate, list, mark-sent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from notifier.duration import parse_iso
from notifier.errors import ConfigError, StateError
from notifier.redact import scrub


def main(argv=None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code)

    try:
        if args.command == "validate":
            return _validate(args)
        if args.command == "run":
            return _run(args)
        if args.command == "list":
            return _list(args)
        if args.command == "mark-sent":
            return _mark(args)
    except ConfigError as exc:
        for message in exc.messages:
            print(f"config error: {scrub(message)}", file=sys.stderr, flush=True)
        return 2
    except StateError as exc:
        print(f"state error: {scrub(str(exc))}", file=sys.stderr, flush=True)
        return 1
    except BrokenPipeError:
        return 0
    print("config error: unknown command", file=sys.stderr, flush=True)
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notifier",
        description="Post scheduled announcements. No model call.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="post every due, unsent item still inside its grace window")
    _add_config(run_parser)
    _add_now(run_parser)
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be posted; do not post or write state",
    )

    validate_parser = sub.add_parser("validate", help="validate the schedule and exit")
    _add_config(validate_parser)

    list_parser = sub.add_parser("list", help="show upcoming, due, overdue, sent, and skipped items")
    _add_config(list_parser)
    _add_now(list_parser)

    mark_parser = sub.add_parser("mark-sent", help="record an id as sent without posting")
    mark_parser.add_argument("id", help="item id to record")
    _add_config(mark_parser)
    _add_now(mark_parser)
    return parser


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default="notifier.yaml",
        help="path to the schedule file (default: notifier.yaml)",
    )


def _add_now(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--now",
        help="ISO-8601 timestamp used as the clock, with a timezone offset",
    )


def _validate(args) -> int:
    from notifier.config import load_config

    load_config(Path(args.config), resolve_secrets=True)
    print("ok", flush=True)
    return 0


def _run(args) -> int:
    from notifier.config import load_config
    from notifier.run import execute

    config = load_config(Path(args.config), resolve_secrets=True)
    return execute(config, dry_run=args.dry_run, now=_clock(args.now))


def _list(args) -> int:
    from notifier.config import load_config
    from notifier.run import _load_if_present, list_text

    config = load_config(Path(args.config), resolve_secrets=False)
    now = _clock(args.now)
    if now is None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
    state = _load_if_present(config.state_path)
    print(list_text(config, state, now), end="")
    return 0


def _mark(args) -> int:
    from notifier.config import load_config
    from notifier.run import mark_sent

    config = load_config(Path(args.config), resolve_secrets=False)
    print(mark_sent(config, args.id, now=_clock(args.now)), flush=True)
    return 0


def _clock(value):
    if value is None:
        return None
    try:
        return parse_iso(value)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

"""Load and validate a notifier schedule. Secrets stay in the environment."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import yaml

from notifier.duration import format_stamp, parse_duration, parse_iso
from notifier.errors import ConfigError
from notifier.mentions import PARSE_ORDER, Mentions
from notifier.names import ID_RE
from notifier.post import LIMITS, outbound_text
from notifier.redact import Redactor, scrub
from notifier.template import TemplateError, iter_fields, render

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SENSITIVE_NAME = re.compile(
    r"(URL|URI|TOKEN|SECRET|PASSWORD|PASSWD|AUTH|WEBHOOK|HOOK)",
    re.IGNORECASE,
)
_DIGITS = re.compile(r"^[0-9]{1,32}$")

_TOP = {"state", "grace", "lock_timeout", "targets", "items", "skip"}
_ITEM = {"id", "due", "message", "target", "grace", "values", "mentions"}
_TARGET_COMMON = {"type", "retries", "backoff", "timeout"}
_TARGET_FIELDS = {
    "discord": _TARGET_COMMON | {"url"},
    "discord_bot": _TARGET_COMMON | {"token", "channel"},
    "slack": _TARGET_COMMON | {"url"},
    "webhook": _TARGET_COMMON | {"url", "headers"},
}

_MAX_TIMEOUT = 86400.0
_MAX_RETRIES = 20
_MAX_GRACE = 3650 * 86400.0
_DEFAULT_GRACE = "24h"


@dataclass
class Target:
    name: str
    type: str
    url: str = ""
    token: str = ""
    channel: str = ""
    headers: dict = field(default_factory=dict)
    retries: int = 3
    backoff: float = 0.5
    timeout: float = 30.0


@dataclass
class Item:
    id: str
    due: datetime
    due_text: str
    message: str
    values: dict
    target: Target
    grace: timedelta
    mentions: Mentions

    def context(self) -> dict:
        data = dict(self.values)
        data["id"] = self.id
        data["due"] = self.due_text
        return data


@dataclass
class AppConfig:
    path: Path
    state_path: Path
    grace: timedelta
    lock_timeout: float
    targets: dict
    items: list
    skip: list
    redactor: Redactor


def load_config(path: Path, *, resolve_secrets: bool = True) -> AppConfig:
    """Load a YAML or JSON schedule.

    `resolve_secrets` is false for `list` and `mark-sent`, which do not post.
    Target URLs and tokens may then be unset. `validate` and `run` resolve them.
    """
    path = Path(path)
    data = _read_document(path)
    if not isinstance(data, dict):
        raise ConfigError("config must be a mapping")

    errors: list[str] = []
    redactor = Redactor()
    base = path.parent.resolve()

    for key in sorted(set(data) - _TOP):
        errors.append(f"unknown setting {key}")

    state_raw = data.get("state", ".notifier/state.json")
    state_expanded = _expand(
        state_raw,
        sensitive=False,
        required=True,
        redactor=redactor,
        errors=errors,
        label="state",
    )
    state_path = _resolve(base, state_expanded) if state_expanded else base / ".notifier" / "state.json"

    grace = _span(data.get("grace", _DEFAULT_GRACE), "grace", errors)
    lock_timeout = _seconds(
        data.get("lock_timeout", 60),
        "lock_timeout",
        errors,
        allow_zero=True,
    )
    targets, defined_targets = _parse_targets(
        data.get("targets", {}),
        errors,
        redactor,
        resolve_secrets,
    )
    items_raw = data.get("items", None)
    items = _parse_items(
        items_raw,
        errors,
        redactor,
        targets,
        defined_targets,
        grace if grace is not None else timedelta(hours=24),
        resolve_secrets,
    )
    skip = _parse_skip(data.get("skip", []), _declared_ids(items_raw), errors)

    if errors:
        raise ConfigError([redactor.text(message) for message in errors])
    if grace is None or lock_timeout is None:
        raise ConfigError("config is invalid")
    return AppConfig(
        path=path.resolve(),
        state_path=state_path,
        grace=grace,
        lock_timeout=lock_timeout,
        targets=targets,
        items=items,
        skip=skip,
        redactor=redactor,
    )


def _read_document(path: Path):
    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read config: {exc.__class__.__name__}") from exc
    if path.suffix.lower() == ".json":
        import json

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(scrub(f"could not parse JSON: {exc}")) from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(scrub(f"could not parse YAML: {exc}")) from exc


def _parse_targets(raw, errors, redactor, resolve_secrets: bool) -> tuple[dict, set]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or isinstance(raw, list):
        errors.append("targets must be a mapping of name to target")
        return {}, set()
    parsed = {}
    defined = set()
    for name, body in raw.items():
        label = f"target {name}"
        if not isinstance(name, str) or not ID_RE.fullmatch(name):
            errors.append(f"{label}: name must be a short letter-or-number identifier")
            continue
        defined.add(name)
        target = _parse_target(body, name, label, errors, redactor, resolve_secrets)
        if target is not None:
            parsed[name] = target
    return parsed, defined


def _parse_target(body, name: str, label: str, errors, redactor, resolve_secrets: bool):
    before = len(errors)
    if not isinstance(body, dict):
        errors.append(f"{label} must be a mapping")
        return None
    target_type = body.get("type")
    if target_type not in _TARGET_FIELDS:
        errors.append(f"{label}: type must be discord, discord_bot, slack, or webhook")
        return None
    for key in sorted(set(body) - _TARGET_FIELDS[target_type]):
        errors.append(f"{label}: unknown setting {key}")
    retries = _int(body.get("retries", 3), f"{label}.retries", errors, minimum=1, maximum=_MAX_RETRIES)
    backoff = _seconds(body.get("backoff", 0.5), f"{label}.backoff", errors, allow_zero=True)
    timeout = _seconds(body.get("timeout", 30), f"{label}.timeout", errors, allow_zero=False)
    url = ""
    token = ""
    channel = ""
    headers: dict = {}
    if target_type == "discord_bot":
        token = _required_text(body, "token", label, errors, redactor, resolve_secrets, sensitive=True)
        channel = _required_text(
            body, "channel", label, errors, redactor, resolve_secrets, sensitive=False
        )
        if channel and not _unresolved(channel):
            if not _DIGITS.fullmatch(channel):
                errors.append(f"{label}.channel must be a numeric channel id")
        if token and not _unresolved(token) and any(char.isspace() for char in token):
            errors.append(f"{label}.token must be a non-empty token")
    else:
        url = _required_text(body, "url", label, errors, redactor, resolve_secrets, sensitive=True)
        if url and not _unresolved(url):
            _require_http(url, f"{label}.url", errors)
        if target_type == "webhook" and "headers" in body:
            headers = _headers(body.get("headers"), f"{label}.headers", errors, redactor, resolve_secrets)
    if len(errors) > before or retries is None or backoff is None or timeout is None:
        return None
    return Target(
        name=name,
        type=target_type,
        url=url,
        token=token,
        channel=channel,
        headers=headers,
        retries=retries,
        backoff=backoff,
        timeout=timeout,
    )


def _declared_ids(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    found = []
    for body in raw:
        if not isinstance(body, dict):
            continue
        item_id = body.get("id")
        if isinstance(item_id, str) and ID_RE.fullmatch(item_id) and item_id not in found:
            found.append(item_id)
    return found


def _parse_items(raw, errors, redactor, targets, defined_targets, default_grace, resolve_secrets: bool) -> list:
    if raw is None:
        errors.append("items is required")
        return []
    if not isinstance(raw, list):
        errors.append("items must be a list")
        return []
    parsed = []
    seen = set()
    for index, body in enumerate(raw):
        label = f"items[{index}]"
        if not isinstance(body, dict):
            errors.append(f"{label} must be a mapping")
            continue
        item_id = body.get("id")
        if not isinstance(item_id, str) or not ID_RE.fullmatch(item_id):
            errors.append(f"{label}.id must be a short letter-or-number identifier")
            item_label = label
        else:
            item_label = f"item {item_id}"
            if item_id in seen:
                errors.append(f"duplicate item id {item_id}")
            seen.add(item_id)
        for key in sorted(set(body) - _ITEM):
            errors.append(f"{item_label}: unknown setting {key}")
        due = _due(body.get("due", None), f"{item_label}.due", errors) if "due" in body else None
        if "due" not in body:
            errors.append(f"{item_label}.due is required")
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            errors.append(f"{item_label}.message must be a non-empty string")
            message = None
        elif "\x00" in message:
            errors.append(f"{item_label}.message must not contain a NUL")
            message = None
        values = _values(body.get("values", {}), f"{item_label}.values", errors, redactor, resolve_secrets)
        mentions = _mentions(body["mentions"], f"{item_label}.mentions", errors) if "mentions" in body else Mentions()
        grace = default_grace
        if "grace" in body:
            parsed_grace = _span(body.get("grace"), f"{item_label}.grace", errors)
            if parsed_grace is not None:
                grace = parsed_grace
        target = _item_target(
            body.get("target", None),
            item_label,
            errors,
            redactor,
            targets,
            defined_targets,
            resolve_secrets,
        )
        if (
            message is None
            or due is None
            or values is None
            or mentions is None
            or target is None
            or not isinstance(item_id, str)
            or not ID_RE.fullmatch(item_id)
        ):
            continue
        item = Item(
            id=item_id,
            due=due[0],
            due_text=due[1],
            message=message,
            values=values,
            target=target,
            grace=grace,
            mentions=mentions,
        )
        before = len(errors)
        _check_template(item, item_label, errors)
        if len(errors) == before:
            parsed.append(item)
    return parsed


def _item_target(raw, label, errors, redactor, targets, defined_targets, resolve_secrets: bool):
    if isinstance(raw, str):
        if raw in targets:
            return targets[raw]
        if raw not in defined_targets:
            errors.append(f"{label}: unknown target {raw}")
        return None
    if isinstance(raw, dict):
        return _parse_target(raw, "", f"{label}.target", errors, redactor, resolve_secrets)
    errors.append(f"{label}.target must be a target name or mapping")
    return None


def _check_template(item: Item, label: str, errors) -> None:
    allowed = set(item.values) | {"id", "due"}
    try:
        fields = list(iter_fields(item.message))
    except TemplateError as exc:
        errors.append(f"{label}.message: {exc}")
        return
    missing = [name for name, _spec in fields if name not in allowed]
    for name in missing:
        errors.append(f"{label}.message missing placeholder {{{name}}}")
    if missing:
        return
    try:
        rendered = render(item.message, item.context())
    except TemplateError as exc:
        errors.append(f"{label}.message: {exc}")
        return
    if not rendered.strip():
        errors.append(f"{label}.message renders to an empty string")
        return
    if "\x00" in rendered:
        errors.append(f"{label}.message must not contain a NUL")
        return
    text = outbound_text(item.target.type, rendered, item.mentions)
    limit = LIMITS.get(item.target.type)
    if limit is not None and len(text) > limit:
        errors.append(f"{label}.message exceeds {limit} characters")


def _values(raw, label, errors, redactor, resolve_secrets: bool):
    if raw is None:
        return {}
    if not isinstance(raw, dict) or isinstance(raw, list):
        errors.append(f"{label} must be a mapping")
        return None
    parsed = {}
    failed = False
    for key, value in raw.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            errors.append(f"{label}.{key} is not a placeholder name")
            failed = True
            continue
        if key in {"id", "due"}:
            errors.append(f"{label} cannot set {key}")
            failed = True
            continue
        scalar = _scalar(value, f"{label}.{key}", errors, redactor, resolve_secrets)
        if scalar is None:
            failed = True
            continue
        parsed[key] = scalar
    if failed:
        return None
    return parsed


def _scalar(value, label, errors, redactor, resolve_secrets: bool):
    if isinstance(value, str):
        return _expand(
            value,
            sensitive=False,
            required=resolve_secrets,
            redactor=redactor,
            errors=errors,
            label=label,
        )
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            errors.append(f"{label} must be a finite number")
            return None
        return value
    errors.append(f"{label} must be a string, number, or boolean")
    return None


def _mentions(raw, label, errors):
    if raw is False or raw is None:
        return Mentions()
    if raw is True:
        return Mentions(parse=PARSE_ORDER)
    if not isinstance(raw, dict):
        errors.append(f"{label} must be true, false, or a mapping")
        return None
    unknown = sorted(set(raw) - {"parse", "users", "roles"})
    if unknown:
        errors.append(f"{label}: unknown setting {unknown[0]}")
        return None
    parse: list[str] = []
    if "parse" in raw:
        values = raw.get("parse")
        if not isinstance(values, list):
            errors.append(f"{label}.parse must be a list")
            return None
        for index, item in enumerate(values):
            if item not in PARSE_ORDER:
                errors.append(f"{label}.parse[{index}] must be users, roles, or everyone")
                return None
            if item not in parse:
                parse.append(item)
    users = _id_list(raw.get("users"), f"{label}.users", errors) if "users" in raw else []
    roles = _id_list(raw.get("roles"), f"{label}.roles", errors) if "roles" in raw else []
    if users is None or roles is None:
        return None
    ordered = tuple(name for name in PARSE_ORDER if name in parse)
    return Mentions(parse=ordered, users=tuple(users), roles=tuple(roles))


def _id_list(raw, label, errors):
    if not isinstance(raw, list):
        errors.append(f"{label} must be a list of quoted ids")
        return None
    parsed = []
    for index, item in enumerate(raw):
        if isinstance(item, bool) or isinstance(item, int) or not isinstance(item, str):
            errors.append(f"{label}[{index}] must be a quoted id string")
            return None
        if not _DIGITS.fullmatch(item):
            errors.append(f"{label}[{index}] must be a numeric id")
            return None
        if item not in parsed:
            parsed.append(item)
    return parsed


def _parse_skip(raw, item_ids: list[str], errors) -> list:
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append("skip must be a list of ids")
        return []
    known = set(item_ids)
    parsed = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not ID_RE.fullmatch(item):
            errors.append(f"skip[{index}] must be an item id")
            continue
        if item in parsed:
            errors.append(f"duplicate skip id {item}")
            continue
        if item not in known:
            errors.append(f"skip id {item} is not an item")
            continue
        parsed.append(item)
    return parsed


def _due(value, label, errors):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            errors.append(f"{label} needs a timezone offset")
            return None
        return value, format_stamp(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = parse_iso(value.strip())
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            return None
        return parsed, format_stamp(parsed)
    errors.append(f"{label} must be a quoted ISO-8601 timestamp with a timezone offset")
    return None


def _headers(raw, label, errors, redactor, resolve_secrets: bool) -> dict:
    if not isinstance(raw, dict):
        errors.append(f"{label} must be a mapping")
        return {}
    parsed = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip() or any(char in key for char in "\r\n:"):
            errors.append(f"{label} keys must be header names")
            continue
        expanded = _expand(
            value,
            sensitive=True,
            required=resolve_secrets,
            redactor=redactor,
            errors=errors,
            label=f"{label}.{key}",
        )
        if not isinstance(value, str):
            continue
        if any(char in expanded for char in "\r\n"):
            errors.append(f"{label}.{key} must not contain a newline")
            continue
        parsed[key] = expanded
    return parsed


def _required_text(body, key, label, errors, redactor, resolve_secrets: bool, *, sensitive: bool) -> str:
    if key not in body:
        errors.append(f"{label}.{key} is required")
        return ""
    raw = body.get(key)
    expanded = _expand(
        raw,
        sensitive=sensitive,
        required=resolve_secrets,
        redactor=redactor,
        errors=errors,
        label=f"{label}.{key}",
    )
    if not isinstance(raw, str):
        return ""
    cleaned = expanded.strip()
    if sensitive and cleaned and not _unresolved(cleaned):
        redactor.add(cleaned)
    if not raw.strip():
        errors.append(f"{label}.{key} must not be empty")
    elif raw.strip() and not cleaned and not _unresolved(expanded):
        errors.append(f"{label}.{key} must not be empty")
    return cleaned


def _expand(value, *, sensitive: bool, required: bool, redactor: Redactor, errors: list[str], label: str) -> str:
    if not isinstance(value, str):
        errors.append(f"{label} must be a string")
        return ""
    missing = []
    # `$${` is an escaped `${`, so a value can keep a literal ${NAME}.
    sentinel = "\x00NOTIFIER_DOLLAR\x00"
    protected = value.replace("$${", sentinel)

    def repl(match):
        name = match.group(1)
        if name not in os.environ:
            missing.append(name)
            return match.group(0)
        env_value = os.environ[name]
        if sensitive or _SENSITIVE_NAME.search(name):
            redactor.add(env_value)
        return env_value

    expanded = _ENV.sub(repl, protected).replace(sentinel, "${")
    if required:
        for name in sorted(set(missing)):
            errors.append(f"{label} references unset environment variable {name}")
    if sensitive and expanded and not _unresolved(expanded):
        redactor.add(expanded)
    if any(char in expanded for char in "\r\n\x00"):
        errors.append(f"{label} must not contain a newline")
    return expanded


def _require_http(url: str, label: str, errors: list[str]) -> bool:
    if any(char.isspace() for char in url):
        errors.append(f"{label} must be an http(s) URL")
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        errors.append(f"{label} must be an http(s) URL")
        return False
    return True


def _unresolved(value: str) -> bool:
    return "${" in value


def _span(value, label, errors):
    if isinstance(value, bool) or value is None:
        errors.append(f"{label} must be seconds or a duration")
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        try:
            seconds = parse_duration(value).total_seconds()
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            return None
    else:
        errors.append(f"{label} must be seconds or a duration")
        return None
    if seconds < 0 or seconds > _MAX_GRACE:
        errors.append(f"{label} must be between 0 and {_MAX_GRACE:g} seconds")
        return None
    return timedelta(seconds=seconds)


def _seconds(value, label, errors, *, allow_zero: bool):
    if isinstance(value, bool):
        errors.append(f"{label} must be seconds or a duration")
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        try:
            seconds = parse_duration(value).total_seconds()
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            return None
    else:
        errors.append(f"{label} must be seconds or a duration")
        return None
    if seconds < 0 or seconds > _MAX_TIMEOUT or (not allow_zero and seconds <= 0):
        if allow_zero:
            errors.append(f"{label} must be between 0 and {_MAX_TIMEOUT:g} seconds")
        else:
            errors.append(f"{label} must be greater than 0 and at most {_MAX_TIMEOUT:g} seconds")
        return None
    return seconds


def _int(value, label, errors, *, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label} must be an integer")
        return None
    if value < minimum or value > maximum:
        errors.append(f"{label} must be between {minimum} and {maximum}")
        return None
    return value


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path

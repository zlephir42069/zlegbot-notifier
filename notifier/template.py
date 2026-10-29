"""{placeholder} templates. A subset of str.format with no attribute access."""

from __future__ import annotations

import re
import string

_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TemplateError(ValueError):
    """The template is not a simple placeholder string."""


def iter_fields(template: str):
    """Yield `(name, format_spec)` for each placeholder."""
    for _literal, field, spec, conversion in _parse(template):
        if field is None:
            continue
        if conversion is not None:
            raise TemplateError(f"conversion is not allowed in {{{field}}}")
        if not _FIELD.fullmatch(field):
            raise TemplateError(f"placeholder {{{field}}} is not a simple name")
        yield field, spec or ""


def render(template: str, values: dict) -> str:
    """Substitute `{name}` from `values`. `{{` and `}}` are literal braces.

    `{name.attr}`, `{name[0]}`, and `{name!r}` are rejected. A format spec
    such as `{count:d}` is passed to the built-in `format`.
    """
    chunks: list[str] = []
    for literal, field, spec, conversion in _parse(template):
        chunks.append(literal)
        if field is None:
            continue
        if conversion is not None:
            raise TemplateError(f"conversion is not allowed in {{{field}}}")
        if not _FIELD.fullmatch(field):
            raise TemplateError(f"placeholder {{{field}}} is not a simple name")
        if field not in values:
            raise TemplateError(f"missing placeholder {{{field}}}")
        try:
            chunks.append(format(values[field], spec or ""))
        except (ValueError, TypeError) as exc:
            raise TemplateError(f"cannot format {{{field}}}") from exc
    return "".join(chunks)


def _parse(template: str):
    try:
        return list(string.Formatter().parse(template))
    except ValueError as exc:
        raise TemplateError(f"invalid template: {exc}") from exc

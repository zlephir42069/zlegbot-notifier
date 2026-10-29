"""Placeholder substitution rejects attribute access."""

import pytest

from notifier.template import TemplateError, render


def test_renders_names_and_literal_braces():
    text = render("Hello {name}. Id is {id}. Braces {{stay}}.", {"name": "Ada", "id": "n1"})
    assert text == "Hello Ada. Id is n1. Braces {stay}."


def test_format_spec_uses_the_value_type():
    assert render("{count:02d}", {"count": 3}) == "03"


def test_inserted_values_are_not_parsed_again():
    assert render("{name}", {"name": "{not a placeholder}"}) == "{not a placeholder}"


def test_missing_and_unsafe_placeholders():
    with pytest.raises(TemplateError, match="missing placeholder"):
        render("Hi {name}", {})
    with pytest.raises(TemplateError, match="not a simple name"):
        render("{name.__class__}", {"name": "x"})
    with pytest.raises(TemplateError, match="not a simple name"):
        render("{name[0]}", {"name": "x"})
    with pytest.raises(TemplateError, match="conversion"):
        render("{name!r}", {"name": "x"})
    with pytest.raises(TemplateError, match="invalid template"):
        render("open {", {})


def test_bad_format_spec_does_not_include_the_value():
    with pytest.raises(TemplateError, match="cannot format") as caught:
        render("{count:d}", {"count": "nope"})
    assert "nope" not in str(caught.value)

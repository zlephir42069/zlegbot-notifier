"""Secrets and webhook URLs are removed from text we show."""

from notifier.redact import Redactor, scrub


def test_replaces_registered_secrets_and_webhook_urls():
    redactor = Redactor()
    redactor.add("SUPERSECRETTOKEN")
    redactor.add("https://example.com/hook/SUPERSECRETTOKEN")
    text = redactor.text(
        "failed https://example.com/hook/SUPERSECRETTOKEN and token SUPERSECRETTOKEN"
    )
    assert "SUPERSECRETTOKEN" not in text
    assert "***" in text
    assert redactor.text("webhook is a word") == "webhook is a word"


def test_scrubs_webhook_urls_without_registration():
    raw = "see https://discord.com/api/webhooks/123/abcdefghij now"
    cleaned = scrub(raw)
    assert "abcdefghij" not in cleaned
    assert "discord.com/api/webhooks" not in cleaned
    slack = scrub("post https://hooks.slack.com/services/T000/B000/secretvalue")
    assert "secretvalue" not in slack


def test_short_values_are_not_registered():
    redactor = Redactor()
    redactor.add("short")
    assert redactor.text("short") == "short"

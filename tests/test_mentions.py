"""Default policy pings nobody. Opt-in is per item."""

from notifier.mentions import Mentions, apply_slack, discord_allowed_mentions, webhook_mentions


def test_discord_default_parse_is_empty():
    assert discord_allowed_mentions(Mentions()) == {"parse": []}


def test_explicit_user_list_is_not_also_in_parse():
    policy = Mentions(parse=("users", "roles"), users=("123",))
    assert discord_allowed_mentions(policy) == {"parse": ["roles"], "users": ["123"]}


def test_everyone_opt_in():
    policy = Mentions(parse=("users", "roles", "everyone"))
    assert discord_allowed_mentions(policy)["parse"] == ["users", "roles", "everyone"]
    assert webhook_mentions(policy)["enabled"] is True
    assert webhook_mentions(Mentions()) == {
        "enabled": False,
        "parse": [],
        "users": [],
        "roles": [],
    }


def test_slack_mass_mentions_are_neutralized_unless_everyone_is_enabled():
    raw = "Hello <!channel> and <!here|folks> plus <!subteam^S123|dev> and <@U123>"
    quiet = apply_slack(raw, Mentions())
    assert "<!" not in quiet
    assert "channel" in quiet
    assert "here" in quiet
    assert "group" in quiet
    assert "<@U123>" in quiet
    assert apply_slack(raw, Mentions(parse=("everyone",))) == raw
    assert apply_slack("See <!date^123^{date}>", Mentions()) == "See <!date^123^{date}>"

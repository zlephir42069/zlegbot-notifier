"""Mention policy. The default posts text and pings nobody."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Discord allowed_mentions.parse. "everyone" covers @everyone and @here.
PARSE_ORDER = ("users", "roles", "everyone")

# Slack mass-mention tags. Individual `<@U...>` mentions are left as written.
_SLACK_MASS = re.compile(r"<!(everyone|channel|here)(?:\|[^>]*)?>", re.IGNORECASE)
_SLACK_SUBTEAM = re.compile(r"<!subteam\^[^>|]+(?:\|[^>]*)?>", re.IGNORECASE)


@dataclass(frozen=True)
class Mentions:
    parse: tuple[str, ...] = ()
    users: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()

    @property
    def mass(self) -> bool:
        """True when @everyone / @here / Slack channel pings are allowed."""
        return "everyone" in self.parse

    @property
    def enabled(self) -> bool:
        return bool(self.parse or self.users or self.roles)


def discord_allowed_mentions(policy: Mentions) -> dict:
    """Build Discord `allowed_mentions`. An empty parse list is always sent.

    Discord rejects a type that appears both in `parse` and as an id list, so
    an explicit `users` or `roles` list drops that type from `parse`.
    """
    parse = [name for name in PARSE_ORDER if name in policy.parse]
    payload: dict = {}
    if policy.users:
        payload["users"] = list(policy.users)
        parse = [name for name in parse if name != "users"]
    if policy.roles:
        payload["roles"] = list(policy.roles)
        parse = [name for name in parse if name != "roles"]
    payload["parse"] = parse
    return payload


def apply_slack(text: str, policy: Mentions) -> str:
    """Replace Slack mass-mention tags unless `everyone` is enabled.

    `<!everyone>`, `<!channel>`, `<!here>`, and `<!subteam^...>` become the
    words `everyone`, `channel`, `here`, and `group`. Other text is unchanged.
    """
    if policy.mass:
        return text
    neutralized = _SLACK_MASS.sub(lambda match: match.group(1).lower(), text)
    return _SLACK_SUBTEAM.sub("group", neutralized)


def webhook_mentions(policy: Mentions) -> dict:
    """Describe the policy for a generic receiver. The receiver has to enforce it."""
    return {
        "enabled": policy.enabled,
        "parse": [name for name in PARSE_ORDER if name in policy.parse],
        "users": list(policy.users),
        "roles": list(policy.roles),
    }

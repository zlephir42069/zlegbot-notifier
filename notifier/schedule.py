"""Classify one item against the clock, the sent set, and the skip list."""

from __future__ import annotations


def target_label(item) -> str:
    """Stable name for logs. Named targets use their name; inline targets use the type."""
    return item.target.name or item.target.type


def bucket(item, now, sent, skip) -> str:
    """Return `sent`, `skipped`, `upcoming`, `overdue`, or `due`.

    The postable window is inclusive: ``due <= now <= due + grace``.
    An id already in ``sent`` stays sent. A skip-list id is skipped even
    when the clock would otherwise call it due or overdue.
    """
    if item.id in sent:
        return "sent"
    if item.id in skip:
        return "skipped"
    if now < item.due:
        return "upcoming"
    if now > item.due + item.grace:
        return "overdue"
    return "due"

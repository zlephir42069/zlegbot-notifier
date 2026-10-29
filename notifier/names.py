"""Shared identifier rule for item ids and target names."""

import re

# Short, filesystem-safe, and unambiguous in log lines (no spaces).
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

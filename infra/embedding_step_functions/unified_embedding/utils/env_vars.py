"""Environment helpers used across embedding handlers."""

from __future__ import annotations

import os


def get_required_env(var_name: str) -> str:
    """Return the named environment variable or raise if missing."""
    value = os.environ.get(var_name)
    if not value:
        raise ValueError(f"{var_name} environment variable not set")
    return value

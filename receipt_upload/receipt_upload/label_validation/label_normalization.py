"""Helpers for keeping receipt word labels inside CORE_LABELS.

The vocabulary itself now lives in :mod:`receipt_dynamo.constants`, next to
``CORE_LABELS`` and to the ``add_receipt_word_label`` guard that enforces it,
so every writer -- including the two MCP servers, which do not import
``receipt_upload`` -- shares one definition.  This module stays as the
import path several call sites already use.
"""

from __future__ import annotations

from receipt_dynamo.constants import (
    CORE_LABEL_NAMES,
    NON_CORE_LABEL_ALIASES,
    canonical_label_name,
    invalid_label_message,
    is_core_label,
    normalize_core_label,
    normalize_label_alias,
)

__all__ = [
    "CORE_LABEL_NAMES",
    "NON_CORE_LABEL_ALIASES",
    "canonical_label_name",
    "invalid_label_message",
    "is_core_label",
    "normalize_core_label",
    "normalize_label_alias",
]

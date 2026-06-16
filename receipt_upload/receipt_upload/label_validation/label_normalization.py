"""Helpers for keeping receipt word labels inside CORE_LABELS."""

from __future__ import annotations

from typing import Any

from receipt_dynamo.constants import CORE_LABELS


NON_CORE_LABEL_ALIASES: dict[str, str] = {
    "ADDRESS": "ADDRESS_LINE",
    "BUSINESS_NAME": "MERCHANT_NAME",
    "CARD_NUMBER": "PAYMENT_METHOD",
    "PAYMENT_TYPE": "PAYMENT_METHOD",
}


def canonical_label_name(label: Any) -> str:
    """Normalize a model or stored label into the Dynamo label format."""
    if label is None:
        return ""
    return str(label).strip().upper()


def is_core_label(label: Any) -> bool:
    """Return whether a label is part of the canonical receipt label set."""
    return canonical_label_name(label) in CORE_LABELS


def normalize_label_alias(label: Any) -> str | None:
    """Map known non-core aliases to CORE_LABELS, if the mapping is safe."""
    canonical = canonical_label_name(label)
    if canonical in CORE_LABELS:
        return canonical
    return NON_CORE_LABEL_ALIASES.get(canonical)

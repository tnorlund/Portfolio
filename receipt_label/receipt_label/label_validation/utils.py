"""Helper utilities for label validation modules."""

import re

from receipt_dynamo.entities import ReceiptWordLabel  # type: ignore


def chroma_id_from_label(label: ReceiptWordLabel) -> str:
    """Return a deterministic ChromaDB ID for the provided label."""

    return (
        f"IMAGE#{label.image_id}#"
        f"RECEIPT#{label.receipt_id:05d}#"
        f"LINE#{label.line_id:05d}#"
        f"WORD#{label.word_id:05d}"
    )


def normalize_text(text: str) -> str:
    """Normalize text for fuzzy matching."""

    return re.sub(r"[^\w\s\-]", "", text.lower().strip())

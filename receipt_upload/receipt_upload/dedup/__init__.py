"""Receipt duplicate detection (Tier 0 exact + Tier 1 content signature)."""

from receipt_upload.dedup.detector import (
    DupGroup,
    detect_duplicates,
    find_exact_duplicates,
    find_signature_candidates,
)

__all__ = [
    "DupGroup",
    "detect_duplicates",
    "find_exact_duplicates",
    "find_signature_candidates",
]

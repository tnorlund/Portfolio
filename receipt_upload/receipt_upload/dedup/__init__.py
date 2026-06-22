"""Receipt duplicate detection + merge-context (dossier) building."""

from receipt_upload.dedup.context import (
    LabelObs,
    MemberContext,
    MergeDossier,
    build_merge_dossiers,
)
from receipt_upload.dedup.detector import DupGroup, find_exact_duplicates

__all__ = [
    "DupGroup",
    "find_exact_duplicates",
    "LabelObs",
    "MemberContext",
    "MergeDossier",
    "build_merge_dossiers",
]

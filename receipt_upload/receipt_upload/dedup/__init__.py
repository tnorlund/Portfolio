"""Receipt duplicate detection + merge-context (dossier) building."""

from receipt_upload.dedup.context import (
    Conflict,
    LabelObs,
    MemberContext,
    MergeDossier,
    build_merge_dossiers,
)
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
    "Conflict",
    "LabelObs",
    "MemberContext",
    "MergeDossier",
    "build_merge_dossiers",
]

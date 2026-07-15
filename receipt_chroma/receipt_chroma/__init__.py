"""ChromaDB utility package for receipt vector storage."""

from typing import TYPE_CHECKING, Any

__version__ = "0.2.0"

if TYPE_CHECKING:
    from receipt_chroma.data.chroma_client import ChromaClient
    from receipt_chroma.glyph_matching import (
        clean_letter_mask,
        normalize_glyph,
        shifted_iou,
        shifted_iou_stack,
    )
    from receipt_chroma.lock_manager import LockManager
    from receipt_chroma.merchant_fingerprint import (
        TypefaceFingerprint,
        TypefaceSourceScore,
        compute_atlas_registry_sha256,
        compute_typeface_registry_sha256,
        load_registry_atlases,
        match_typeface,
        score_typeface_sources,
        validate_typeface_registry,
    )

__all__ = [
    "__version__",
    "ChromaClient",
    "LockManager",
]


def __getattr__(name: str) -> Any:
    """Load the stable public classes only when callers request them."""
    if name == "ChromaClient":
        from receipt_chroma.data.chroma_client import ChromaClient

        return ChromaClient
    if name == "LockManager":
        from receipt_chroma.lock_manager import LockManager

        return LockManager
    if name in {
        "clean_letter_mask",
        "normalize_glyph",
        "shifted_iou",
        "shifted_iou_stack",
    }:
        from receipt_chroma import glyph_matching

        return getattr(glyph_matching, name)
    if name in {
        "TypefaceFingerprint",
        "TypefaceSourceScore",
        "compute_atlas_registry_sha256",
        "compute_typeface_registry_sha256",
        "load_registry_atlases",
        "match_typeface",
        "score_typeface_sources",
        "validate_typeface_registry",
    }:
        from receipt_chroma import merchant_fingerprint

        return getattr(merchant_fingerprint, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

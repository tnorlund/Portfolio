"""Optional ChromaDB check node for fast-path merchant matching."""

from typing import Dict, Any, Optional
import logging

from receipt_label.langchain.state.metadata_creation import MetadataCreationState
from receipt_label.vector_store import VectorStoreInterface

logger = logging.getLogger(__name__)


async def check_chromadb(
    state: MetadataCreationState,
    chroma_line_client: Optional[VectorStoreInterface] = None,
    embed_fn: Optional[callable] = None,
    threshold: float = 0.7,
) -> Dict[str, Any]:
    """Check ChromaDB for high-confidence merchant matches before Places API search.

    This is an optional fast-path that can provide merchant information from
    similar receipts. If a high-confidence match is found, it can inform the
    Places API search or be used as a fallback.

    Args:
        state: Current workflow state
        chroma_line_client: Optional ChromaDB client for vector search
        embed_fn: Optional embedding function for query vectors
        threshold: Minimum confidence score to consider a match (default: 0.7)

    Returns:
        Dictionary with chroma_candidates and use_chroma flag
    """
    print(f"🔍 Checking ChromaDB for similar receipts...")

    # If no ChromaDB client or embedding function, skip
    if not chroma_line_client or not embed_fn:
        print(f"   ⏭️  ChromaDB not available, skipping")
        return {
            "chroma_candidates": [],
            "use_chroma": False,
            "chroma_best": None,
        }

    try:
        # Import ChromaDB search function
        from receipt_label.merchant_resolution.chroma import chroma_find_candidates

        # Build context dict from state (similar to load_receipt_context)
        from receipt_label.utils.text_reconstruction import ReceiptTextReconstructor
        from receipt_label.merchant_validation.normalize import (
            build_full_address_from_lines,
            build_full_address_from_words,
            normalize_phone,
            normalize_address,
        )

        # Reconstruct text if not already formatted
        if not state.formatted_text and state.lines:
            text, _ = ReceiptTextReconstructor().reconstruct_receipt(state.lines)
        else:
            text = state.formatted_text or ""

        # Build address from words or lines
        words_address = build_full_address_from_words(state.words) if state.words else ""
        lines_address = build_full_address_from_lines(state.lines) if state.lines else ""
        address = words_address or lines_address or ""
        address_normalized = normalize_address(address) if address else ""

        # Extract phones from words
        phones = []
        for w in state.words or []:
            ext = getattr(w, "extracted_data", None) or {}
            etype = (ext.get("type") or "").lower()
            if etype == "phone":
                p = normalize_phone(str(ext.get("value") or getattr(w, "text", "") or ""))
                if p:
                    phones.append(p)

        ctx = {
            "key": (state.image_id, int(state.receipt_id.split("/")[-1])),
            "words": state.words,
            "lines": state.lines,
            "address": address,
            "address_normalized": address_normalized,
            "phones": phones,
            "header_text": text,
        }

        # Search ChromaDB for candidates
        candidates = chroma_find_candidates(
            line_client=chroma_line_client,
            embed_fn=embed_fn,
            ctx=ctx,
            get_neighbor_phones=None,  # Can be enhanced later if needed
        )

        # Find best candidate
        chroma_best = None
        if candidates:
            chroma_best = max(candidates, key=lambda x: x.get("score", 0.0), default=None)
            best_score = chroma_best.get("score", 0.0) if chroma_best else 0.0

            print(f"   ✅ Found {len(candidates)} ChromaDB candidate(s)")
            print(f"      Best score: {best_score:.3f} (threshold: {threshold})")

            if best_score >= threshold:
                print(f"   ✅ High-confidence ChromaDB match found")
                return {
                    "chroma_candidates": candidates,
                    "use_chroma": True,
                    "chroma_best": chroma_best,
                }
            else:
                print(f"   ⚠️  ChromaDB match below threshold, will use Places API")
        else:
            print(f"   ⚠️  No ChromaDB candidates found, will use Places API")

        return {
            "chroma_candidates": candidates,
            "use_chroma": False,
            "chroma_best": chroma_best,
        }

    except Exception as e:
        logger.warning(f"ChromaDB check failed: {e}", exc_info=True)
        print(f"   ⚠️  ChromaDB check failed: {e}")
        return {
            "chroma_candidates": [],
            "use_chroma": False,
            "chroma_best": None,
            "error_count": state.error_count + 1,
            "last_error": str(e),
        }


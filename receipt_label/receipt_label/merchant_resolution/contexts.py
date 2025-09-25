from typing import Tuple, Dict, Any, List
import logging

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.utils.text_reconstruction import ReceiptTextReconstructor
from receipt_label.merchant_validation.normalize import (
    build_full_address_from_lines,
    build_full_address_from_words,
    normalize_phone,
    normalize_address,
)

logger = logging.getLogger(__name__)


def load_receipt_context(
    client: DynamoClient, key: Tuple[str, int]
) -> Dict[str, Any]:
    """Load words/lines for a receipt and derive simple context.

    Returns a dict with keys: key, words, lines, address, phones, header_text.
    """
    img, rec = key
    # Use targeted queries to Dynamo for efficiency
    try:
        words = client.list_receipt_words_from_receipt(
            image_id=img,
            receipt_id=rec,
        )
    except Exception:
        # Fallback to full scan if targeted method unavailable
        all_words, _ = client.list_receipt_words()
        words = [
            w
            for w in all_words
            if str(w.image_id) == img and int(w.receipt_id) == rec
        ]
    try:
        lines = client.list_receipt_lines_from_receipt(
            image_id=img,
            receipt_id=rec,
        )
    except Exception:
        all_lines, _ = client.list_receipt_lines()
        lines = [
            l
            for l in all_lines
            if str(l.image_id) == img and int(l.receipt_id) == rec
        ]

    text, _ = ReceiptTextReconstructor().reconstruct_receipt(lines)
    words_address = build_full_address_from_words(words)
    lines_address = build_full_address_from_lines(lines)
    address = words_address or lines_address or ""
    address_normalized = normalize_address(address) if address else ""
    phones: List[str] = []
    for w in words:
        ext = getattr(w, "extracted_data", None) or {}
        etype = (ext.get("type") or "").lower()
        if etype == "phone":
            p = normalize_phone(str(ext.get("value") or w.text or ""))
            if p:
                phones.append(p)
        elif etype == "address" and not words_address:
            # If we have address fragments on words and words_address is empty, allow enrichment via lines builder later
            logger.info(w.extracted_data)
            pass

    # Debug: log address assembly
    try:
        logger.info(
            "address_assembly",
            extra={
                "address": address,
                "address_normalized": address_normalized,
                "source": (
                    "words"
                    if words_address
                    else ("lines" if lines_address else "none")
                ),
                "words_address": words_address or "",
                "lines_address": lines_address or "",
            },
        )
    except Exception:
        pass

    return {
        "key": key,
        "words": words,
        "lines": lines,
        "address": address,
        "address_normalized": address_normalized,
        "phones": phones,
        "header_text": text or "",
    }

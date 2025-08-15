"""Address label validation logic."""

# pylint: disable=duplicate-code

import re
from typing import Optional

from rapidfuzz.fuzz import partial_ratio, ratio
from receipt_dynamo.entities import ReceiptWord  # type: ignore
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_label.label_validation.data import LabelValidationResult
from receipt_label.label_validation.utils import (
    normalize_text,
    chroma_id_from_label,
)
from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager

SUFFIXES = {
    "rd": "road",
    "st": "street",
    "ave": "avenue",
    "blvd": "boulevard",
    "dr": "drive",
    "ln": "lane",
    "ct": "court",
    "trl": "trail",
    "hwy": "highway",
}

DIRECTIONAL_ABBREVIATIONS = {
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "ne": "northeast",
    "nw": "northwest",
    "se": "southeast",
    "sw": "southwest",
}

UNIT_ABBREVIATIONS = {
    "apt": "apartment",
    "ste": "suite",
    "unit": "unit",
    "#": "apartment",
}

STATE_MAP = {
    "california": "ca",
    "new york": "ny",
    "texas": "tx",
    "florida": "fl",
    "illinois": "il",
}


def _normalize_address(text: str) -> str:
    """Enhanced address normalization for better matching."""
    # Start with basic text normalization
    normalized = normalize_text(text)

    # Remove excessive punctuation and spaces
    normalized = re.sub(r"[.]{2,}", " ", normalized)  # Replace multiple dots
    normalized = re.sub(
        r"[!]{2,}", " ", normalized
    )  # Replace multiple exclamation marks
    normalized = re.sub(
        r"\s+", " ", normalized
    )  # Replace multiple spaces with single space

    # Split into tokens for replacement
    tokens = normalized.split()
    processed_tokens = []

    for token in tokens:
        # Remove trailing punctuation from token
        clean_token = token.rstrip(".,!")

        # Apply suffix replacements
        if clean_token in SUFFIXES:
            processed_tokens.append(SUFFIXES[clean_token])
        # Apply directional replacements
        elif clean_token in DIRECTIONAL_ABBREVIATIONS:
            processed_tokens.append(DIRECTIONAL_ABBREVIATIONS[clean_token])
        # Apply unit replacements
        elif clean_token in UNIT_ABBREVIATIONS:
            processed_tokens.append(UNIT_ABBREVIATIONS[clean_token])
        else:
            processed_tokens.append(clean_token)

    return " ".join(processed_tokens)


def _fuzzy_in(text: str, target: str, threshold: int = 90) -> bool:
    """Return ``True`` if ``text`` fuzzily matches ``target``."""

    return partial_ratio(text, target) >= threshold or any(
        ratio(text, token) >= threshold for token in target.split()
    )


def _merged_address_variants(word: ReceiptWord, metadata: dict) -> list[str]:
    """Return possible address strings from the word and its neighbors."""

    current = _normalize_address(word.text)
    variants = [current]

    left = metadata.get("left")
    right = metadata.get("right")

    if left and left != "<EDGE>":
        left_clean = _normalize_address(left)
        variants.append(f"{left_clean} {current}")
        variants.append(f"{left_clean}{current}")

    if right and right != "<EDGE>":
        right_clean = _normalize_address(right)
        variants.append(f"{current} {right_clean}")
        variants.append(f"{current}{right_clean}")

    if left and right and left != "<EDGE>" and right != "<EDGE>":
        left_clean = _normalize_address(left)
        right_clean = _normalize_address(right)
        variants.append(f"{left_clean} {current} {right_clean}")
        variants.append(f"{left_clean}{current}{right_clean}")

    return variants


def validate_address(
    word: ReceiptWord,
    label: ReceiptWordLabel,
    receipt_metadata,  # Can be ReceiptMetadata or SimpleNamespace for testing
    client_manager: Optional[ClientManager] = None,
) -> LabelValidationResult:
    """Validate that a word is part of the receipt address."""

    # Get ChromaDB client from client manager
    if client_manager is None:
        client_manager = get_client_manager()
    chroma_client = client_manager.chroma

    chroma_id = chroma_id_from_label(label)
    canonical_address = (
        _normalize_address(receipt_metadata.canonical_address)
        if receipt_metadata.canonical_address
        else ""
    )

    # Get vector from ChromaDB
    results = chroma_client.get_by_ids("words", [chroma_id], include=["metadatas"])
    
    # Extract vector data
    vector_data = None
    if results and 'ids' in results and len(results['ids']) > 0:
        idx = results['ids'].index(chroma_id) if chroma_id in results['ids'] else -1
        if idx >= 0:
            vector_data = {
                'metadata': results['metadatas'][idx] if 'metadatas' in results else {}
            }
    if vector_data is None:
        return LabelValidationResult(
            image_id=label.image_id,
            receipt_id=label.receipt_id,
            line_id=label.line_id,
            word_id=label.word_id,
            label=label.label,
            status="NO_VECTOR",
            is_consistent=False,
            avg_similarity=0.0,
            neighbors=[],
            pinecone_id=chroma_id,
        )

    variants = _merged_address_variants(word, vector_data['metadata'])

    looks_like_address = False
    for v in variants:
        if v in canonical_address:
            looks_like_address = True
            break
        if v in SUFFIXES and SUFFIXES[v] in canonical_address:
            looks_like_address = True
            break
        if re.match(r"\d{5}-\d{4}", v) and v[:5] in canonical_address:
            looks_like_address = True
            break
        if v in STATE_MAP and STATE_MAP[v] in canonical_address:
            looks_like_address = True
            break
        if v in STATE_MAP.values() and v in canonical_address:
            looks_like_address = True
            break
        if _fuzzy_in(v, canonical_address):
            looks_like_address = True
            break

    return LabelValidationResult(
        image_id=label.image_id,
        receipt_id=label.receipt_id,
        line_id=label.line_id,
        word_id=label.word_id,
        label=label.label,
        status="VALIDATED",
        is_consistent=looks_like_address,
        avg_similarity=1.0 if looks_like_address else 0.0,
        neighbors=[],
        pinecone_id=chroma_id,
    )

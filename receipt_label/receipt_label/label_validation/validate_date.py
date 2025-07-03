"""Date label validation logic."""

# pylint: disable=duplicate-code

import re
from datetime import datetime
from typing import Optional

from receipt_dynamo.entities import ReceiptWord  # type: ignore
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_label.label_validation.data import LabelValidationResult
from receipt_label.label_validation.utils import pinecone_id_from_label
from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager

# Date format patterns
DATE_SLASH_FORMAT = r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
DATE_ISO_FORMAT = r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b"
DATE_ISO_WITH_Z = r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\b"
DATE_ISO_WITH_TZ = r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}\b"
DATE_WITH_TZ_ABBR = r"\b\d{4}-\d{2}-\d{2}\s+[A-Z]{3,4}\b"
DATE_DD_MMM_YYYY = (
    r"\b\d{1,2}[/-]\s*"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
    r"[/-]?\s*\d{2,4}\b"
)
DATE_MMM_DD_YYYY = (
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{1,2},?\s*\d{2,4}\b"
)
DATE_DD_MMM_YYYY_ALT = (
    r"\b\d{1,2}\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{2,4}\b"
)


def _is_date(text: str) -> bool:  # pylint: disable=too-many-return-statements
    """Return ``True`` if the text resembles a date."""

    # Match various date formats including month names and ISO formats
    patterns = [
        DATE_SLASH_FORMAT,
        DATE_ISO_FORMAT,
        DATE_ISO_WITH_Z,
        DATE_ISO_WITH_TZ,
        DATE_WITH_TZ_ABBR,
        DATE_DD_MMM_YYYY,
        DATE_MMM_DD_YYYY,
        DATE_DD_MMM_YYYY_ALT,
    ]

    # First check if it matches a pattern
    if not any(
        re.search(pattern, text.strip(), re.IGNORECASE) for pattern in patterns
    ):
        return False

    # Check for partial dates that should be invalid (MM/YYYY without day)
    if re.match(r"^\d{1,2}[/-]\d{4}$", text.strip()):
        return False

    # For numeric dates, validate the month/day values
    # MM/DD/YYYY format
    mm_dd_yyyy = re.search(
        r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", text.strip()
    )
    if mm_dd_yyyy:
        month, day, year = map(int, mm_dd_yyyy.groups())
        if month > 12 or month < 1 or day > 31 or day < 1:
            return False
        # Check for February 30th and other invalid dates
        try:
            datetime(year, month, day)
        except ValueError:
            return False

    # YYYY-MM-DD format
    yyyy_mm_dd = re.search(
        r"\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b", text.strip()
    )
    if yyyy_mm_dd:
        year, month, day = map(int, yyyy_mm_dd.groups())
        if month > 12 or month < 1 or day > 31 or day < 1:
            return False
        try:
            datetime(year, month, day)
        except ValueError:
            return False

    # ISO format validation
    iso_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})T", text.strip())
    if iso_match:
        year, month, day = map(int, iso_match.groups())
        try:
            datetime(year, month, day)
        except ValueError:
            return False

    return True


# Merge left and right words with current word to create date candidates
def _merged_date_candidates_from_text(
    word: ReceiptWord, metadata: dict
) -> list[str]:
    """Return possible date strings from the word and its neighbors."""

    current = word.text.strip()
    variants = [current]

    left = metadata.get("left")
    right = metadata.get("right")

    if left and left != "<EDGE>":
        variants.append(f"{left.strip()}{current}")

    if right and right != "<EDGE>":
        variants.append(f"{current}{right.strip()}")

    if left and right and left != "<EDGE>" and right != "<EDGE>":
        variants.append(f"{left.strip()}{current}{right.strip()}")

    return variants


def validate_date(
    word: ReceiptWord,
    label: ReceiptWordLabel,
    client_manager: Optional[ClientManager] = None,
) -> LabelValidationResult:
    """Validate that a word is a date using Pinecone neighbors."""

    # Get pinecone index from client manager
    if client_manager is None:
        client_manager = get_client_manager()
    pinecone_index = client_manager.pinecone

    pinecone_id = pinecone_id_from_label(label)
    fetch_response = pinecone_index.fetch(ids=[pinecone_id], namespace="words")
    vector_data = fetch_response.vectors.get(pinecone_id)

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
            pinecone_id=pinecone_id,
        )

    vector = vector_data.values
    query_response = pinecone_index.query(
        vector=vector,
        top_k=10,
        include_metadata=True,
        filter={
            "valid_labels": {"$in": ["DATE"]},
        },
        namespace="words",
    )

    matches = query_response.matches
    avg_similarity = (
        sum(match.score for match in matches) / len(matches)
        if matches
        else 0.0
    )

    # Try merged variants for date detection
    variants = _merged_date_candidates_from_text(word, vector_data.metadata)
    looks_like_date = any(_is_date(v) for v in variants)

    is_consistent = avg_similarity > 0.7 and looks_like_date

    return LabelValidationResult(
        image_id=label.image_id,
        receipt_id=label.receipt_id,
        line_id=label.line_id,
        word_id=label.word_id,
        label=label.label,
        status="VALIDATED",
        is_consistent=is_consistent,
        avg_similarity=avg_similarity,
        neighbors=[match.id for match in matches],
        pinecone_id=pinecone_id,
    )

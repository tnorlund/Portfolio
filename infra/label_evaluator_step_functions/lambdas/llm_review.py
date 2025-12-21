"""
LLM Review Lambda Handler - Batched Per-Merchant Processing

Reviews flagged label issues using Ollama LLM with ChromaDB for semantic
similarity search. Processes all issues for a merchant in a single invocation
for efficient ChromaDB and DynamoDB access.

Key Features:
- Batched processing per merchant (one Lambda invocation per merchant)
- Semantic similarity search using ChromaDB word embeddings
- Rich context including validation history with reasoning from DynamoDB
- Dynamic merchant scoping (same-merchant vs cross-merchant based on data)
- Full similarity distribution shown to LLM (no artificial limits)

Environment Variables:
- BATCH_BUCKET: S3 bucket for data and results
- CHROMADB_BUCKET: S3 bucket with ChromaDB snapshots
- DYNAMODB_TABLE_NAME: DynamoDB table for label history
- RECEIPT_AGENT_OLLAMA_API_KEY: Ollama Cloud API key
- RECEIPT_AGENT_OLLAMA_BASE_URL: Ollama API base URL
- RECEIPT_AGENT_OLLAMA_MODEL: Ollama model name
- LANGCHAIN_API_KEY: LangSmith API key
- LANGCHAIN_TRACING_V2: Enable tracing
- LANGCHAIN_PROJECT: LangSmith project name
"""

import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any, Optional

import boto3
from receipt_agent import (
    OllamaCircuitBreaker,
    OllamaRateLimitError,
    RateLimitedLLMInvoker,
)

from utils.tracing import flush_langsmith_traces

if TYPE_CHECKING:
    from evaluator_types import (
        LabelDistributionStats,
        LLMDecision,
        LLMReviewBatchOutput,
        MerchantBreakdown,
        SimilarityDistribution,
        SimilarWordEvidence,
    )

logger = logging.getLogger()
logger.setLevel(logging.INFO)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

s3 = boto3.client("s3")


# =============================================================================
# S3 Utilities
# =============================================================================


def download_chromadb_snapshot(
    bucket: str, collection: str, cache_path: str
) -> str:
    """Download ChromaDB snapshot from S3 using atomic pointer pattern."""
    chroma_db_file = os.path.join(cache_path, "chroma.sqlite3")
    if os.path.exists(chroma_db_file):
        logger.info(f"ChromaDB already cached at {cache_path}")
        return cache_path

    logger.info(f"Downloading ChromaDB from s3://{bucket}/{collection}/")

    pointer_key = f"{collection}/snapshot/latest-pointer.txt"
    try:
        response = s3.get_object(Bucket=bucket, Key=pointer_key)
        timestamp = response["Body"].read().decode().strip()
        logger.info(f"Latest snapshot: {timestamp}")
    except Exception as e:
        logger.error(f"Failed to get pointer: {e}")
        raise

    prefix = f"{collection}/snapshot/timestamped/{timestamp}/"
    paginator = s3.get_paginator("list_objects_v2")

    os.makedirs(cache_path, exist_ok=True)
    downloaded = 0

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            relative_path = key[len(prefix) :]
            if not relative_path or key.endswith(".snapshot_hash"):
                continue

            local_path = os.path.join(cache_path, relative_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            s3.download_file(bucket, key, local_path)
            downloaded += 1

    logger.info(f"Downloaded {downloaded} files to {cache_path}")
    return cache_path


def load_json_from_s3(bucket: str, key: str) -> dict[str, Any]:
    """Load JSON data from S3."""
    response = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def upload_json_to_s3(bucket: str, key: str, data: Any) -> None:
    """Upload JSON data to S3."""
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )


# =============================================================================
# Text Pattern Detection
# =============================================================================


def detect_text_pattern(text: str) -> Optional[str]:
    """Detect common text patterns for context."""
    text = text.strip()

    # Currency patterns
    if re.match(r"^\$?\d+\.\d{2}$", text):
        return "currency"
    if re.match(r"^\d+\.\d{2}$", text):
        return "decimal-number"

    # Date patterns
    if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", text):
        return "date-slash"
    if re.match(r"^\d{1,2}-\d{1,2}-\d{2,4}$", text):
        return "date-dash"

    # Time patterns
    if re.match(r"^\d{1,2}:\d{2}(:\d{2})?\s*(AM|PM|am|pm)?$", text):
        return "time"

    # Phone patterns
    if re.match(r"^\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$", text):
        return "phone-number"

    # Zip code
    if re.match(r"^\d{5}(-\d{4})?$", text):
        return "zip-code"

    # Pure digits
    if re.match(r"^\d+$", text):
        n = len(text)
        if n <= 2:
            return "1-2-digit-number"
        elif n <= 4:
            return "3-4-digit-number"
        elif n == 5:
            return "5-digit-number"
        else:
            return f"{n}-digit-number"

    # All caps
    if text.isupper() and len(text) > 2:
        return "all-caps"

    return None


def is_currency_amount(text: str) -> bool:
    """Check if text looks like a currency amount."""
    text = text.strip()
    # Match $X.XX, X.XX, or X.XX patterns (with optional $ and commas)
    return bool(re.match(r"^\$?\d{1,3}(,\d{3})*\.\d{2}$", text))


def parse_currency_value(text: str) -> Optional[float]:
    """Parse currency text to float value."""
    text = text.strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


# Currency-related labels that should be shown in receipt context
CURRENCY_LABELS = {
    "LINE_TOTAL",
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",
    "TENDER",
    "CHANGE",
    "DISCOUNT",
    "SAVINGS",
    "CASH_BACK",
    "REFUND",
    "UNIT_PRICE",
}


def extract_receipt_currency_context(
    words: list[dict],
    labels: list[dict],
) -> list[dict]:
    """
    Extract all currency amounts from a receipt with their labels and context.

    Returns a list of dicts with:
    - amount: the numeric value
    - text: original text
    - label: current label (or None)
    - line_id: line number
    - word_id: word number
    - context: surrounding words on the same line
    """
    # Build label lookup
    label_map: dict[tuple[int, int], dict] = {}
    for lbl in labels:
        key = (lbl.get("line_id"), lbl.get("word_id"))
        label_map[key] = lbl

    # Group words by line for context
    lines: dict[int, list[dict]] = {}
    for word in words:
        line_id = word.get("line_id")
        if line_id not in lines:
            lines[line_id] = []
        lines[line_id].append(word)

    # Sort words within each line by x position
    for line_id in lines:
        lines[line_id].sort(
            key=lambda w: w.get("bounding_box", {}).get("x", 0)
        )

    # Extract currency amounts
    currency_items: list[dict] = []
    for word in words:
        text = word.get("text", "")
        if not is_currency_amount(text):
            continue

        amount = parse_currency_value(text)
        if amount is None:
            continue

        line_id = word.get("line_id")
        word_id = word.get("word_id")
        label_info = label_map.get((line_id, word_id))

        # Build context from same line
        line_words = lines.get(line_id, [])
        word_idx = next(
            (
                i
                for i, w in enumerate(line_words)
                if w.get("word_id") == word_id
            ),
            -1,
        )

        # Get words before this one on the same line
        context_before = []
        if word_idx > 0:
            for w in line_words[max(0, word_idx - 3) : word_idx]:
                context_before.append(w.get("text", ""))

        context = (
            " ".join(context_before) if context_before else "(start of line)"
        )

        currency_items.append(
            {
                "amount": amount,
                "text": text,
                "label": label_info.get("label") if label_info else None,
                "validation_status": (
                    label_info.get("validation_status") if label_info else None
                ),
                "line_id": line_id,
                "word_id": word_id,
                "context": context,
                "y_position": word.get("bounding_box", {}).get("y", 0.5),
            }
        )

    # Sort by position on receipt (top to bottom)
    currency_items.sort(key=lambda x: -x["y_position"])

    return currency_items


def format_currency_context_table(currency_items: list[dict]) -> str:
    """Format currency items as a markdown table for the LLM prompt.

    Note: We intentionally do NOT show validation_status here because we want
    the LLM to make an independent judgment based on mathematical relationships,
    not confirm/deny existing labels.
    """
    if not currency_items:
        return "No currency amounts found on this receipt."

    lines = ["| Amount | Current Label | Line | Preceding Text |"]
    lines.append("|--------|---------------|------|----------------|")

    for item in currency_items:
        amount = f"${item['amount']:.2f}"
        label = item["label"] or "(unlabeled)"
        line_id = item["line_id"]
        context = (
            item["context"][:25] + "..."
            if len(item["context"]) > 25
            else item["context"]
        )

        lines.append(f"| {amount} | {label} | {line_id} | {context} |")

    return "\n".join(lines)


def compute_currency_math_hints(currency_items: list[dict]) -> str:
    """
    Compute mathematical relationships between currency amounts.

    Returns hints like "LINE_TOTALs sum to $10.78" to help LLM reason.
    """
    hints = []

    # Group by label
    by_label: dict[str, list[float]] = {}
    for item in currency_items:
        label = item.get("label")
        if label:
            if label not in by_label:
                by_label[label] = []
            by_label[label].append(item["amount"])

    # Sum of LINE_TOTALs and UNIT_PRICEs (both represent item amounts)
    line_totals = by_label.get("LINE_TOTAL", [])
    unit_prices = by_label.get("UNIT_PRICE", [])
    item_amounts = line_totals + unit_prices

    if item_amounts:
        total = sum(item_amounts)
        label_desc = []
        if line_totals:
            label_desc.append(f"{len(line_totals)} LINE_TOTAL")
        if unit_prices:
            label_desc.append(f"{len(unit_prices)} UNIT_PRICE")
        hints.append(
            f"- Item amounts ({', '.join(label_desc)}): sum to ${total:.2f}"
        )

    # Check for GRAND_TOTAL match against item amounts
    grand_totals = by_label.get("GRAND_TOTAL", [])
    if grand_totals and item_amounts:
        items_sum = sum(item_amounts)
        for gt in set(grand_totals):
            if abs(gt - items_sum) < 0.01:
                hints.append(
                    f"- GRAND_TOTAL ${gt:.2f} equals sum of item amounts "
                    f"(${items_sum:.2f}) - mathematically consistent"
                )
            else:
                diff = gt - items_sum
                if diff > 0:
                    hints.append(
                        f"- GRAND_TOTAL ${gt:.2f} = items (${items_sum:.2f}) + "
                        f"${diff:.2f} (likely tax/fees)"
                    )
                else:
                    hints.append(
                        f"- GRAND_TOTAL ${gt:.2f} is ${abs(diff):.2f} less than "
                        f"items sum (${items_sum:.2f}) - possible discount"
                    )

    # Check for duplicate amounts
    amount_counts: dict[float, int] = {}
    for item in currency_items:
        amt = item["amount"]
        amount_counts[amt] = amount_counts.get(amt, 0) + 1

    duplicates = [(amt, cnt) for amt, cnt in amount_counts.items() if cnt > 1]
    if duplicates:
        for amt, cnt in duplicates:
            labels = [
                item["label"]
                for item in currency_items
                if item["amount"] == amt and item["label"]
            ]
            if labels:
                hints.append(
                    f"- ${amt:.2f} appears {cnt} times with labels: "
                    f"{', '.join(labels)}"
                )

    return "\n".join(hints) if hints else "No mathematical patterns detected."


def describe_position(x: float, y: float) -> str:
    """Describe position on receipt."""
    # Y: 0=bottom, 1=top in our normalized space
    if y > 0.7:
        v_pos = "top"
    elif y > 0.3:
        v_pos = "middle"
    else:
        v_pos = "bottom"

    if x < 0.33:
        h_pos = "left"
    elif x < 0.66:
        h_pos = "center"
    else:
        h_pos = "right"

    return f"{v_pos}-{h_pos}"


# =============================================================================
# ChromaDB Similar Word Query
# =============================================================================


def build_word_chroma_id(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    """Build ChromaDB ID for a word."""
    return (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
        f"#LINE#{line_id:05d}#WORD#{word_id:05d}"
    )


def query_similar_words(
    chroma_client: Any,
    word_text: str,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    target_merchant: str,
    n_results: int = 100,
) -> list["SimilarWordEvidence"]:
    """
    Query ChromaDB for semantically similar words.

    Returns ALL similar words above a minimum threshold, grouped by merchant.
    The LLM decides what evidence is sufficient.
    """
    word_chroma_id = build_word_chroma_id(
        image_id, receipt_id, line_id, word_id
    )

    try:
        # First, get the target word's embedding
        word_result = chroma_client.get(
            collection_name="words",
            ids=[word_chroma_id],
            include=["embeddings"],
        )

        embeddings = word_result.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            logger.warning(f"No embedding found for {word_chroma_id}")
            return []

        embedding = embeddings[0]
        # Handle numpy array - convert to list for ChromaDB query
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()

        # Query for similar words (no merchant filter - we'll categorize after)
        results = chroma_client.query(
            collection_name="words",
            query_embeddings=[embedding],
            n_results=n_results,
            where={"label_status": {"$in": ["validated", "auto_suggested"]}},
            include=["metadatas", "distances"],
        )

        metadatas = results.get("metadatas")
        if metadatas is None or len(metadatas) == 0 or len(metadatas[0]) == 0:
            return []

        evidence_list: list["SimilarWordEvidence"] = []
        distances = results.get("distances", [[]])[0]

        for metadata, distance in zip(metadatas[0], distances):
            # Convert L2 distance to similarity score (0-1)
            similarity = max(0.0, 1.0 - (distance / 2.0))

            # Skip very low similarity
            if similarity < 0.3:
                continue

            # Skip self
            chroma_id = (
                f"IMAGE#{metadata.get('image_id')}"
                f"#RECEIPT#{metadata.get('receipt_id', 0):05d}"
                f"#LINE#{metadata.get('line_id', 0):05d}"
                f"#WORD#{metadata.get('word_id', 0):05d}"
            )
            if chroma_id == word_chroma_id:
                continue

            merchant = metadata.get("merchant_name", "Unknown")
            is_same = merchant.lower() == target_merchant.lower()

            # Parse valid/invalid labels from comma-delimited format
            valid_labels_str = metadata.get("valid_labels", "")
            invalid_labels_str = metadata.get("invalid_labels", "")

            valid_labels = [
                l for l in valid_labels_str.split(",") if l.strip()
            ]
            invalid_labels = [
                l for l in invalid_labels_str.split(",") if l.strip()
            ]

            evidence: "SimilarWordEvidence" = {
                "word_text": metadata.get("text", ""),
                "similarity_score": round(similarity, 3),
                "chroma_id": chroma_id,
                "position_x": metadata.get("x", 0.5),
                "position_y": metadata.get("y", 0.5),
                "position_description": describe_position(
                    metadata.get("x", 0.5), metadata.get("y", 0.5)
                ),
                "left_neighbor": metadata.get("left", "<EDGE>"),
                "right_neighbor": metadata.get("right", "<EDGE>"),
                "current_label": metadata.get("label"),
                "label_status": metadata.get("label_status", "unvalidated"),
                "validated_as": [],  # Will be enriched from DynamoDB
                "invalidated_as": [],
                "merchant_name": merchant,
                "is_same_merchant": is_same,
            }

            # Pre-populate validation info from metadata
            for label in valid_labels:
                evidence["validated_as"].append(
                    {
                        "label": label,
                        "reasoning": None,  # Will be fetched from DynamoDB
                        "proposed_by": metadata.get("label_proposed_by"),
                        "timestamp": metadata.get("label_validated_at", ""),
                    }
                )

            for label in invalid_labels:
                evidence["invalidated_as"].append(
                    {
                        "label": label,
                        "reasoning": None,
                        "proposed_by": None,
                        "timestamp": "",
                    }
                )

            evidence_list.append(evidence)

        # Sort by relevance: same merchant + validated + high similarity
        evidence_list.sort(
            key=lambda e: (
                e["is_same_merchant"],
                len(e["validated_as"]) > 0,
                e["similarity_score"],
            ),
            reverse=True,
        )

        return evidence_list

    except Exception as e:
        logger.warning(f"Error querying similar words: {e}")
        return []


def enrich_evidence_with_dynamo_reasoning(
    evidence_list: list["SimilarWordEvidence"],
    dynamo_client: Any,
    limit: int = 20,
) -> list["SimilarWordEvidence"]:
    """
    Fetch reasoning from DynamoDB for top similar words.

    Only fetches for the top N most relevant words to limit DynamoDB calls.
    """
    for evidence in evidence_list[:limit]:
        try:
            # Parse IDs from chroma_id
            parts = evidence["chroma_id"].split("#")
            image_id = parts[1]
            receipt_id = int(parts[3])
            line_id = int(parts[5])
            word_id = int(parts[7])

            # Fetch labels from DynamoDB
            labels = dynamo_client.list_receipt_word_labels_for_word(
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=line_id,
                word_id=word_id,
            )

            # Enrich validation history with reasoning
            validated_map = {v["label"]: v for v in evidence["validated_as"]}
            invalidated_map = {
                v["label"]: v for v in evidence["invalidated_as"]
            }

            for label in labels:
                if label.validation_status == "VALID":
                    if label.label in validated_map:
                        validated_map[label.label][
                            "reasoning"
                        ] = label.reasoning
                        validated_map[label.label][
                            "proposed_by"
                        ] = label.label_proposed_by
                        validated_map[label.label][
                            "timestamp"
                        ] = label.timestamp_added
                elif label.validation_status == "INVALID":
                    if label.label in invalidated_map:
                        invalidated_map[label.label][
                            "reasoning"
                        ] = label.reasoning
                        invalidated_map[label.label][
                            "proposed_by"
                        ] = label.label_proposed_by

        except Exception as e:
            logger.debug(f"Could not enrich evidence: {e}")
            continue

    return evidence_list


def compute_similarity_distribution(
    evidence_list: list["SimilarWordEvidence"],
) -> "SimilarityDistribution":
    """Compute distribution of similarity scores."""
    dist: "SimilarityDistribution" = {
        "very_high": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }

    for e in evidence_list:
        score = e["similarity_score"]
        if score >= 0.9:
            dist["very_high"] += 1
        elif score >= 0.7:
            dist["high"] += 1
        elif score >= 0.5:
            dist["medium"] += 1
        else:
            dist["low"] += 1

    return dist


def compute_label_distribution(
    evidence_list: list["SimilarWordEvidence"],
) -> dict[str, "LabelDistributionStats"]:
    """Compute label distribution across similar words."""
    label_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "example_words": [],
        }
    )

    for e in evidence_list:
        label = e["current_label"]
        if not label:
            continue

        stats = label_stats[label]
        stats["count"] += 1
        stats["valid_count"] += len(e["validated_as"])
        stats["invalid_count"] += len(e["invalidated_as"])

        if len(stats["example_words"]) < 3:
            stats["example_words"].append(e["word_text"])

    return dict(label_stats)


def compute_merchant_breakdown(
    evidence_list: list["SimilarWordEvidence"],
) -> list["MerchantBreakdown"]:
    """Compute label counts by merchant."""
    merchant_labels: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"is_same": False, "labels": Counter()}
    )

    for e in evidence_list:
        merchant = e["merchant_name"]
        label = e["current_label"]
        if label:
            merchant_labels[merchant]["labels"][label] += 1
            if e["is_same_merchant"]:
                merchant_labels[merchant]["is_same"] = True

    breakdown: list["MerchantBreakdown"] = []
    for merchant, data in sorted(
        merchant_labels.items(), key=lambda x: -sum(x[1]["labels"].values())
    ):
        breakdown.append(
            {
                "merchant_name": merchant,
                "is_same_merchant": data["is_same"],
                "labels": dict(data["labels"]),
            }
        )

    return breakdown


# =============================================================================
# Prompt Building
# =============================================================================

CORE_LABELS = """
MERCHANT_NAME: Trading name or brand of the store (e.g., "Sprouts", "Costco")
STORE_NUMBER: Store/location identifier number
STORE_HOURS: Business hours printed on receipt
PHONE_NUMBER: Store telephone number
WEBSITE: Web address or email
LOYALTY_ID: Customer loyalty/rewards ID
ADDRESS_LINE: Full or partial address line (street, city, state, zip)

DATE: Transaction date
TIME: Transaction time

PAYMENT_METHOD: Payment instrument type (VISA, CASH, MASTERCARD, etc.)
CARD_LAST_4: Last 4 digits of payment card

COUPON: Coupon code or description
DISCOUNT: Non-coupon discount line
SAVINGS: Amount saved

PRODUCT_NAME: Name of product being purchased
QUANTITY: Number of units purchased
UNIT_PRICE: Price per unit
LINE_TOTAL: Total for a line item
WEIGHT: Weight measurement for weighted items

SUBTOTAL: Subtotal before tax
TAX: Tax amount or description
TAX_RATE: Tax percentage
GRAND_TOTAL: Final total paid
TENDER: Amount tendered by customer
CHANGE: Change returned to customer
CASH_BACK: Cash back amount
REFUND: Refund amount or description

RECEIPT_NUMBER: Transaction/receipt identifier
CASHIER: Cashier name or ID
REGISTER: Register/terminal number
BARCODE: Barcode number

OTHER: Miscellaneous text not fitting other categories
"""


def format_line_item_patterns(patterns: Optional[dict]) -> str:
    """Format line item patterns for the LLM prompt."""
    if not patterns:
        return "No line item patterns available for this merchant."

    lines = []
    lines.append(f"**Merchant**: {patterns.get('merchant', 'Unknown')}")
    lines.append(
        f"**Item Structure**: {patterns.get('item_structure', 'unknown')}"
    )

    lpi = patterns.get("lines_per_item", {})
    if lpi:
        lines.append(
            f"**Lines per Item**: typical={lpi.get('typical', '?')}, "
            f"range=[{lpi.get('min', '?')}, {lpi.get('max', '?')}]"
        )

    if patterns.get("item_start_marker"):
        lines.append(f"**Item Start**: {patterns['item_start_marker']}")

    if patterns.get("item_end_marker"):
        lines.append(f"**Item End**: {patterns['item_end_marker']}")

    if patterns.get("grouping_rule"):
        lines.append(f"**Grouping Rule**: {patterns['grouping_rule']}")

    label_positions = patterns.get("label_positions", {})
    if label_positions:
        pos_lines = []
        for label, position in label_positions.items():
            pos_lines.append(f"  - {label}: {position}")
        if pos_lines:
            lines.append("**Typical Label Positions**:")
            lines.extend(pos_lines)

    return "\n".join(lines)


def build_review_prompt(
    issue: dict[str, Any],
    similar_evidence: list["SimilarWordEvidence"],
    similarity_dist: "SimilarityDistribution",
    label_dist: dict[str, "LabelDistributionStats"],
    merchant_breakdown: list["MerchantBreakdown"],
    merchant_name: str,
    merchant_receipt_count: int,
    currency_context: Optional[list[dict]] = None,
    line_item_patterns: Optional[dict] = None,
) -> str:
    """Build comprehensive LLM review prompt with full context."""

    word_text = issue.get("word_text", "")
    current_label = issue.get("current_label") or "NONE (unlabeled)"
    issue_type = issue.get("type", "unknown")
    evaluator_reasoning = issue.get("reasoning", "No reasoning provided")

    # Build similar words section
    same_merchant_examples = []
    other_merchant_examples = []

    for e in similar_evidence[:30]:  # Show top 30
        line = (
            f"- \"{e['word_text']}\" (similarity: {e['similarity_score']:.0%})"
        )
        line += f"\n  Context: `{e['left_neighbor']}` | **{e['word_text']}** "
        line += f"| `{e['right_neighbor']}`"
        line += f"\n  Position: {e['position_description']}"

        if e["validated_as"]:
            for v in e["validated_as"][:2]:
                reasoning = v.get("reasoning") or "no reasoning recorded"
                line += (
                    f"\n  VALIDATED as **{v['label']}**: \"{reasoning[:100]}\""
                )

        if e["invalidated_as"]:
            for v in e["invalidated_as"][:2]:
                reasoning = v.get("reasoning") or "no reasoning recorded"
                line += f"\n  INVALIDATED as **{v['label']}**: \"{reasoning[:100]}\""

        if e["is_same_merchant"]:
            same_merchant_examples.append(line)
        else:
            other_merchant_examples.append(line)

    # Build distribution summary
    dist_summary = (
        f"- {similarity_dist['very_high']} words with similarity >= 90% "
        f"(very similar)\n"
        f"- {similarity_dist['high']} words with similarity 70-90% (similar)\n"
        f"- {similarity_dist['medium']} words with similarity 50-70% "
        f"(somewhat similar)\n"
        f"- {similarity_dist['low']} words with similarity < 50% "
        f"(weak matches)"
    )

    # Build label distribution
    label_summary_lines = []
    for label, stats in sorted(
        label_dist.items(), key=lambda x: -x[1]["count"]
    )[:10]:
        examples = ", ".join(stats["example_words"][:3])
        label_summary_lines.append(
            f"- **{label}**: {stats['count']} occurrences "
            f"({stats['valid_count']} validated, "
            f"{stats['invalid_count']} invalidated) "
            f'e.g., "{examples}"'
        )
    label_summary = "\n".join(label_summary_lines) or "No label data available"

    # Merchant breakdown
    merchant_lines = []
    for m in merchant_breakdown[:5]:
        marker = " (SAME MERCHANT)" if m["is_same_merchant"] else ""
        labels_str = ", ".join(
            f"{l}: {c}" for l, c in list(m["labels"].items())[:3]
        )
        merchant_lines.append(f"- {m['merchant_name']}{marker}: {labels_str}")
    merchant_summary = "\n".join(merchant_lines) or "No merchant data"

    # Data sparsity note
    if merchant_receipt_count < 10:
        sparsity_note = (
            f"\n**NOTE**: Only {merchant_receipt_count} receipts available "
            f"for {merchant_name}. Cross-merchant examples are shown for "
            f"additional context.\n"
        )
    else:
        sparsity_note = ""

    prompt = f"""# Receipt Label Validation Task

You are reviewing a potential labeling issue on a receipt. Analyze the evidence
carefully and make a decision about whether the current label is correct.

## The Issue Being Reviewed

**Word**: "{word_text}"
**Current Label**: {current_label}
**Issue Type**: {issue_type}
**Evaluator's Concern**: {evaluator_reasoning}

## Label Definitions

{CORE_LABELS}

## Semantic Similarity Evidence
{sparsity_note}
The following words are semantically similar to "{word_text}" based on their
embeddings (which include surrounding context). The similarity score indicates
how close the embedding is to the target word.

### Distribution Summary

{dist_summary}

### Label Distribution Across Similar Words

{label_summary}

### By Merchant

{merchant_summary}

### From Same Merchant ({merchant_name})

{chr(10).join(same_merchant_examples[:15]) if same_merchant_examples else "No examples from same merchant"}

### From Other Merchants

{chr(10).join(other_merchant_examples[:15]) if other_merchant_examples else "No cross-merchant examples"}

## This Receipt's Currency Amounts

{format_currency_context_table(currency_context) if currency_context else "Currency context not available."}

### Mathematical Observations

{compute_currency_math_hints(currency_context) if currency_context else "No math hints available."}

**IMPORTANT**: When reviewing currency-related labels (LINE_TOTAL, SUBTOTAL, TAX,
GRAND_TOTAL, TENDER, CHANGE), use the table above to understand the relationships
between amounts on THIS receipt. The same dollar amount may correctly appear with
the same label multiple times (e.g., GRAND_TOTAL shown at top and bottom of receipt).

## Line Item Patterns for {merchant_name}

{format_line_item_patterns(line_item_patterns)}

**IMPORTANT**: When reviewing line item labels (PRODUCT_NAME, QUANTITY, UNIT_PRICE,
LINE_TOTAL, SKU), use the patterns above to understand how line items are structured
for this specific merchant. Multi-line items may have the product name, quantity, and
price on separate lines that all belong to the same item.

## Your Task

Based on all evidence above, determine:

1. **Decision**:
   - VALID: The current label IS correct (the flag was a false positive)
   - INVALID: The current label IS wrong - provide the correct label
   - NEEDS_REVIEW: Genuinely ambiguous, needs human review

2. **Reasoning**: Cite specific evidence from the similar words

3. **Suggested Label**: If INVALID, what should it be? Use a label from the
   definitions above, or null if no label applies.

4. **Confidence**: low / medium / high

Respond with ONLY a JSON object:
```json
{{
  "decision": "VALID | INVALID | NEEDS_REVIEW",
  "reasoning": "Your detailed reasoning citing evidence...",
  "suggested_label": "LABEL_NAME or null",
  "confidence": "low | medium | high"
}}
```
"""
    return prompt


def parse_llm_response(response_text: str) -> "LLMDecision":
    """Parse LLM JSON response."""
    # Handle markdown code blocks
    if "```" in response_text:
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", response_text, re.S)
        if match:
            response_text = match.group(1)

    response_text = response_text.strip()

    try:
        result = json.loads(response_text)
        decision = result.get("decision", "NEEDS_REVIEW")
        if decision not in ("VALID", "INVALID", "NEEDS_REVIEW"):
            decision = "NEEDS_REVIEW"

        confidence = result.get("confidence", "medium")
        if confidence not in ("low", "medium", "high"):
            confidence = "medium"

        return {
            "decision": decision,
            "reasoning": result.get("reasoning", "No reasoning provided"),
            "suggested_label": result.get("suggested_label"),
            "confidence": confidence,
        }
    except json.JSONDecodeError as e:
        return {
            "decision": "NEEDS_REVIEW",
            "reasoning": f"Failed to parse LLM response: {response_text[:200]}",
            "suggested_label": None,
            "confidence": "low",
        }


# =============================================================================
# Main Handler
# =============================================================================


def handler(event: dict[str, Any], _context: Any) -> "LLMReviewBatchOutput":
    """
    Review a batch of flagged issues using LLM with semantic similarity.

    This handler processes a BATCH of issues (not all issues for a merchant).
    It supports two input formats:
    1. Batch format (from batch_issues handler): batch_s3_key points to a batch file
    2. Legacy format: issues_s3_key points to all issues

    Includes rate limiting with circuit breaker to handle Ollama API limits.
    When rate limits are hit, raises OllamaRateLimitError for Step Function retry.

    Input (batch format):
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Sprouts Farmers Market",
        "merchant_receipt_count": 50,
        "batch_s3_key": "batches/{exec}/{merchant_hash}_0.json",
        "batch_index": 0,
        "dry_run": false
    }

    Input (legacy format):
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Sprouts Farmers Market",
        "merchant_receipt_count": 50,
        "issues_s3_key": "issues/{exec}/{merchant_hash}.json",
        "dry_run": false
    }

    Output:
    {
        "status": "completed",
        "execution_id": "abc123",
        "merchant_name": "Sprouts Farmers Market",
        "batch_index": 0,
        "total_issues": 50,
        "issues_reviewed": 50,
        "decisions": {"VALID": 10, "INVALID": 25, "NEEDS_REVIEW": 15},
        "reviewed_issues_s3_key": "reviewed/{exec}/{merchant_hash}_0.json"
    }

    Raises:
        OllamaRateLimitError: When rate limit is hit, triggers Step Function retry
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "Unknown")
    merchant_receipt_count = event.get("merchant_receipt_count", 0)
    batch_index = event.get("batch_index", 0)
    dry_run = event.get("dry_run", False)
    line_item_patterns_s3_key = event.get("line_item_patterns_s3_key")

    # Support both batch format (batch_s3_key) and legacy format (issues_s3_key)
    batch_s3_key = event.get("batch_s3_key")
    issues_s3_key = event.get("issues_s3_key")
    data_s3_key = batch_s3_key or issues_s3_key

    # Rate limiting configuration
    circuit_breaker_threshold = int(
        os.environ.get("CIRCUIT_BREAKER_THRESHOLD", "5")
    )
    delay_seconds = float(os.environ.get("LLM_DELAY_SECONDS", "0.5"))

    chromadb_bucket = os.environ.get("CHROMADB_BUCKET")
    table_name = os.environ.get(
        "DYNAMODB_TABLE_NAME",
        os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
    )

    if not batch_bucket:
        raise ValueError("batch_bucket is required")
    if not data_s3_key:
        raise ValueError("batch_s3_key or issues_s3_key is required")

    start_time = time.time()

    try:
        # 1. Load collected issues from S3
        logger.info(f"Loading issues from s3://{batch_bucket}/{data_s3_key}")
        issues_data = load_json_from_s3(batch_bucket, data_s3_key)
        collected_issues = issues_data.get("issues", [])

        total_issues = len(collected_issues)
        if total_issues == 0:
            logger.info("No issues to review")
            return {
                "status": "skipped",
                "execution_id": execution_id,
                "merchant_name": merchant_name,
                "total_issues": 0,
                "issues_reviewed": 0,
                "decisions": {},
            }

        logger.info(
            f"Reviewing {total_issues} issues for {merchant_name} "
            f"({merchant_receipt_count} receipts)"
        )

        # 2. Load line item patterns (if available)
        line_item_patterns = None
        if line_item_patterns_s3_key:
            try:
                logger.info(
                    f"Loading line item patterns from "
                    f"s3://{batch_bucket}/{line_item_patterns_s3_key}"
                )
                line_item_patterns = load_json_from_s3(
                    batch_bucket, line_item_patterns_s3_key
                )
                logger.info(
                    f"Loaded patterns: {line_item_patterns.get('item_structure', 'unknown')} "
                    f"structure, {line_item_patterns.get('lines_per_item', {}).get('typical', '?')} "
                    f"lines per item"
                )
            except Exception as e:
                logger.warning(f"Could not load line item patterns: {e}")

        # 3. Setup ChromaDB (was step 2)
        chroma_client = None
        if chromadb_bucket:
            try:
                chroma_path = os.environ.get(
                    "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY", "/tmp/chromadb"
                )
                download_chromadb_snapshot(
                    chromadb_bucket, "words", chroma_path
                )
                os.environ["RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY"] = (
                    chroma_path
                )

                from receipt_chroma import ChromaClient

                chroma_client = ChromaClient(persist_directory=chroma_path)
                logger.info("ChromaDB client initialized")
            except Exception as e:
                logger.warning(f"Could not initialize ChromaDB: {e}")

        # 3. Setup DynamoDB client
        dynamo_client = None
        if table_name:
            try:
                from receipt_dynamo import DynamoClient

                dynamo_client = DynamoClient(table_name=table_name)
                logger.info("DynamoDB client initialized")
            except Exception as e:
                logger.warning(f"Could not initialize DynamoDB: {e}")

        # 4. Setup Ollama LLM
        ollama_api_key = os.environ.get("RECEIPT_AGENT_OLLAMA_API_KEY")
        ollama_base_url = os.environ.get(
            "RECEIPT_AGENT_OLLAMA_BASE_URL", "https://ollama.com"
        )
        ollama_model = os.environ.get(
            "RECEIPT_AGENT_OLLAMA_MODEL", "gpt-oss:20b-cloud"
        )

        if not ollama_api_key:
            raise ValueError("RECEIPT_AGENT_OLLAMA_API_KEY not set")

        from langchain_ollama import ChatOllama

        base_llm = ChatOllama(
            model=ollama_model,
            base_url=ollama_base_url,
            client_kwargs={
                "headers": {"Authorization": f"Bearer {ollama_api_key}"},
                "timeout": 120,
            },
            temperature=0,
        )

        # Wrap LLM with rate limiting and circuit breaker
        circuit_breaker = OllamaCircuitBreaker(
            threshold=circuit_breaker_threshold
        )
        llm_invoker = RateLimitedLLMInvoker(
            llm=base_llm,
            circuit_breaker=circuit_breaker,
            delay_seconds=delay_seconds,
        )

        logger.info(
            f"LLM initialized: {ollama_model} (delay: {delay_seconds}s, "
            f"circuit breaker threshold: {circuit_breaker_threshold})"
        )

        # 5. Review each issue with full context
        decisions: Counter = Counter()
        reviewed_issues: list[dict[str, Any]] = []

        # Cache for similar word queries (same word text might appear multiple times)
        similar_cache: dict[str, list["SimilarWordEvidence"]] = {}

        # Cache for receipt data (keyed by image_id:receipt_id)
        receipt_data_cache: dict[str, dict[str, Any]] = {}

        # Cache for currency context (keyed by image_id:receipt_id)
        currency_context_cache: dict[str, list[dict]] = {}

        for idx, collected in enumerate(collected_issues):
            issue = collected.get("issue", {})
            image_id = collected.get("image_id")
            receipt_id = collected.get("receipt_id")
            word_text = issue.get("word_text", "")
            word_id = issue.get("word_id", 0)
            line_id = issue.get("line_id", 0)

            try:
                # Load receipt data and extract currency context (with caching)
                receipt_cache_key = f"{image_id}:{receipt_id}"
                if receipt_cache_key not in currency_context_cache:
                    try:
                        receipt_data_key = (
                            f"data/{execution_id}/{image_id}_{receipt_id}.json"
                        )
                        receipt_data = load_json_from_s3(
                            batch_bucket, receipt_data_key
                        )
                        receipt_data_cache[receipt_cache_key] = receipt_data

                        # Extract currency context
                        words = receipt_data.get("words", [])
                        labels = receipt_data.get("labels", [])
                        currency_context = extract_receipt_currency_context(
                            words, labels
                        )
                        currency_context_cache[receipt_cache_key] = (
                            currency_context
                        )
                    except Exception as e:
                        logger.debug(
                            f"Could not load receipt data for {receipt_cache_key}: {e}"
                        )
                        currency_context_cache[receipt_cache_key] = []

                currency_context = currency_context_cache.get(
                    receipt_cache_key, []
                )

                # Query similar words (with caching)
                cache_key = f"{image_id}:{receipt_id}:{line_id}:{word_id}"
                if cache_key in similar_cache:
                    similar_evidence = similar_cache[cache_key]
                elif chroma_client:
                    similar_evidence = query_similar_words(
                        chroma_client=chroma_client,
                        word_text=word_text,
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=line_id,
                        word_id=word_id,
                        target_merchant=merchant_name,
                    )

                    # Enrich with DynamoDB reasoning
                    if dynamo_client and similar_evidence:
                        similar_evidence = (
                            enrich_evidence_with_dynamo_reasoning(
                                similar_evidence, dynamo_client, limit=20
                            )
                        )

                    similar_cache[cache_key] = similar_evidence
                else:
                    similar_evidence = []

                # Compute distributions
                similarity_dist = compute_similarity_distribution(
                    similar_evidence
                )
                label_dist = compute_label_distribution(similar_evidence)
                merchant_breakdown = compute_merchant_breakdown(
                    similar_evidence
                )

                # Build prompt
                prompt = build_review_prompt(
                    issue=issue,
                    similar_evidence=similar_evidence,
                    similarity_dist=similarity_dist,
                    label_dist=label_dist,
                    merchant_breakdown=merchant_breakdown,
                    merchant_name=merchant_name,
                    merchant_receipt_count=merchant_receipt_count,
                    currency_context=currency_context,
                    line_item_patterns=line_item_patterns,
                )

                # Call LLM for review using rate-limited invoker
                from langchain_core.messages import HumanMessage

                response = llm_invoker.invoke([HumanMessage(content=prompt)])
                review_result = parse_llm_response(response.content.strip())

                decisions[review_result["decision"]] += 1

                reviewed_issues.append(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "issue": issue,
                        "llm_review": review_result,
                        "similar_word_count": len(similar_evidence),
                    }
                )

                if (idx + 1) % 10 == 0:
                    logger.info(f"Reviewed {idx + 1}/{total_issues} issues")

            except OllamaRateLimitError:
                # Rate limit errors should propagate up for Step Function retry
                # First, save partial progress so we don't lose work
                logger.warning(
                    f"Rate limit hit after {idx}/{total_issues} issues. "
                    f"Saving partial progress."
                )
                if reviewed_issues:
                    merchant_hash = merchant_name.lower().replace(" ", "_")[
                        :30
                    ]
                    partial_s3_key = f"partial/{execution_id}/{merchant_hash}_{batch_index}.json"
                    partial_data = {
                        "execution_id": execution_id,
                        "merchant_name": merchant_name,
                        "batch_index": batch_index,
                        "issues_processed": len(reviewed_issues),
                        "total_issues": total_issues,
                        "decisions": dict(decisions),
                        "issues": reviewed_issues,
                        "rate_limited": True,
                    }
                    upload_json_to_s3(
                        batch_bucket, partial_s3_key, partial_data
                    )
                    logger.info(f"Saved partial progress to {partial_s3_key}")
                raise  # Re-raise for Step Function retry

            except Exception as e:
                logger.warning(f"Error reviewing issue {idx}: {e}")
                reviewed_issues.append(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "issue": issue,
                        "llm_review": {
                            "decision": "NEEDS_REVIEW",
                            "reasoning": f"LLM review failed: {e}",
                            "suggested_label": None,
                            "confidence": "low",
                        },
                        "error": str(e),
                    }
                )
                decisions["NEEDS_REVIEW"] += 1

        logger.info(f"Reviewed {total_issues} issues: {dict(decisions)}")

        # 6. Upload reviewed results to S3
        merchant_hash = merchant_name.lower().replace(" ", "_")[:30]
        reviewed_s3_key = (
            f"reviewed/{execution_id}/{merchant_hash}_{batch_index}.json"
        )

        # Include rate limiting stats in output
        rate_limit_stats = llm_invoker.get_stats()

        reviewed_data = {
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "merchant_receipt_count": merchant_receipt_count,
            "batch_index": batch_index,
            "total_issues": total_issues,
            "issues_reviewed": len(reviewed_issues),
            "decisions": dict(decisions),
            "issues": reviewed_issues,
            "rate_limit_stats": rate_limit_stats,
        }

        upload_json_to_s3(batch_bucket, reviewed_s3_key, reviewed_data)
        logger.info(
            f"Uploaded reviewed results to s3://{batch_bucket}/{reviewed_s3_key}"
        )

        # 7. Log metrics
        from utils.emf_metrics import emf_metrics

        processing_time = time.time() - start_time
        emf_metrics.log_metrics(
            metrics={
                "IssuesReviewed": total_issues,
                "DecisionsValid": decisions.get("VALID", 0),
                "DecisionsInvalid": decisions.get("INVALID", 0),
                "DecisionsNeedsReview": decisions.get("NEEDS_REVIEW", 0),
                "ProcessingTimeSeconds": round(processing_time, 2),
                "SimilarWordsCached": len(similar_cache),
            },
            dimensions={"Merchant": merchant_name[:50]},
            properties={"execution_id": execution_id},
            units={"ProcessingTimeSeconds": "Seconds"},
        )

        # Flush LangSmith traces
        flush_langsmith_traces()

        return {
            "status": "completed",
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "batch_index": batch_index,
            "total_issues": total_issues,
            "issues_reviewed": len(reviewed_issues),
            "decisions": dict(decisions),
            "reviewed_issues_s3_key": reviewed_s3_key,
            "rate_limit_stats": rate_limit_stats,
        }

    except Exception as e:
        logger.error(f"Error in LLM review batch: {e}", exc_info=True)

        from utils.emf_metrics import emf_metrics

        processing_time = time.time() - start_time
        emf_metrics.log_metrics(
            metrics={
                "LLMReviewBatchFailed": 1,
                "ProcessingTimeSeconds": round(processing_time, 2),
            },
            dimensions={"Merchant": merchant_name[:50]},
            properties={"execution_id": execution_id, "error": str(e)},
            units={"ProcessingTimeSeconds": "Seconds"},
        )

        flush_langsmith_traces()

        return {
            "status": "error",
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "batch_index": batch_index,
            "total_issues": 0,
            "issues_reviewed": 0,
            "decisions": {},
            "error": str(e),
        }

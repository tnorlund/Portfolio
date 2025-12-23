"""Discover line item patterns for a merchant.

This handler analyzes sample receipts from a merchant and uses an LLM
to identify patterns for how line items are structured. The patterns
are stored in S3 for use during LLM review.
"""

import json
import logging
import os
import re
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def load_json_from_s3(bucket: str, key: str) -> dict[str, Any] | None:
    """Load JSON data from S3, return None if not found."""
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code in {"NoSuchKey", "404"}:
            return None
        logger.warning("Error loading %s: %s", key, e)
        raise
    except Exception as e:
        logger.warning(f"Error loading {key}: {e}")
        return None


def upload_json_to_s3(bucket: str, key: str, data: Any) -> None:
    """Upload JSON data to S3."""
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )


def get_merchant_hash(merchant_name: str) -> str:
    """Create a safe hash for merchant name."""
    return re.sub(r"[^a-z0-9]", "_", merchant_name.lower())[:50]


def build_receipt_structure(
    dynamo_client, merchant_name: str, limit: int = 3
) -> list[dict]:
    """Build structured receipt data for LLM analysis."""
    result = dynamo_client.get_receipt_places_by_merchant(
        merchant_name, limit=limit
    )
    receipt_places = result[0] if result else []

    if not receipt_places:
        return []

    receipts_data = []

    for place in receipt_places:
        try:
            words = dynamo_client.list_receipt_words_from_receipt(
                place.image_id, place.receipt_id
            )
            labels_result = dynamo_client.list_receipt_word_labels_for_receipt(
                place.image_id, place.receipt_id
            )
            labels = labels_result[0] if labels_result else []
        except Exception as e:
            logger.warning(f"Error fetching receipt data: {e}")
            continue

        if not words or not labels:
            continue

        # Build label lookup (only VALID labels)
        labels_by_word = {}
        for label in labels:
            if label.validation_status == "VALID":
                key = (label.line_id, label.word_id)
                if key not in labels_by_word:
                    labels_by_word[key] = []
                labels_by_word[key].append(label.label)

        # Group words by line, sorted by y-position
        lines = {}
        for word in words:
            if word.line_id not in lines:
                lines[word.line_id] = []
            lines[word.line_id].append(word)

        # Sort lines by y-position (top to bottom)
        sorted_lines = []
        for line_id, line_words in lines.items():
            avg_y = sum(w.bounding_box["y"] for w in line_words) / len(
                line_words
            )
            line_words.sort(key=lambda w: w.bounding_box["x"])
            sorted_lines.append((line_id, avg_y, line_words))
        sorted_lines.sort(key=lambda x: -x[1])

        # Build structured representation
        receipt_lines = []
        for line_id, y_pos, line_words in sorted_lines:
            words_data = []
            for word in line_words:
                key = (line_id, word.word_id)
                word_labels = labels_by_word.get(key, [])
                words_data.append(
                    {
                        "text": word.text,
                        "x": round(word.bounding_box["x"], 3),
                        "labels": word_labels if word_labels else None,
                    }
                )

            # Include lines that have labeled words or are near labeled lines
            if any(w["labels"] for w in words_data):
                receipt_lines.append(
                    {
                        "line_id": line_id,
                        "y": round(y_pos, 3),
                        "words": words_data,
                    }
                )

        if receipt_lines:
            receipts_data.append(
                {
                    "receipt_id": f"{place.image_id[:8]}_{place.receipt_id}",
                    "line_count": len(receipt_lines),
                    "lines": receipt_lines[:50],  # Limit lines per receipt
                }
            )

    return receipts_data[:3]  # Limit to 3 receipts


def build_discovery_prompt(merchant_name: str, receipts_data: list) -> str:
    """Build the LLM prompt for pattern discovery."""
    # Format receipt data for the prompt
    simplified = []
    for receipt in receipts_data[:2]:
        lines = []
        for line in receipt["lines"][:40]:
            words_str = " ".join(
                (
                    f"{w['text']}[{','.join(w['labels'])}]"
                    if w["labels"]
                    else w["text"]
                )
                for w in line["words"]
            )
            lines.append(f"  y={line['y']:.2f} | {words_str}")
        simplified.append("\n".join(lines))

    receipts_text = "\n\n---\n\n".join(simplified)

    prompt = f"""Analyze the following receipt data from "{merchant_name}" and identify LINE ITEM patterns.

Each line shows: y-position | words (with [LABELS] for labeled words)

RECEIPT DATA:
{receipts_text}

---

Analyze this data and respond with ONLY a JSON object (no other text):

{{
  "merchant": "{merchant_name}",
  "item_structure": "single-line" or "multi-line",
  "lines_per_item": {{"typical": N, "min": N, "max": N}},
  "item_start_marker": "description of what marks the start of a new item (e.g., 'barcode pattern', 'PRODUCT_NAME label')",
  "item_end_marker": "description of what marks the end of an item (e.g., 'LINE_TOTAL label')",
  "barcode_pattern": "regex pattern for SKU/barcode if found (e.g., '\\\\d{{12}}')",
  "x_position_zones": {{
    "left": [0.0, 0.3],
    "center": [0.3, 0.6],
    "right": [0.6, 1.0]
  }},
  "label_positions": {{
    "PRODUCT_NAME": "left" or "center" or "right" or "varies",
    "LINE_TOTAL": "left" or "center" or "right",
    "UNIT_PRICE": "left" or "center" or "right" or "not_found",
    "QUANTITY": "left" or "center" or "right" or "varies"
  }},
  "grouping_rule": "plain english description of how to group words into line items",
  "special_markers": ["list of special markers like <A>, *, etc. if found"]
}}

Respond with ONLY the JSON object, no markdown code blocks or other text."""

    return prompt


def discover_patterns_with_llm(prompt: str) -> dict | None:
    """Call LLM to discover patterns."""
    import httpx

    ollama_api_key = os.environ.get("OLLAMA_API_KEY", "")
    ollama_base_url = os.environ.get(
        "OLLAMA_BASE_URL", "https://api.ollama.com"
    )
    ollama_model = os.environ.get("OLLAMA_MODEL", "llama3.1:70b")

    if not ollama_api_key:
        logger.error("OLLAMA_API_KEY not set")
        return None

    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                f"{ollama_base_url}/api/chat",
                headers={
                    "Authorization": f"Bearer {ollama_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": ollama_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a receipt analysis expert. "
                            "Respond only with valid JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
            )
            response.raise_for_status()
            result = response.json()

            # Extract the content
            content = result.get("message", {}).get("content", "")

            # Try to parse as JSON
            # Remove markdown code blocks if present
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            return json.loads(content)

    except json.JSONDecodeError:
        logger.exception("Failed to parse LLM response as JSON")
        logger.debug("Raw content: %s", content[:500])
        return None
    except Exception:
        logger.exception("LLM call failed")
        return None


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Discover line item patterns for a merchant.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Home Depot",
        "force_rediscovery": false  # Optional, defaults to false
    }

    Output:
    {
        "execution_id": "abc123",
        "merchant_name": "Home Depot",
        "patterns_s3_key": "patterns/home_depot.json",
        "patterns": { ... discovered patterns ... },
        "cached": true/false,
        "error": null or "error message"
    }
    """
    from receipt_dynamo import DynamoClient

    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "Unknown")
    force_rediscovery = event.get("force_rediscovery", False)

    if not batch_bucket:
        return {
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "patterns_s3_key": None,
            "patterns": None,
            "cached": False,
            "error": "batch_bucket is required",
        }

    merchant_hash = get_merchant_hash(merchant_name)
    patterns_s3_key = f"patterns/{merchant_hash}.json"

    # Check if patterns already exist
    if not force_rediscovery:
        existing = load_json_from_s3(batch_bucket, patterns_s3_key)
        if existing:
            logger.info(f"Using cached patterns for {merchant_name}")
            return {
                "execution_id": execution_id,
                "merchant_name": merchant_name,
                "patterns_s3_key": patterns_s3_key,
                "patterns": existing,
                "cached": True,
                "error": None,
            }

    # Build receipt structure for analysis
    logger.info(f"Discovering patterns for {merchant_name}")
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable")
    dynamo_client = DynamoClient(table_name=table_name)

    receipts_data = build_receipt_structure(
        dynamo_client, merchant_name, limit=3
    )

    if not receipts_data:
        logger.warning(f"No receipt data found for {merchant_name}")
        # Return default patterns
        default_patterns = {
            "merchant": merchant_name,
            "item_structure": "unknown",
            "lines_per_item": {"typical": 1, "min": 1, "max": 3},
            "item_start_marker": "PRODUCT_NAME label",
            "item_end_marker": "LINE_TOTAL label",
            "barcode_pattern": None,
            "label_positions": {
                "PRODUCT_NAME": "left",
                "LINE_TOTAL": "right",
                "UNIT_PRICE": "right",
                "QUANTITY": "varies",
            },
            "grouping_rule": "Group all words between consecutive LINE_TOTAL labels",
            "special_markers": [],
            "auto_generated": True,
            "reason": "no_receipt_data",
        }
        upload_json_to_s3(batch_bucket, patterns_s3_key, default_patterns)
        return {
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "patterns_s3_key": patterns_s3_key,
            "patterns": default_patterns,
            "cached": False,
            "error": None,
        }

    # Build prompt and call LLM
    prompt = build_discovery_prompt(merchant_name, receipts_data)
    patterns = discover_patterns_with_llm(prompt)

    if not patterns:
        logger.warning(f"LLM pattern discovery failed for {merchant_name}")
        # Return default patterns
        default_patterns = {
            "merchant": merchant_name,
            "item_structure": "unknown",
            "lines_per_item": {"typical": 2, "min": 1, "max": 5},
            "item_start_marker": "PRODUCT_NAME or barcode",
            "item_end_marker": "LINE_TOTAL label",
            "barcode_pattern": r"\d{10,14}",
            "label_positions": {
                "PRODUCT_NAME": "left",
                "LINE_TOTAL": "right",
                "UNIT_PRICE": "right",
                "QUANTITY": "varies",
            },
            "grouping_rule": "Group all words between consecutive LINE_TOTAL labels",
            "special_markers": [],
            "auto_generated": True,
            "reason": "llm_discovery_failed",
        }
        upload_json_to_s3(batch_bucket, patterns_s3_key, default_patterns)
        return {
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "patterns_s3_key": patterns_s3_key,
            "patterns": default_patterns,
            "cached": False,
            "error": "LLM discovery failed, using defaults",
        }

    # Add metadata
    patterns["discovered_from_receipts"] = len(receipts_data)
    patterns["auto_generated"] = False

    # Store patterns
    upload_json_to_s3(batch_bucket, patterns_s3_key, patterns)
    logger.info(f"Stored patterns for {merchant_name} at {patterns_s3_key}")

    return {
        "execution_id": execution_id,
        "merchant_name": merchant_name,
        "patterns_s3_key": patterns_s3_key,
        "patterns": patterns,
        "cached": False,
        "error": None,
    }

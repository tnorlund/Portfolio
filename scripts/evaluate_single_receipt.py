#!/usr/bin/env python3
"""
Evaluate labels for a single receipt.

This dev script runs pattern discovery and optional LLM review for a receipt.
It's useful for testing the pattern discovery and LLM prompts without running
the full step function.

Usage:
    python scripts/evaluate_single_receipt.py <image_id> <receipt_id> [--llm-review] [--verbose]

Examples:
    # Just run pattern discovery
    python scripts/evaluate_single_receipt.py abc123 0

    # Run with LLM review
    python scripts/evaluate_single_receipt.py abc123 0 --llm-review

    # Verbose output with receipt text
    python scripts/evaluate_single_receipt.py abc123 0 --verbose

Requirements:
    pip install receipt-dynamo httpx
"""

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Optional

import httpx

# Add parent directory to path for local imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipt_dynamo import DynamoClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class PatternConfig:
    """Configuration for pattern discovery LLM calls."""
    ollama_base_url: str = "https://ollama.tylernorlund.com"
    ollama_api_key: str = ""
    ollama_model: str = "llama3.2"

    @classmethod
    def from_env(cls) -> "PatternConfig":
        return cls(
            ollama_base_url=os.environ.get(
                "OLLAMA_BASE_URL", "https://ollama.tylernorlund.com"
            ),
            ollama_api_key=os.environ.get("OLLAMA_API_KEY", ""),
            ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
        )


def load_receipt_data(
    dynamo_client: DynamoClient,
    image_id: str,
    receipt_id: int,
) -> dict[str, Any]:
    """Load receipt data from DynamoDB."""
    logger.info(f"Loading receipt data for {image_id}#{receipt_id}")

    words = dynamo_client.list_receipt_words_from_receipt(image_id, receipt_id)
    logger.info(f"  Loaded {len(words)} words")

    labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(image_id, receipt_id)
    logger.info(f"  Loaded {len(labels)} labels")

    place = dynamo_client.get_receipt_place(image_id, receipt_id)
    merchant_name = place.merchant_name if place else "Unknown"
    logger.info(f"  Merchant: {merchant_name}")

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "words": words,
        "labels": labels,
        "place": place,
        "merchant_name": merchant_name,
    }


def build_receipt_structure(
    dynamo_client: DynamoClient,
    merchant_name: str,
    limit: int = 3,
) -> list[dict]:
    """Build receipt structure for pattern discovery."""
    places, _ = dynamo_client.get_receipt_places_by_merchant(merchant_name, limit=limit)

    receipts_data = []
    for place in places[:limit]:
        words = dynamo_client.list_receipt_words_from_receipt(place.image_id, place.receipt_id)
        labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(place.image_id, place.receipt_id)

        if not words:
            continue

        # Build label map
        label_map = defaultdict(list)
        for label in labels:
            label_map[(label.line_id, label.word_id)].append(label.label)

        # Build simplified structure
        lines = defaultdict(list)
        for word in words:
            key = (word.line_id, word.word_id)
            word_labels = label_map.get(key, [])
            lines[word.line_id].append({
                "text": word.text,
                "labels": word_labels,
                "y": word.y_min,
                "x": word.x_min,
            })

        receipts_data.append({
            "image_id": place.image_id,
            "receipt_id": place.receipt_id,
            "lines": dict(lines),
        })

    return receipts_data


def build_discovery_prompt(merchant_name: str, receipts_data: list[dict]) -> str:
    """Build the pattern discovery prompt."""
    # Build simplified receipt text
    simplified = []
    for receipt in receipts_data:
        lines = []
        sorted_line_ids = sorted(
            receipt["lines"].keys(),
            key=lambda lid: min(w["y"] for w in receipt["lines"][lid]),
        )
        for line_id in sorted_line_ids[:50]:
            line_words = sorted(receipt["lines"][line_id], key=lambda w: w["x"])
            parts = []
            for word in line_words:
                if word["labels"]:
                    labels_str = ",".join(word["labels"])
                    parts.append(f"{word['text']}[{labels_str}]")
                else:
                    parts.append(word["text"])
            lines.append(" ".join(parts))
        simplified.append("\n".join(lines))

    receipts_text = "\n\n---\n\n".join(simplified)

    prompt = f"""Analyze the following receipt data from "{merchant_name}".

Each line shows words with [LABELS] for labeled words.

RECEIPT DATA:
{receipts_text}

---

STEP 1: Determine the receipt type:
- ITEMIZED: Multiple products with individual prices (grocery, retail)
- SERVICE: Single charge, non-itemized (medical, parking)

STEP 2: If ITEMIZED, identify the line item patterns.

Respond with ONLY a JSON object:

If SERVICE receipt:
{{
  "merchant": "{merchant_name}",
  "receipt_type": "service",
  "receipt_type_reason": "brief explanation",
  "item_structure": null,
  "lines_per_item": null,
  "item_start_marker": null,
  "item_end_marker": null,
  "grouping_rule": null,
  "label_positions": null
}}

If ITEMIZED receipt:
{{
  "merchant": "{merchant_name}",
  "receipt_type": "itemized",
  "receipt_type_reason": "brief explanation",
  "item_structure": "single-line" or "multi-line",
  "lines_per_item": {{"typical": N, "min": N, "max": N}},
  "item_start_marker": "what marks start of item",
  "item_end_marker": "what marks end of item",
  "grouping_rule": "how to group words into line items",
  "label_positions": {{"PRODUCT_NAME": "left", "LINE_TOTAL": "right", ...}}
}}"""

    return prompt


def discover_patterns(
    dynamo_client: DynamoClient,
    merchant_name: str,
    config: PatternConfig,
    verbose: bool = False,
) -> dict[str, Any]:
    """Discover line item patterns for the merchant."""
    logger.info(f"Discovering patterns for {merchant_name}")

    if not config.ollama_api_key:
        logger.warning("OLLAMA_API_KEY not set, using defaults")
        return get_default_patterns(merchant_name, "no_api_key")

    receipts_data = build_receipt_structure(dynamo_client, merchant_name, limit=3)

    if not receipts_data:
        logger.warning("No receipt data found")
        return get_default_patterns(merchant_name, "no_data")

    prompt = build_discovery_prompt(merchant_name, receipts_data)

    if verbose:
        logger.info(f"Pattern prompt:\n{prompt[:800]}...")

    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                f"{config.ollama_base_url}/api/chat",
                headers={
                    "Authorization": f"Bearer {config.ollama_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.ollama_model,
                    "messages": [
                        {"role": "system", "content": "You are a receipt analysis expert. Respond only with valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
            )
            response.raise_for_status()
            result = response.json()

        content = result.get("message", {}).get("content", "")

        # Parse JSON from response
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            patterns = json.loads(json_match.group())
            patterns["discovered_from_receipts"] = len(receipts_data)
            patterns["auto_generated"] = False
            logger.info(f"  Receipt type: {patterns.get('receipt_type', 'unknown')}")
            logger.info(f"  Item structure: {patterns.get('item_structure', 'unknown')}")
            return patterns

    except Exception as e:
        logger.exception(f"Pattern discovery failed: {e}")

    return get_default_patterns(merchant_name, "llm_failed")


def get_default_patterns(merchant_name: str, reason: str) -> dict[str, Any]:
    """Return default patterns."""
    return {
        "merchant": merchant_name,
        "receipt_type": "unknown",
        "receipt_type_reason": reason,
        "item_structure": "unknown",
        "lines_per_item": {"typical": 2, "min": 1, "max": 5},
        "item_start_marker": "PRODUCT_NAME or barcode",
        "item_end_marker": "LINE_TOTAL label",
        "grouping_rule": "Group words between LINE_TOTAL labels",
        "label_positions": {"PRODUCT_NAME": "left", "LINE_TOTAL": "right"},
        "auto_generated": True,
    }


def format_patterns(patterns: dict[str, Any]) -> str:
    """Format patterns for display."""
    lines = []
    lines.append(f"**Merchant**: {patterns.get('merchant', 'Unknown')}")

    receipt_type = patterns.get("receipt_type")
    if receipt_type:
        lines.append(f"**Receipt Type**: {receipt_type}")
        if patterns.get("receipt_type_reason"):
            lines.append(f"**Reason**: {patterns['receipt_type_reason']}")

    if receipt_type == "service":
        return "\n".join(lines)

    if patterns.get("item_structure"):
        lines.append(f"**Item Structure**: {patterns['item_structure']}")

    lpi = patterns.get("lines_per_item")
    if lpi and isinstance(lpi, dict):
        lines.append(f"**Lines per Item**: typical={lpi.get('typical')}, range=[{lpi.get('min')}, {lpi.get('max')}]")

    if patterns.get("item_start_marker"):
        lines.append(f"**Item Start**: {patterns['item_start_marker']}")

    if patterns.get("item_end_marker"):
        lines.append(f"**Item End**: {patterns['item_end_marker']}")

    if patterns.get("grouping_rule"):
        lines.append(f"**Grouping Rule**: {patterns['grouping_rule']}")

    label_positions = patterns.get("label_positions")
    if label_positions and isinstance(label_positions, dict):
        lines.append("**Label Positions**:")
        for label, pos in label_positions.items():
            if pos:
                lines.append(f"  - {label}: {pos}")

    return "\n".join(lines)


def assemble_receipt_text(
    words: list,
    labels: list,
    max_lines: int = 60,
) -> str:
    """Assemble receipt text for display."""
    label_map = defaultdict(list)
    for label in labels:
        label_map[(label.line_id, label.word_id)].append(label.label)

    lines_dict = defaultdict(list)
    for word in words:
        lines_dict[word.line_id].append(word)

    sorted_line_ids = sorted(
        lines_dict.keys(),
        key=lambda lid: min(w.y_min for w in lines_dict[lid]),
    )

    result_lines = []
    for line_id in sorted_line_ids[:max_lines]:
        line_words = sorted(lines_dict[line_id], key=lambda w: w.x_min)
        parts = []
        for word in line_words:
            key = (word.line_id, word.word_id)
            if key in label_map:
                labels_str = ",".join(label_map[key])
                parts.append(f"{word.text}[{labels_str}]")
            else:
                parts.append(word.text)
        result_lines.append(" ".join(parts))

    return "\n".join(result_lines)


def find_potential_issues(words: list, labels: list) -> list[dict]:
    """Find potential labeling issues (simplified heuristics)."""
    issues = []

    label_map = {}
    for label in labels:
        key = (label.line_id, label.word_id)
        if key not in label_map:
            label_map[key] = []
        label_map[key].append(label)

    for word in words:
        key = (word.line_id, word.word_id)
        if label_map.get(key):
            continue

        text = word.text.strip()
        if len(text) >= 3 and text.isupper() and not text.isdigit() and not text.startswith("$"):
            issues.append({
                "type": "potential_unlabeled_product",
                "line_id": word.line_id,
                "word_id": word.word_id,
                "word_text": text,
                "current_label": None,
                "reasoning": "Unlabeled uppercase word that may be a product name",
            })

    return issues[:10]


def print_results(
    receipt_data: dict[str, Any],
    patterns: dict[str, Any],
    issues: list[dict],
    verbose: bool = False,
):
    """Print results."""
    print("\n" + "=" * 60)
    print("LABEL EVALUATION RESULTS")
    print("=" * 60)

    print(f"\nReceipt: {receipt_data['image_id']}#{receipt_data['receipt_id']}")
    print(f"Merchant: {receipt_data['merchant_name']}")
    print(f"Words: {len(receipt_data['words'])}")
    print(f"Labels: {len(receipt_data['labels'])}")

    print("\n--- Pattern Discovery ---")
    print(format_patterns(patterns))

    if verbose:
        print("\n--- Receipt Text (first 40 lines) ---")
        receipt_text = assemble_receipt_text(
            receipt_data["words"],
            receipt_data["labels"],
            max_lines=40,
        )
        print(receipt_text)

    print(f"\n--- Potential Issues: {len(issues)} ---")
    for issue in issues[:15]:
        print(f"\n  [{issue.get('type', 'unknown')}]")
        print(f"    Word: \"{issue.get('word_text', '')}\"")
        print(f"    Reason: {issue.get('reasoning', '')[:80]}")

    print("\n" + "=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Evaluate labels for a single receipt")
    parser.add_argument("image_id", help="The image ID")
    parser.add_argument("receipt_id", type=int, help="The receipt ID (0, 1, 2, ...)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--table-name", default=None, help="DynamoDB table name")
    parser.add_argument("--skip-patterns", action="store_true", help="Skip pattern discovery")
    parser.add_argument("--output-json", type=str, default=None, help="Output to JSON file")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    table_name = args.table_name or os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    dynamo_client = DynamoClient(table_name=table_name)
    config = PatternConfig.from_env()

    # 1. Load receipt data
    receipt_data = load_receipt_data(dynamo_client, args.image_id, args.receipt_id)

    # 2. Discover patterns
    if args.skip_patterns:
        patterns = get_default_patterns(receipt_data["merchant_name"], "skipped")
    else:
        patterns = discover_patterns(dynamo_client, receipt_data["merchant_name"], config, args.verbose)

    # 3. Find potential issues
    issues = find_potential_issues(receipt_data["words"], receipt_data["labels"])
    logger.info(f"Found {len(issues)} potential issues")

    # 4. Print results
    print_results(receipt_data, patterns, issues, args.verbose)

    # 5. Optional JSON output
    if args.output_json:
        output = {
            "image_id": args.image_id,
            "receipt_id": args.receipt_id,
            "merchant_name": receipt_data["merchant_name"],
            "patterns": patterns,
            "issues": issues,
        }
        with open(args.output_json, "w") as f:
            json.dump(output, f, indent=2, default=str)
        logger.info(f"Results saved to {args.output_json}")


if __name__ == "__main__":
    main()

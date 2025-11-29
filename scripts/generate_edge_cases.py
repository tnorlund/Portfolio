#!/usr/bin/env python3
"""
Generate edge cases from invalid labels in DynamoDB.

This script analyzes all INVALID labels to identify common invalid word/label
combinations that can be used to avoid expensive LLM validation calls.

Usage:
    # Generate edge cases for all labels
    python scripts/generate_edge_cases.py

    # Generate for specific label type
    python scripts/generate_edge_cases.py --label-type PHONE_NUMBER

    # Custom minimum occurrences threshold
    python scripts/generate_edge_cases.py --min-occurrences 5

    # Custom output file
    python scripts/generate_edge_cases.py --output edge_cases.json
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus

try:
    from receipt_label.constants import CORE_LABELS
except ImportError:
    # Fallback if receipt_label is not available
    CORE_LABELS = {
        "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
        "STORE_HOURS": "Printed business hours or opening times for the merchant.",
        "PHONE_NUMBER": "Telephone number printed on the receipt (store's main line).",
        "WEBSITE": "Web or email address printed on the receipt.",
        "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
        "ADDRESS_LINE": "Full address line (street + city etc.) printed on the receipt.",
        "DATE": "Calendar date of the transaction.",
        "TIME": "Time of the transaction.",
        "PAYMENT_METHOD": "Payment instrument summary (e.g., VISA ••••1234, CASH).",
        "COUPON": "Coupon code or description that reduces price.",
        "DISCOUNT": "Any non-coupon discount line item.",
        "PRODUCT_NAME": "Descriptive text of a purchased product (item name).",
        "QUANTITY": "Numeric count or weight of the item.",
        "UNIT_PRICE": "Price per single unit / weight before tax.",
        "LINE_TOTAL": "Extended price for that line (quantity x unit price).",
        "SUBTOTAL": "Sum of all line totals before tax and discounts.",
        "TAX": "Any tax line (sales tax, VAT, bottle deposit).",
        "GRAND_TOTAL": "Final amount due after all discounts, taxes and fees.",
    }

try:
    from receipt_agent.config.settings import get_settings
except ImportError:
    print("Error: receipt_agent package not found. Install dependencies first.")
    sys.exit(1)


class EdgeCaseGenerator:
    """Generate edge cases from invalid labels in DynamoDB."""

    def __init__(self, dynamo_client: DynamoClient):
        self.dynamo = dynamo_client
        self.min_occurrences = 5  # Conservative default during harmonization
        self.min_merchant_occurrences = 3  # Higher threshold for merchant-specific

    def generate_edge_cases(
        self,
        label_type: Optional[str] = None,
        min_occurrences: int = 3,
    ) -> Dict[str, List[Dict]]:
        """
        Generate edge cases from all INVALID labels.

        Args:
            label_type: Optional filter to only process one CORE_LABEL
            min_occurrences: Minimum occurrences to consider an edge case

        Returns:
            Dict mapping label_type -> list of edge case candidates
        """
        self.min_occurrences = min_occurrences

        print("Phase 1: Collecting invalid labels...")
        invalid_labels = self._collect_invalid_labels(label_type)
        print(f"  Found {len(invalid_labels)} invalid labels")

        if not invalid_labels:
            print("No invalid labels found. Nothing to generate.")
            return {}

        print("\nPhase 2: Fetching word texts and merchant names...")
        enriched_labels = self._enrich_labels(invalid_labels)
        print(f"  Enriched {len(enriched_labels)} labels")

        print("\nPhase 3: Identifying patterns...")
        patterns = self._identify_patterns(enriched_labels)

        print("\nPhase 4: Generating edge cases...")
        edge_cases = self._generate_edge_cases(patterns)

        return edge_cases

    def _collect_invalid_labels(
        self, label_type: Optional[str] = None
    ) -> List:
        """Query all INVALID labels from DynamoDB."""
        from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

        all_invalid = []
        last_key = None

        while True:
            batch, last_key = self.dynamo.list_receipt_word_labels_with_status(
                status=ValidationStatus.INVALID,
                limit=1000,
                last_evaluated_key=last_key,
            )

            # Filter by label_type if specified
            if label_type:
                batch = [
                    l for l in batch if l.label == label_type.upper()
                ]

            all_invalid.extend(batch)
            print(f"  Loaded {len(all_invalid)} invalid labels...", end="\r")

            if not last_key:
                break

        print()  # New line after progress
        return all_invalid

    def _enrich_labels(self, labels: List) -> List[Dict]:
        """
        Enrich labels with word_text and merchant_name.

        Uses batch fetching for efficiency.
        """
        enriched = []

        # Collect unique keys for batch fetching
        word_keys = set(
            (l.image_id, l.receipt_id, l.line_id, l.word_id) for l in labels
        )
        receipt_keys = set((l.image_id, l.receipt_id) for l in labels)

        # Batch fetch words
        print("  Fetching word texts...")
        words_by_key = {}
        words_found = 0
        words_missing = 0

        for image_id, receipt_id, line_id, word_id in word_keys:
            try:
                word = self.dynamo.get_receipt_word(
                    receipt_id=receipt_id,
                    image_id=image_id,
                    line_id=line_id,
                    word_id=word_id,
                )
                if word:
                    words_by_key[(image_id, receipt_id, line_id, word_id)] = (
                        word.text
                    )
                    words_found += 1
                else:
                    words_missing += 1
            except Exception as e:
                words_missing += 1
                if words_missing <= 5:  # Only print first few errors
                    print(f"    Warning: Could not get word {image_id}#{receipt_id}#{line_id}#{word_id}: {e}")

        print(f"    Found {words_found} words, {words_missing} missing")

        # Batch fetch merchants
        print("  Fetching merchant names...")
        merchants_by_key = {}
        merchants_found = 0
        merchants_missing = 0

        for image_id, receipt_id in receipt_keys:
            try:
                metadata = self.dynamo.get_receipt_metadata(image_id, receipt_id)
                if metadata and metadata.merchant_name:
                    merchants_by_key[(image_id, receipt_id)] = (
                        metadata.merchant_name
                    )
                    merchants_found += 1
                else:
                    merchants_missing += 1
            except Exception as e:
                merchants_missing += 1
                if merchants_missing <= 5:  # Only print first few errors
                    print(f"    Warning: Could not get metadata {image_id}#{receipt_id}: {e}")

        print(f"    Found {merchants_found} merchants, {merchants_missing} missing")

        # Combine into enriched labels
        for label in labels:
            word_key = (
                label.image_id,
                label.receipt_id,
                label.line_id,
                label.word_id,
            )
            receipt_key = (label.image_id, label.receipt_id)

            enriched.append(
                {
                    "label": label,
                    "word_text": words_by_key.get(word_key, ""),
                    "merchant_name": merchants_by_key.get(receipt_key),
                    "label_type": label.label,
                }
            )

        return enriched

    def _identify_patterns(self, enriched_labels: List[Dict]) -> Dict[str, Dict]:
        """
        Identify patterns in invalid labels.

        Groups by:
        1. Global patterns: (label_type, word_text) - no merchant
        2. Merchant-specific: (label_type, merchant_name, word_text)

        Returns:
            Dict with structure:
            {
                "PHONE_NUMBER": {
                    "global": {
                        "Main:": {"count": 15, "examples": [...]},
                    },
                    "merchant_specific": {
                        "Vons": {
                            "VONS.": {"count": 5, "examples": [...]},
                        }
                    }
                }
            }
        """
        patterns = defaultdict(
            lambda: {
                "global": defaultdict(lambda: {"count": 0, "examples": []}),
                "merchant_specific": defaultdict(
                    lambda: defaultdict(lambda: {"count": 0, "examples": []})
                ),
            }
        )

        for item in enriched_labels:
            label_type = item["label_type"]
            word_text = item["word_text"].strip() if item["word_text"] else ""
            merchant_name = item["merchant_name"]

            if not word_text:
                continue

            # Normalize word for grouping
            normalized_word = word_text.upper()

            # Add to global pattern
            patterns[label_type]["global"][normalized_word]["count"] += 1
            if len(patterns[label_type]["global"][normalized_word]["examples"]) < 5:
                patterns[label_type]["global"][normalized_word]["examples"].append(
                    {
                        "word_text": word_text,
                        "merchant_name": merchant_name,
                        "image_id": item["label"].image_id,
                        "reasoning": item["label"].reasoning,
                    }
                )

            # Add to merchant-specific pattern if merchant exists
            if merchant_name:
                patterns[label_type]["merchant_specific"][merchant_name][
                    normalized_word
                ]["count"] += 1
                if (
                    len(
                        patterns[label_type]["merchant_specific"][merchant_name][
                            normalized_word
                        ]["examples"]
                    )
                    < 5
                ):
                    patterns[label_type]["merchant_specific"][merchant_name][
                        normalized_word
                    ]["examples"].append(
                        {
                            "word_text": word_text,
                            "image_id": item["label"].image_id,
                            "reasoning": item["label"].reasoning,
                        }
                    )

        return dict(patterns)

    def _generate_edge_cases(
        self, patterns: Dict[str, Dict]
    ) -> Dict[str, List[Dict]]:
        """
        Generate edge case candidates from patterns.

        Strategy:
        1. Global patterns with >= min_occurrences → global edge case
        2. Merchant-specific patterns with >= min_merchant_occurrences → merchant-specific edge case
        3. If same pattern appears for multiple merchants → consider making it global
        """
        edge_cases = defaultdict(list)

        for label_type, pattern_data in patterns.items():
            if label_type not in CORE_LABELS:
                continue

            # Process global patterns
            for word_text, data in pattern_data["global"].items():
                if data["count"] >= self.min_occurrences:
                    # Check if this appears merchant-specific too
                    merchant_appearances = sum(
                        1
                        for merchant_data in pattern_data[
                            "merchant_specific"
                        ].values()
                        if word_text in merchant_data
                        and merchant_data[word_text]["count"] > 0
                    )

                    # If appears in many merchants, it's truly global
                    if merchant_appearances >= 3:
                        edge_cases[label_type].append(
                            {
                                "word_text": data["examples"][0]["word_text"]
                                if data["examples"]
                                else word_text,  # Original case
                                "normalized_word": word_text,
                                "merchant_name": None,  # Global
                                "match_type": "exact",
                                "count": data["count"],
                                "merchant_appearances": merchant_appearances,
                                "examples": data["examples"],
                                "reason": self._generate_reason(
                                    label_type, word_text, data["examples"]
                                ),
                            }
                        )

            # Process merchant-specific patterns
            for (
                merchant_name,
                merchant_patterns,
            ) in pattern_data["merchant_specific"].items():
                for word_text, data in merchant_patterns.items():
                    # Only add if it's NOT already a global pattern
                    global_count = pattern_data["global"].get(word_text, {}).get(
                        "count", 0
                    )

                    if (
                        data["count"] >= self.min_merchant_occurrences
                        and global_count < self.min_occurrences
                    ):
                        edge_cases[label_type].append(
                            {
                                "word_text": data["examples"][0]["word_text"]
                                if data["examples"]
                                else word_text,
                                "normalized_word": word_text,
                                "merchant_name": merchant_name,
                                "match_type": "exact",
                                "count": data["count"],
                                "examples": data["examples"],
                                "reason": self._generate_reason(
                                    label_type,
                                    word_text,
                                    data["examples"],
                                    merchant_name,
                                ),
                            }
                        )

        return dict(edge_cases)

    def _generate_reason(
        self,
        label_type: str,
        word_text: str,
        examples: List[Dict],
        merchant_name: Optional[str] = None,
    ) -> str:
        """Generate a human-readable reason for why this is an edge case."""
        # Analyze examples to infer reason
        word_lower = word_text.lower()

        # Common patterns
        if ":" in word_text:
            return (
                f"'{word_text}' is a text prefix/label (contains colon), "
                f"not a {label_type} value"
            )
        elif word_text in [
            "STORE",
            "MAIN",
            "PHONE",
            "ADDRESS",
            "DATE",
            "TIME",
            "TOTAL",
            "SUBTOTAL",
        ]:
            return (
                f"'{word_text}' is a common header word, not a {label_type} value"
            )
        elif not any(c.isalnum() for c in word_text):
            return (
                f"'{word_text}' is punctuation-only, not a valid {label_type}"
            )
        elif merchant_name:
            return (
                f"'{word_text}' is an invalid {label_type} pattern specific "
                f"to {merchant_name}"
            )
        else:
            return (
                f"'{word_text}' is a common invalid {label_type} pattern "
                f"(appears {len(examples)}+ times as INVALID)"
            )

    def generate_pattern_based_edge_cases(
        self, edge_cases: Dict[str, List[Dict]]
    ) -> Dict[str, List[Dict]]:
        """
        Generate pattern-based edge cases (prefixes, suffixes) from exact matches.

        Example: If "Main:", "Phone:", "Tel:" all appear → create prefix pattern ".*:"
        """
        import re

        pattern_edge_cases = defaultdict(list)

        for label_type, cases in edge_cases.items():
            # Group by pattern type
            colon_suffixes = [c for c in cases if ":" in c["word_text"]]
            if len(colon_suffixes) >= 3:
                # Create regex pattern
                pattern_edge_cases[label_type].append(
                    {
                        "word_text": None,
                        "pattern": r".*:",
                        "match_type": "regex",
                        "merchant_name": None,
                        "count": sum(c["count"] for c in colon_suffixes),
                        "reason": (
                            f"Words ending with ':' are typically labels/headers, "
                            f"not {label_type} values"
                        ),
                        "derived_from": [
                            c["word_text"] for c in colon_suffixes[:5]
                        ],
                    }
                )

        return dict(pattern_edge_cases)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate edge cases from invalid labels in DynamoDB"
    )
    parser.add_argument(
        "--label-type",
        type=str,
        help="Optional filter for specific CORE_LABEL (e.g., PHONE_NUMBER)",
    )
    parser.add_argument(
        "--min-occurrences",
        type=int,
        default=5,
        help="Minimum occurrences to consider an edge case (default: 5, recommended: 10 during active harmonization)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="edge_case_candidates.json",
        help="Output JSON file path (default: edge_case_candidates.json)",
    )
    parser.add_argument(
        "--include-patterns",
        action="store_true",
        help="Include pattern-based edge cases (regex patterns)",
    )

    args = parser.parse_args()

    # Initialize DynamoDB client
    try:
        # Try to get table name from Pulumi outputs first
        from receipt_dynamo.data._pulumi import load_env

        # Try dev first, then prod
        pulumi_outputs = load_env("dev") or load_env("prod")

        if pulumi_outputs and "dynamodb_table_name" in pulumi_outputs:
            table_name = pulumi_outputs["dynamodb_table_name"]
            # Extract region from ARN if available, otherwise use region output
            if "dynamodb_table_arn" in pulumi_outputs:
                arn = pulumi_outputs["dynamodb_table_arn"]
                # ARN format: arn:aws:dynamodb:REGION:ACCOUNT:table/NAME
                region = arn.split(":")[3] if ":" in arn else pulumi_outputs.get("region", "us-east-1")
            else:
                region = pulumi_outputs.get("region", "us-east-1")
            print(f"Using table from Pulumi: {table_name} in {region}")
        else:
            # Fall back to settings
            settings = get_settings()
            table_name = settings.dynamo_table_name
            region = settings.aws_region
            print(f"Using table from settings: {table_name} in {region}")

        dynamo = DynamoClient(table_name=table_name, region=region)
    except Exception as e:
        print(f"Error initializing DynamoDB client: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Generate edge cases
    generator = EdgeCaseGenerator(dynamo)

        print("=" * 60)
    print("Edge Case Generator")
    print("=" * 60)
    if args.label_type:
        print(f"Label Type: {args.label_type}")
    print(f"Min Occurrences: {args.min_occurrences}")
    print(f"Output: {args.output}")
    print()
    print("⚠️  WARNING: During active harmonization, use high thresholds (10+)")
    print("   and review all candidates before loading into DynamoDB.")
    print("   See docs/EDGE_CASE_BEST_PRACTICES.md for guidelines.")
    print("=" * 60)
    print()

    try:
        edge_cases = generator.generate_edge_cases(
            label_type=args.label_type, min_occurrences=args.min_occurrences
        )

        # Generate pattern-based edge cases if requested
        pattern_cases = {}
        if args.include_patterns:
            print("\nPhase 5: Generating pattern-based edge cases...")
            pattern_cases = generator.generate_pattern_based_edge_cases(edge_cases)

        # Summary
        print("\n" + "=" * 60)
        print("Summary")
        print("=" * 60)
        total_cases = sum(len(cases) for cases in edge_cases.values())
        total_patterns = sum(len(cases) for cases in pattern_cases.values())
        print(f"Total edge cases generated: {total_cases}")
        print(f"Total pattern-based cases: {total_patterns}")

        for label_type, cases in edge_cases.items():
            print(f"  {label_type}: {len(cases)} cases")

        # Save to JSON
        output_data = {
            "generated_at": datetime.now().isoformat(),
            "min_occurrences": args.min_occurrences,
            "label_type_filter": args.label_type,
            "exact_matches": edge_cases,
            "patterns": pattern_cases,
        }

        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2, default=str)

        print(f"\nResults saved to {args.output}")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError generating edge cases: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()


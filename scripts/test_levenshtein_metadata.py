#!/usr/bin/env python3
"""
Dev script to measure how many LLM-bound metadata words could be resolved
by fuzzy (Levenshtein / SequenceMatcher) matching against Google Places data.

The current auto-resolve layer uses exact substring matching
(``word_norm in merchant_norm``).  OCR-corrupted text like "Bivd" (Blvd),
"OArS" (Oaks), or "THILLAND" (Thousand) fails exact matching and falls
through to the LLM.

This script:
1. Loads receipts that have Google Places data from DynamoDB
2. Runs the existing auto-resolve pipeline to find what falls through
3. Applies SequenceMatcher fuzzy matching on the unresolved words
4. Reports how many could be caught at various thresholds

Usage:
    python scripts/test_levenshtein_metadata.py [--limit N] [--threshold 0.8] [-v]
"""

import argparse
import logging
import os
import sys
import types
import unittest.mock
from collections import defaultdict
from difflib import SequenceMatcher

# Add project root and receipt_agent package to path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(_project_root, "receipt_agent"))

# Mock receipt_chroma to avoid chromadb import (incompatible with Python 3.14)
_mock_chroma = unittest.mock.MagicMock()
for _mod in [
    "receipt_chroma", "receipt_chroma.s3", "receipt_chroma.data",
    "receipt_chroma.data.chroma_client", "receipt_chroma.compaction",
    "receipt_chroma.compaction.deletions",
]:
    sys.modules[_mod] = _mock_chroma


def fuzzy_match_place(word_text: str, place, threshold: float) -> tuple[str | None, str | None, float]:
    """Check if word fuzzy-matches any ReceiptPlace field.

    Returns (matching_label, matched_token, ratio) or (None, None, 0.0).
    """
    from receipt_agent.agents.label_evaluator.metadata_subagent import (
        normalize_text,
    )

    word_norm = normalize_text(word_text)
    if len(word_norm) < 3:
        return None, None, 0.0

    best_label = None
    best_token = None
    best_ratio = 0.0

    # Check merchant name (word-by-word)
    merchant = getattr(place, "merchant_name", "") or ""
    if merchant:
        for part in normalize_text(merchant).split():
            if len(part) < 3:
                continue
            ratio = SequenceMatcher(None, word_norm, part).ratio()
            if ratio >= threshold and ratio > best_ratio:
                best_label = "MERCHANT_NAME"
                best_token = part
                best_ratio = ratio

    # Check address (word-by-word against full address + components)
    address = getattr(place, "formatted_address", "") or ""
    if address:
        for part in normalize_text(address).split():
            if len(part) < 3:
                continue
            ratio = SequenceMatcher(None, word_norm, part).ratio()
            if ratio >= threshold and ratio > best_ratio:
                best_label = "ADDRESS_LINE"
                best_token = part
                best_ratio = ratio

    # Also check address_components values
    components = getattr(place, "address_components", {}) or {}
    for key in ["route", "locality", "administrative_area_level_1"]:
        val = components.get(key, "")
        if not val:
            continue
        for part in normalize_text(str(val)).split():
            if len(part) < 3:
                continue
            ratio = SequenceMatcher(None, word_norm, part).ratio()
            if ratio >= threshold and ratio > best_ratio:
                best_label = "ADDRESS_LINE"
                best_token = part
                best_ratio = ratio

    # Check hours summary entries
    hours = getattr(place, "hours_summary", []) or []
    for entry in hours:
        for part in normalize_text(str(entry)).split():
            if len(part) < 3:
                continue
            ratio = SequenceMatcher(None, word_norm, part).ratio()
            if ratio >= threshold and ratio > best_ratio:
                best_label = "STORE_HOURS"
                best_token = part
                best_ratio = ratio

    return best_label, best_token, best_ratio


def main():
    parser = argparse.ArgumentParser(
        description="Test Levenshtein/SequenceMatcher fuzzy matching for metadata words"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Number of receipt places to process (0 for all, default: 100)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="Similarity threshold (default: 0.8)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output showing individual word matches",
    )
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

    # Import after path setup
    from receipt_agent.agents.label_evaluator.metadata_subagent import (
        auto_resolve_metadata_words,
        collect_metadata_words,
        normalize_text,
    )
    from receipt_agent.agents.label_evaluator.word_context import (
        assemble_visual_lines,
        build_word_contexts,
    )
    from receipt_dynamo import DynamoClient

    # Get configuration from environment
    table_name = os.environ.get(
        "RECEIPT_AGENT_DYNAMO_TABLE_NAME", "ReceiptsTable-dc5be22"
    )
    dynamo_client = DynamoClient(table_name=table_name)

    # =========================================================================
    # Step 1: Load receipts with Places data
    # =========================================================================
    logger.info("Loading receipt places (limit=%s)...", args.limit or "all")

    all_places = []
    last_key = None
    while True:
        batch_limit = args.limit - len(all_places) if args.limit else None
        places, last_key = dynamo_client.list_receipt_places(
            limit=batch_limit, last_evaluated_key=last_key
        )
        all_places.extend(places)
        if not last_key or (args.limit and len(all_places) >= args.limit):
            break

    logger.info("Loaded %d receipt places", len(all_places))

    # =========================================================================
    # Step 2: Process each receipt
    # =========================================================================
    thresholds = [0.7, 0.8, 0.9] if args.threshold == 0.8 else [args.threshold]

    # Accumulators
    total_receipts = 0
    total_receipts_with_words = 0
    total_metadata_words = 0
    total_resolved = 0
    total_unresolved = 0
    skipped_no_words = 0

    # Per-threshold accumulators
    fuzzy_matches = {t: 0 for t in thresholds}
    fuzzy_by_label = {t: defaultdict(int) for t in thresholds}
    fuzzy_agrees_with_current = {t: 0 for t in thresholds}
    fuzzy_disagrees_with_current = {t: 0 for t in thresholds}
    fuzzy_fills_unlabeled = {t: 0 for t in thresholds}

    # Per-label breakdown of unresolved words
    unresolved_by_label = defaultdict(int)

    # Collect examples for reporting
    match_examples = {t: [] for t in thresholds}
    disagree_examples = {t: [] for t in thresholds}

    for i, place in enumerate(all_places):
        total_receipts += 1
        if (i + 1) % 50 == 0:
            logger.info("Processing receipt %d/%d...", i + 1, len(all_places))

        # Load words and labels
        try:
            words = dynamo_client.list_receipt_words_from_receipt(
                place.image_id, place.receipt_id
            )
        except Exception as e:
            logger.debug("Failed to load words for %s#%s: %s", place.image_id, place.receipt_id, e)
            skipped_no_words += 1
            continue

        if not words:
            skipped_no_words += 1
            continue

        labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(
            place.image_id, place.receipt_id
        )

        # Build visual lines
        word_contexts = build_word_contexts(words, labels)
        visual_lines = assemble_visual_lines(word_contexts)

        # Collect metadata words
        metadata_words, _ = collect_metadata_words(visual_lines, place)
        if not metadata_words:
            skipped_no_words += 1
            continue

        total_receipts_with_words += 1
        total_metadata_words += len(metadata_words)

        # Auto-resolve
        resolved, unresolved = auto_resolve_metadata_words(metadata_words)
        total_resolved += len(resolved)
        total_unresolved += len(unresolved)

        for mw in unresolved:
            label_key = mw.current_label or "unlabeled"
            unresolved_by_label[label_key] += 1

        # Fuzzy match unresolved words at each threshold
        for threshold in thresholds:
            for mw in unresolved:
                word_text = mw.word_context.word.text
                fuzzy_label, matched_token, ratio = fuzzy_match_place(
                    word_text, place, threshold
                )

                if fuzzy_label is None:
                    continue

                fuzzy_matches[threshold] += 1
                fuzzy_by_label[threshold][fuzzy_label] += 1

                current = mw.current_label
                word_norm = normalize_text(word_text)

                if current is None:
                    fuzzy_fills_unlabeled[threshold] += 1
                elif current == fuzzy_label:
                    fuzzy_agrees_with_current[threshold] += 1
                else:
                    fuzzy_disagrees_with_current[threshold] += 1
                    if len(disagree_examples[threshold]) < 20:
                        disagree_examples[threshold].append({
                            "word": word_text,
                            "word_norm": word_norm,
                            "current_label": current,
                            "fuzzy_label": fuzzy_label,
                            "matched_token": matched_token,
                            "ratio": ratio,
                            "merchant": place.merchant_name,
                            "address": place.formatted_address,
                        })

                if len(match_examples[threshold]) < 30:
                    match_examples[threshold].append({
                        "word": word_text,
                        "word_norm": word_norm,
                        "current_label": current or "unlabeled",
                        "fuzzy_label": fuzzy_label,
                        "matched_token": matched_token,
                        "ratio": ratio,
                        "merchant": place.merchant_name,
                    })

                if args.verbose:
                    marker = "=" if current == fuzzy_label else ("+" if current is None else "!")
                    print(
                        f"  [{marker}] t={threshold:.1f} "
                        f"\"{word_text}\" ~{ratio:.3f}~ \"{matched_token}\" "
                        f"-> {fuzzy_label}  (current: {current or 'unlabeled'})"
                    )

    # =========================================================================
    # Step 3: Report
    # =========================================================================
    print("\n" + "=" * 70)
    print("LEVENSHTEIN / SEQUENCE MATCHER EXPERIMENT")
    print("=" * 70)

    print(f"\nReceipts loaded:           {total_receipts}")
    print(f"Receipts with metadata:    {total_receipts_with_words}")
    print(f"Receipts skipped:          {skipped_no_words}")
    print(f"Total metadata words:      {total_metadata_words}")
    print(f"Auto-resolved (existing):  {total_resolved} ({total_resolved / total_metadata_words * 100:.1f}%)" if total_metadata_words else "")
    print(f"Unresolved (LLM-bound):    {total_unresolved} ({total_unresolved / total_metadata_words * 100:.1f}%)" if total_metadata_words else "")

    print("\n--- Unresolved by Label ---")
    for label, count in sorted(unresolved_by_label.items(), key=lambda x: -x[1]):
        pct = count / total_unresolved * 100 if total_unresolved else 0
        print(f"  {label:25s}  {count:5d}  ({pct:.1f}%)")

    for threshold in thresholds:
        matches = fuzzy_matches[threshold]
        print(f"\n{'=' * 70}")
        print(f"THRESHOLD = {threshold}")
        print(f"{'=' * 70}")
        print(f"Fuzzy matches:             {matches} / {total_unresolved} unresolved "
              f"({matches / total_unresolved * 100:.1f}%)" if total_unresolved else "")
        print(f"  Agrees with current label:   {fuzzy_agrees_with_current[threshold]}")
        print(f"  Fills unlabeled word:        {fuzzy_fills_unlabeled[threshold]}")
        print(f"  Disagrees with current:      {fuzzy_disagrees_with_current[threshold]}  <-- potential false positives")

        if fuzzy_by_label[threshold]:
            print(f"\n  Fuzzy matches by suggested label:")
            for label, count in sorted(fuzzy_by_label[threshold].items(), key=lambda x: -x[1]):
                print(f"    {label:25s}  {count:5d}")

        # Show examples
        examples = match_examples[threshold]
        if examples:
            print(f"\n  Sample matches (up to 15):")
            for ex in examples[:15]:
                marker = "=" if ex["current_label"] == ex["fuzzy_label"] else (
                    "+" if ex["current_label"] == "unlabeled" else "!"
                )
                print(
                    f"    [{marker}] \"{ex['word']}\" ~{ex['ratio']:.3f}~ "
                    f"\"{ex['matched_token']}\" -> {ex['fuzzy_label']}  "
                    f"(was: {ex['current_label']}, merchant: {ex['merchant']})"
                )

        disagrees = disagree_examples[threshold]
        if disagrees:
            print(f"\n  False positive examples (fuzzy != current label):")
            for ex in disagrees[:10]:
                print(
                    f"    \"{ex['word']}\" ~{ex['ratio']:.3f}~ "
                    f"\"{ex['matched_token']}\" -> {ex['fuzzy_label']}  "
                    f"(current: {ex['current_label']}, merchant: {ex['merchant']})"
                )

    # Summary recommendation
    if len(thresholds) > 1 and total_unresolved:
        print(f"\n{'=' * 70}")
        print("SUMMARY")
        print(f"{'=' * 70}")
        print(f"{'Threshold':>10s}  {'Matches':>8s}  {'% of LLM':>8s}  {'Agrees':>8s}  {'Disagrees':>10s}")
        for t in thresholds:
            m = fuzzy_matches[t]
            print(
                f"{t:10.1f}  {m:8d}  {m / total_unresolved * 100:7.1f}%  "
                f"{fuzzy_agrees_with_current[t]:8d}  {fuzzy_disagrees_with_current[t]:10d}"
            )

    print()


if __name__ == "__main__":
    main()

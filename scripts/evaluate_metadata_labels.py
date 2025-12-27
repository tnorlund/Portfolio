#!/usr/bin/env python3
"""
Dev script to evaluate metadata labels on a single receipt.

This mirrors the Step Function flow for local testing:
1. FetchReceiptData - Load words, labels, place from DynamoDB
2. EvaluateMetadataLabels - LLM validates metadata labels against ReceiptPlace

Usage:
    python scripts/evaluate_metadata_labels.py <image_id> <receipt_id>
    python scripts/evaluate_metadata_labels.py <image_id> <receipt_id> --apply
    python scripts/evaluate_metadata_labels.py <image_id> <receipt_id> -v
"""

import argparse
import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate metadata labels on a single receipt"
    )
    parser.add_argument("image_id", help="Image ID")
    parser.add_argument("receipt_id", type=int, help="Receipt ID")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply decisions to DynamoDB",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output",
    )
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

    logger.info("Loading configuration...")

    # Import after path setup
    from langchain_ollama import ChatOllama

    from receipt_dynamo import DynamoClient
    from receipt_agent.agents.label_evaluator.word_context import (
        build_word_contexts,
        assemble_visual_lines,
    )
    from receipt_agent.agents.label_evaluator.metadata_subagent import (
        evaluate_metadata_labels,
    )
    from receipt_agent.agents.label_evaluator.llm_review import apply_llm_decisions

    # Get configuration from environment
    table_name = os.environ.get(
        "RECEIPT_AGENT_DYNAMO_TABLE_NAME",
        "ReceiptsTable-dc5be22"
    )
    ollama_api_key = os.environ.get("RECEIPT_AGENT_OLLAMA_API_KEY", "")
    ollama_base_url = os.environ.get(
        "RECEIPT_AGENT_OLLAMA_BASE_URL",
        "https://ollama.com"
    )
    ollama_model = os.environ.get(
        "RECEIPT_AGENT_OLLAMA_MODEL",
        "gpt-oss:120b-cloud"
    )

    # Initialize clients
    dynamo_client = DynamoClient(table_name=table_name)

    # =========================================================================
    # Step 1: Fetch Receipt Data
    # =========================================================================
    logger.info(f"Step 1: Fetching receipt data for {args.image_id}#{args.receipt_id}...")

    # Get words
    words = dynamo_client.list_receipt_words_from_receipt(
        args.image_id, args.receipt_id
    )
    logger.info(f"  Loaded {len(words)} words")

    # Get labels (returns tuple: list of labels, last_evaluated_key)
    labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(
        args.image_id, args.receipt_id
    )
    logger.info(f"  Loaded {len(labels)} labels")

    # Get place
    place = dynamo_client.get_receipt_place(args.image_id, args.receipt_id)
    if place:
        logger.info(f"  Loaded place: {place.merchant_name}")
        logger.info(f"    Address: {place.formatted_address}")
        logger.info(f"    Phone: {place.phone_number}")
        logger.info(f"    Website: {place.website}")
    else:
        logger.warning("  No ReceiptPlace found - validation will be limited")

    # Build visual lines
    word_contexts = build_word_contexts(words, labels)
    visual_lines = assemble_visual_lines(word_contexts)
    logger.info(f"  Built {len(visual_lines)} visual lines")

    # Get merchant name
    merchant_name = place.merchant_name if place else "Unknown"

    # =========================================================================
    # Step 2: Evaluate Metadata Labels
    # =========================================================================
    logger.info("Step 2: Evaluating metadata labels with LLM...")

    # Create LLM
    llm = ChatOllama(
        model=ollama_model,
        base_url=ollama_base_url,
        api_key=ollama_api_key,
        temperature=0.0,
    )

    # Run evaluation
    decisions = evaluate_metadata_labels(
        visual_lines=visual_lines,
        place=place,
        llm=llm,
        image_id=args.image_id,
        receipt_id=args.receipt_id,
        merchant_name=merchant_name,
    )

    logger.info(f"  Evaluated {len(decisions)} metadata words")

    # Count decisions
    decision_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
    for d in decisions:
        decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
        if decision in decision_counts:
            decision_counts[decision] += 1
        else:
            decision_counts["NEEDS_REVIEW"] += 1
    logger.info(f"  Decisions: {decision_counts}")

    # =========================================================================
    # Step 3: Apply Decisions (if --apply)
    # =========================================================================
    if args.apply and decisions:
        logger.info("Step 3: Applying decisions to DynamoDB...")

        # Filter to only INVALID decisions
        invalid_decisions = [
            d for d in decisions
            if d.get("llm_review", {}).get("decision") == "INVALID"
        ]

        if invalid_decisions:
            stats = apply_llm_decisions(
                reviewed_issues=invalid_decisions,
                dynamo_client=dynamo_client,
                execution_id=f"metadata-dev-{args.image_id[:8]}",
            )
            logger.info(f"  Applied: {stats}")
        else:
            logger.info("  No INVALID decisions to apply")
    elif args.apply:
        logger.info("Step 3: No decisions to apply")

    # =========================================================================
    # Print Results
    # =========================================================================
    print("\n" + "=" * 70)
    print(f"Receipt: {args.image_id}#{args.receipt_id}")
    print(f"Merchant: {merchant_name}")
    if place:
        print(f"Address: {place.formatted_address}")
        print(f"Phone: {place.phone_number}")
        print(f"Website: {place.website}")
    print("-" * 70)
    print(f"Decisions: {decision_counts}")
    print("\n--- Metadata Words ---\n")

    for i, d in enumerate(decisions[:25]):
        issue = d["issue"]
        review = d["llm_review"]
        decision = review["decision"]
        confidence = review["confidence"]

        if decision == "VALID":
            symbol = "✓"
        elif decision == "INVALID":
            symbol = "✗"
        else:
            symbol = "?"

        print(f"  [{i}] {symbol} {decision} ({confidence})")
        print(f"      Word: \"{issue['word_text']}\"")
        print(f"      Current Label: {issue['current_label'] or 'unlabeled'}")
        print(f"      Reasoning: {review['reasoning'][:70]}")
        if review.get("suggested_label"):
            print(f"      Suggested: {review['suggested_label']}")
        print()

    if len(decisions) > 25:
        print(f"  ... and {len(decisions) - 25} more words")

    print("=" * 70)


if __name__ == "__main__":
    main()

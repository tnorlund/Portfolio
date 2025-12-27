#!/usr/bin/env python3
"""Evaluate currency labels on a single receipt.

This script mirrors the Step Function flow for currency label evaluation:

1. FetchReceiptData     → Load words, labels, place from DynamoDB
2. DiscoverLineItemPatterns → Get line item patterns (LLM)
3. EvaluateCurrencyLabels → Evaluate currency labels (LLM)
4. (Optional) Apply     → Write decisions to DynamoDB

Usage:
    python scripts/evaluate_currency_labels.py <image_id> <receipt_id>
    python scripts/evaluate_currency_labels.py <image_id> <receipt_id> --apply
    python scripts/evaluate_currency_labels.py <image_id> <receipt_id> --skip-patterns
    python scripts/evaluate_currency_labels.py <image_id> <receipt_id> -v
"""

import argparse
import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    """Load configuration from Pulumi stack outputs and secrets."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    outputs = load_env("dev")
    secrets = load_secrets("dev")

    config = {
        "dynamodb_table_name": outputs.get("dynamodb_table_name"),
        "chromadb_bucket": outputs.get("embedding_chromadb_bucket_name"),
        "ollama_api_key": secrets.get("portfolio:OLLAMA_API_KEY"),
        "langchain_api_key": secrets.get("portfolio:LANGCHAIN_API_KEY"),
    }

    # Set environment variables
    if config["ollama_api_key"]:
        os.environ["OLLAMA_API_KEY"] = config["ollama_api_key"]
        os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = config["ollama_api_key"]

    if config["langchain_api_key"]:
        os.environ["LANGCHAIN_API_KEY"] = config["langchain_api_key"]
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = "currency-evaluator-dev"

    os.environ.setdefault("OLLAMA_BASE_URL", "https://ollama.com")
    os.environ.setdefault("OLLAMA_MODEL", "gpt-oss:120b-cloud")
    os.environ.setdefault("RECEIPT_AGENT_OLLAMA_BASE_URL", "https://ollama.com")
    os.environ.setdefault("RECEIPT_AGENT_OLLAMA_MODEL", "gpt-oss:120b-cloud")

    return config


def create_llm():
    """Create the LLM instance for currency validation."""
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=os.environ.get("OLLAMA_MODEL", "gpt-oss:120b-cloud"),
        base_url=os.environ.get("OLLAMA_BASE_URL", "https://ollama.com"),
        api_key=os.environ.get("OLLAMA_API_KEY", ""),
        temperature=0.0,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate currency labels on a single receipt"
    )
    parser.add_argument("image_id", help="Image ID")
    parser.add_argument("receipt_id", type=int, help="Receipt ID")
    parser.add_argument(
        "--apply", action="store_true", help="Apply decisions to DynamoDB"
    )
    parser.add_argument(
        "--skip-patterns",
        action="store_true",
        help="Skip pattern discovery, use default patterns",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ==========================================================================
    # Step 0: Load config
    # ==========================================================================
    logger.info("Loading configuration...")
    config = load_config()

    if not config["dynamodb_table_name"]:
        logger.error("Could not load dynamodb_table_name from Pulumi")
        sys.exit(1)

    # Import after setting env vars
    from receipt_dynamo import DynamoClient
    from receipt_agent.agents.label_evaluator.word_context import (
        build_word_contexts,
        assemble_visual_lines,
    )
    from receipt_agent.agents.label_evaluator.currency_subagent import (
        evaluate_currency_labels,
    )
    from receipt_agent.agents.label_evaluator.pattern_discovery import (
        build_receipt_structure,
        build_discovery_prompt,
        discover_patterns_with_llm,
        get_default_patterns,
        PatternDiscoveryConfig,
    )
    from receipt_agent.agents.label_evaluator.llm_review import apply_llm_decisions

    dynamo_client = DynamoClient(table_name=config["dynamodb_table_name"])

    # ==========================================================================
    # Step 1: FetchReceiptData - Load receipt data from DynamoDB
    # ==========================================================================
    logger.info(f"Step 1: Fetching receipt data for {args.image_id}#{args.receipt_id}...")
    words = dynamo_client.list_receipt_words_from_receipt(
        args.image_id, args.receipt_id
    )
    labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(
        args.image_id, args.receipt_id
    )
    place = dynamo_client.get_receipt_place(args.image_id, args.receipt_id)
    merchant_name = place.merchant_name if place else "Unknown"

    logger.info(f"  Merchant: {merchant_name}")
    logger.info(f"  Words: {len(words)}, Labels: {len(labels)}")

    # Build word contexts and visual lines
    word_contexts = build_word_contexts(words, labels)
    visual_lines = assemble_visual_lines(word_contexts)
    logger.info(f"  Visual lines: {len(visual_lines)}")

    # ==========================================================================
    # Step 2: DiscoverLineItemPatterns - Get line item patterns
    # ==========================================================================
    patterns = None
    if not args.skip_patterns:
        logger.info("Step 2: Discovering line item patterns...")
        try:
            receipts_data = build_receipt_structure(
                dynamo_client,
                merchant_name,
                limit=3,
                focus_on_line_items=True,
                max_lines=80,
            )
            if receipts_data:
                prompt = build_discovery_prompt(merchant_name, receipts_data)
                llm_config = PatternDiscoveryConfig.from_env()
                patterns = discover_patterns_with_llm(prompt, llm_config)
                if patterns:
                    logger.info(f"  Item structure: {patterns.get('item_structure')}")
                    logger.info(f"  Label positions: {patterns.get('label_positions')}")
                else:
                    logger.warning("  Pattern discovery returned no patterns")
        except Exception as e:
            logger.warning(f"  Pattern discovery failed: {e}")

    if patterns is None:
        logger.info("  Using default patterns")
        patterns = get_default_patterns(merchant_name)

    # ==========================================================================
    # Step 3: EvaluateCurrencyLabels - Evaluate currency labels with LLM
    # ==========================================================================
    logger.info("Step 3: Evaluating currency labels...")
    llm = create_llm()

    decisions = evaluate_currency_labels(
        visual_lines=visual_lines,
        patterns=patterns,
        llm=llm,
        image_id=args.image_id,
        receipt_id=args.receipt_id,
        merchant_name=merchant_name,
    )

    logger.info(f"  Evaluated {len(decisions)} currency words")

    # Count decisions
    decision_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
    for d in decisions:
        decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
        if decision in decision_counts:
            decision_counts[decision] += 1
        else:
            decision_counts["NEEDS_REVIEW"] += 1
    logger.info(f"  Decisions: {decision_counts}")

    # ==========================================================================
    # Step 4: Apply decisions (optional)
    # ==========================================================================
    if args.apply and decisions:
        # Filter to only INVALID decisions (VALID and NEEDS_REVIEW don't change anything)
        apply_decisions = [
            d for d in decisions
            if d.get("llm_review", {}).get("decision") == "INVALID"
        ]

        if apply_decisions:
            logger.info(f"Step 4: Applying {len(apply_decisions)} INVALID decisions to DynamoDB...")
            stats = apply_llm_decisions(
                reviewed_issues=apply_decisions,
                dynamo_client=dynamo_client,
                execution_id=f"currency-{args.image_id[:8]}",
            )
            logger.info(f"  Applied: {stats}")
        else:
            logger.info("Step 4: No INVALID decisions to apply")

    # ==========================================================================
    # Print summary
    # ==========================================================================
    print("\n" + "=" * 70)
    print(f"Receipt: {args.image_id}#{args.receipt_id}")
    print(f"Merchant: {merchant_name}")
    print(f"Words: {len(words)}, Labels: {len(labels)}")
    print(f"Visual Lines: {len(visual_lines)}")
    print(f"Currency Words Evaluated: {len(decisions)}")
    print(f"Decisions: {decision_counts}")

    print("\n--- Currency Words ---")
    for i, d in enumerate(decisions[:20]):
        issue = d.get("issue", {})
        review = d.get("llm_review", {})
        decision = review.get("decision", "NEEDS_REVIEW")
        confidence = review.get("confidence", "unknown")
        status_icon = {"VALID": "✓", "INVALID": "✗", "NEEDS_REVIEW": "?"}.get(decision, "?")

        print(f"\n  [{i}] {status_icon} {decision} ({confidence})")
        print(f"      Word: \"{issue.get('word_text', '')}\"")
        print(f"      Current Label: {issue.get('current_label') or 'unlabeled'}")
        reasoning = review.get("reasoning", "")
        print(f"      Reasoning: {reasoning[:80] if reasoning else ''}")
        if review.get("suggested_label"):
            print(f"      Suggested: {review['suggested_label']}")

    if len(decisions) > 20:
        print(f"\n  ... and {len(decisions) - 20} more words")

    print("=" * 70)

    if config["langchain_api_key"]:
        print("\nTrace: https://smith.langchain.com/ (project: currency-evaluator-dev)")


if __name__ == "__main__":
    main()

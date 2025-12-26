#!/usr/bin/env python3
"""Evaluate a single receipt using the same logic as the Step Function.

This script calls the SAME shared functions as the Lambda handlers:
1. Pattern discovery (discover_patterns_with_llm)
2. Compute merchant patterns (compute_merchant_patterns)
3. Evaluate labels (run_compute_only_sync)
4. LLM review (review_issues_batch)
5. Apply decisions (apply_llm_decisions)

Usage:
    python scripts/evaluate_single_receipt.py <image_id> <receipt_id>
    python scripts/evaluate_single_receipt.py <image_id> <receipt_id> --apply
    python scripts/evaluate_single_receipt.py <image_id> <receipt_id> --skip-llm
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
        os.environ["LANGCHAIN_PROJECT"] = "label-evaluator-dev"

    os.environ.setdefault("OLLAMA_BASE_URL", "https://ollama.com")
    os.environ.setdefault("OLLAMA_MODEL", "gpt-oss:120b-cloud")
    os.environ.setdefault("RECEIPT_AGENT_OLLAMA_BASE_URL", "https://ollama.com")
    os.environ.setdefault("RECEIPT_AGENT_OLLAMA_MODEL", "gpt-oss:120b-cloud")

    return config


def main():
    parser = argparse.ArgumentParser(description="Evaluate a single receipt")
    parser.add_argument("image_id", help="Image ID")
    parser.add_argument("receipt_id", type=int, help="Receipt ID")
    parser.add_argument("--apply", action="store_true", help="Apply decisions to DynamoDB")
    parser.add_argument("--skip-llm", action="store_true", help="Skip LLM review")
    parser.add_argument("--skip-patterns", action="store_true", help="Skip pattern discovery")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load config
    logger.info("Loading configuration...")
    config = load_config()

    if not config["dynamodb_table_name"]:
        logger.error("Could not load dynamodb_table_name from Pulumi")
        sys.exit(1)

    # Import after setting env vars
    from receipt_dynamo import DynamoClient
    from receipt_agent.agents.label_evaluator import (
        # Pattern discovery
        build_receipt_structure,
        build_discovery_prompt,
        get_default_patterns,
        PatternDiscoveryConfig,
        # Patterns
        compute_merchant_patterns,
        OtherReceiptData,
        # Evaluation
        create_compute_only_graph,
        run_compute_only_sync,
        EvaluatorState,
        # LLM Review
        review_issues_batch,
        apply_llm_decisions,
    )
    from receipt_agent.agents.label_evaluator.pattern_discovery import (
        discover_patterns_with_llm,
    )

    dynamo_client = DynamoClient(table_name=config["dynamodb_table_name"])

    # 1. Load receipt data
    logger.info(f"Loading receipt {args.image_id}#{args.receipt_id}...")
    words = dynamo_client.list_receipt_words_from_receipt(args.image_id, args.receipt_id)
    labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(args.image_id, args.receipt_id)
    place = dynamo_client.get_receipt_place(args.image_id, args.receipt_id)
    merchant_name = place.merchant_name if place else "Unknown"

    logger.info(f"  Merchant: {merchant_name}")
    logger.info(f"  Words: {len(words)}, Labels: {len(labels)}")

    # 2. Pattern discovery
    if args.skip_patterns:
        patterns = get_default_patterns(merchant_name, "skipped")
    else:
        logger.info("Running pattern discovery...")
        receipts_data = build_receipt_structure(
            dynamo_client, merchant_name, limit=3, focus_on_line_items=True, max_lines=80
        )
        if receipts_data:
            prompt = build_discovery_prompt(merchant_name, receipts_data)
            llm_config = PatternDiscoveryConfig.from_env()
            patterns = discover_patterns_with_llm(prompt, llm_config)
            if not patterns:
                patterns = get_default_patterns(merchant_name, "llm_failed")
        else:
            patterns = get_default_patterns(merchant_name, "no_data")

    logger.info(f"  Receipt type: {patterns.get('receipt_type', 'unknown')}")
    logger.info(f"  Item structure: {patterns.get('item_structure', 'unknown')}")

    # 3. Load training receipts and compute merchant patterns
    logger.info("Loading training receipts...")
    other_receipts = []
    places, _ = dynamo_client.get_receipt_places_by_merchant(merchant_name, limit=20)
    for p in places:
        if p.image_id == args.image_id and p.receipt_id == args.receipt_id:
            continue
        if len(other_receipts) >= 10:
            break
        try:
            other_words = dynamo_client.list_receipt_words_from_receipt(p.image_id, p.receipt_id)
            other_labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(p.image_id, p.receipt_id)
            other_receipts.append(OtherReceiptData(place=p, words=other_words, labels=other_labels))
        except Exception as e:
            logger.warning(f"  Failed to load {p.image_id}#{p.receipt_id}: {e}")

    logger.info(f"  Loaded {len(other_receipts)} training receipts")
    merchant_patterns = compute_merchant_patterns(other_receipts, merchant_name)

    # 4. Run evaluation
    logger.info("Running label evaluation...")
    state = EvaluatorState(
        image_id=args.image_id,
        receipt_id=args.receipt_id,
        words=words,
        labels=labels,
        place=place,
        other_receipt_data=[],
        merchant_patterns=merchant_patterns,
        skip_llm_review=True,
    )
    graph = create_compute_only_graph()
    result = run_compute_only_sync(graph, state)

    issues = result.get("issues", [])
    logger.info(f"  Found {len(issues)} issues")

    # 5. LLM Review
    reviewed_issues = []
    if issues and not args.skip_llm:
        logger.info("Running LLM review...")
        # Download ChromaDB if available
        chroma_client = None
        if config.get("chromadb_bucket"):
            try:
                import boto3
                import tempfile
                sys.path.insert(0, os.path.join(PROJECT_ROOT, "infra/label_evaluator_step_functions/lambdas/utils"))
                from s3_helpers import download_chromadb_snapshot
                from receipt_chroma import ChromaClient

                s3 = boto3.client("s3")
                chroma_path = os.path.join(tempfile.gettempdir(), "chromadb_dev")
                download_chromadb_snapshot(s3, config["chromadb_bucket"], "words", chroma_path)
                chroma_client = ChromaClient(persist_directory=chroma_path)
                logger.info("  ChromaDB loaded")
            except Exception as e:
                logger.warning(f"  ChromaDB failed: {e}")

        reviewed_issues = review_issues_batch(
            issues=issues,
            words=words,
            labels=labels,
            merchant_name=merchant_name,
            line_item_patterns=patterns,
            chroma_client=chroma_client,
            dynamo_client=dynamo_client,
        )
        logger.info(f"  Reviewed {len(reviewed_issues)} issues")

    # 6. Apply decisions
    if args.apply and reviewed_issues:
        logger.info("Applying decisions to DynamoDB...")
        stats = apply_llm_decisions(
            reviewed_issues=reviewed_issues,
            dynamo_client=dynamo_client,
            execution_id=f"dev-{args.image_id[:8]}",
        )
        logger.info(f"  Applied: {stats}")

    # Print summary
    print("\n" + "=" * 60)
    print(f"Receipt: {args.image_id}#{args.receipt_id}")
    print(f"Merchant: {merchant_name}")
    print(f"Words: {len(words)}, Labels: {len(labels)}")
    print(f"Issues found: {len(issues)}")

    if reviewed_issues:
        decisions = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
        for r in reviewed_issues:
            decisions[r.get("decision", "NEEDS_REVIEW")] += 1
        print(f"LLM decisions: {decisions}")

    for i, issue in enumerate(issues[:10]):
        print(f"\n  [{i}] {issue.issue_type}: \"{issue.word.text}\"")
        print(f"      Current: {issue.current_label}")
        if i < len(reviewed_issues):
            r = reviewed_issues[i]
            print(f"      Decision: {r.get('decision')} - {r.get('reasoning', '')[:60]}...")

    print("=" * 60)

    if config["langchain_api_key"]:
        print("\nTrace: https://smith.langchain.com/ (project: label-evaluator-dev)")


if __name__ == "__main__":
    main()

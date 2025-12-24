#!/usr/bin/env python3
"""Analyze pattern discovery across all merchants.

This script runs the LLM-based receipt type classification and pattern discovery
against all merchants to understand coverage and effectiveness.

Usage:
    # Analyze all merchants with LLM classification
    python scripts/analyze_all_merchants.py

    # Limit to specific number of merchants
    python scripts/analyze_all_merchants.py --max-merchants 10

    # Output to CSV
    python scripts/analyze_all_merchants.py --output results.csv

    # Skip LLM calls (structure analysis only)
    python scripts/analyze_all_merchants.py --no-llm
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class MerchantAnalysis:
    """Analysis results for a single merchant."""
    merchant_name: str
    total_receipts: int
    receipts_with_words: int = 0
    receipts_analyzed: int = 0
    avg_lines_selected: float = 0.0
    avg_total_lines: float = 0.0
    # LLM classification results
    receipt_type: str = ""  # "itemized", "service", or ""
    receipt_type_reason: str = ""
    has_patterns: bool = False
    item_structure: str = ""  # "single-line", "multi-line"
    llm_error: str | None = None
    error: str | None = None


def load_config():
    """Load configuration from Pulumi stack outputs and secrets."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    outputs = load_env("dev")
    secrets = load_secrets("dev")

    config = {
        "dynamodb_table_name": outputs.get("dynamodb_table_name"),
        "chromadb_bucket": outputs.get("embedding_chromadb_bucket_name"),
        "openai_api_key": secrets.get("portfolio:OPENAI_API_KEY"),
        "ollama_api_key": secrets.get("portfolio:OLLAMA_API_KEY"),
        "langchain_api_key": secrets.get("portfolio:LANGCHAIN_API_KEY"),
    }

    # Set environment variables
    if config["openai_api_key"]:
        os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = config["openai_api_key"]
    if config["ollama_api_key"]:
        os.environ["OLLAMA_API_KEY"] = config["ollama_api_key"]
    if config["langchain_api_key"]:
        os.environ["LANGCHAIN_API_KEY"] = config["langchain_api_key"]
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = "merchant-analysis"

    # Default Ollama settings
    os.environ.setdefault("OLLAMA_BASE_URL", "https://ollama.com")
    os.environ.setdefault("OLLAMA_MODEL", "gpt-oss:120b-cloud")

    return config


def get_all_merchants(dynamo_client) -> list[tuple[str, int]]:
    """Get all unique merchants with receipt counts."""
    logger.info("Fetching all receipt places...")

    all_places = []
    result = dynamo_client.list_receipt_places()
    all_places.extend(result[0])

    page_count = 1
    while result[1]:
        result = dynamo_client.list_receipt_places(last_evaluated_key=result[1])
        all_places.extend(result[0])
        page_count += 1
        if page_count % 10 == 0:
            logger.info(f"  Fetched {len(all_places)} places so far...")

    logger.info(f"Found {len(all_places)} total receipt places")

    # Count receipts per merchant
    merchant_counts: Counter = Counter()
    for place in all_places:
        merchant_counts[place.merchant_name] += 1

    # Sort by count descending
    sorted_merchants = sorted(
        merchant_counts.items(),
        key=lambda x: -x[1]
    )

    logger.info(f"Found {len(sorted_merchants)} unique merchants")
    return sorted_merchants


def analyze_merchant(
    dynamo_client,
    merchant_name: str,
    total_receipts: int,
    build_receipt_structure,
    build_discovery_prompt,
    discover_patterns_with_llm,
    PatternDiscoveryConfig,
    max_receipts: int = 5,
    run_llm: bool = True,
) -> MerchantAnalysis:
    """Analyze a single merchant's receipt structure."""
    analysis = MerchantAnalysis(
        merchant_name=merchant_name,
        total_receipts=total_receipts,
    )

    try:
        # Build receipt structure (no smart selection - let LLM see full structure)
        receipts_data = build_receipt_structure(
            dynamo_client,
            merchant_name,
            limit=max_receipts,
            focus_on_line_items=False,  # Show full receipt to LLM
            max_lines=80,
        )

        if not receipts_data:
            analysis.error = "No receipt data found"
            return analysis

        analysis.receipts_analyzed = len(receipts_data)

        # Count receipts with words
        receipts_with_words = [r for r in receipts_data if r.get("lines")]
        analysis.receipts_with_words = len(receipts_with_words)

        if not receipts_with_words:
            analysis.error = "No receipts with word data"
            return analysis

        # Calculate line stats
        total_lines = sum(len(r.get("lines", [])) for r in receipts_data)
        total_original = sum(r.get("total_lines", len(r.get("lines", []))) for r in receipts_data)
        analysis.avg_lines_selected = total_lines / len(receipts_data)
        analysis.avg_total_lines = total_original / len(receipts_data)

        # Run LLM classification if requested
        if run_llm:
            prompt = build_discovery_prompt(merchant_name, receipts_data)
            config = PatternDiscoveryConfig.from_env()

            try:
                result = discover_patterns_with_llm(prompt, config)

                if result:
                    analysis.receipt_type = result.get("receipt_type", "")
                    analysis.receipt_type_reason = result.get("receipt_type_reason", "")

                    patterns = result.get("patterns")
                    if patterns:
                        analysis.has_patterns = True
                        analysis.item_structure = patterns.get("item_structure", "")
                else:
                    analysis.llm_error = "No response from LLM"

            except Exception as e:
                analysis.llm_error = str(e)

    except Exception as e:
        analysis.error = str(e)

    return analysis


def main():
    parser = argparse.ArgumentParser(
        description="Analyze pattern discovery across all merchants"
    )
    parser.add_argument(
        "--max-merchants",
        type=int,
        default=None,
        help="Maximum number of merchants to analyze",
    )
    parser.add_argument(
        "--min-receipts",
        type=int,
        default=3,
        help="Minimum receipts per merchant to include (default: 3)",
    )
    parser.add_argument(
        "--receipts-per-merchant",
        type=int,
        default=3,
        help="Number of receipts to analyze per merchant (default: 3)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output CSV file path",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM calls (structure analysis only)",
    )
    parser.add_argument(
        "--env",
        default="dev",
        choices=["dev", "prod"],
        help="Pulumi environment (default: dev)",
    )
    args = parser.parse_args()

    # Load config from Pulumi
    logger.info("Loading configuration from Pulumi...")
    config = load_config()

    if not config["dynamodb_table_name"]:
        logger.error("Could not load dynamodb_table_name from Pulumi outputs")
        sys.exit(1)

    if not args.no_llm and not config["ollama_api_key"]:
        logger.error("OLLAMA_API_KEY not found. Use --no-llm to skip LLM calls.")
        sys.exit(1)

    logger.info(f"Using DynamoDB table: {config['dynamodb_table_name']}")

    # Import pattern_discovery module directly (avoid chromadb import issues with Python 3.14)
    import importlib.util

    pattern_discovery_path = os.path.join(
        PROJECT_ROOT,
        "receipt_agent/receipt_agent/agents/label_evaluator/pattern_discovery.py"
    )
    spec = importlib.util.spec_from_file_location("pattern_discovery", pattern_discovery_path)
    pattern_discovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pattern_discovery)

    build_receipt_structure = pattern_discovery.build_receipt_structure
    build_discovery_prompt = pattern_discovery.build_discovery_prompt
    discover_patterns_with_llm = pattern_discovery.discover_patterns_with_llm
    PatternDiscoveryConfig = pattern_discovery.PatternDiscoveryConfig

    # Import DynamoClient
    from receipt_dynamo import DynamoClient

    dynamo_client = DynamoClient(table_name=config["dynamodb_table_name"])

    # Get all merchants
    all_merchants = get_all_merchants(dynamo_client)

    # Filter by min_receipts
    merchants = [
        (name, count) for name, count in all_merchants
        if count >= args.min_receipts
    ]
    logger.info(f"Filtered to {len(merchants)} merchants with >= {args.min_receipts} receipts")

    # Limit if specified
    if args.max_merchants:
        merchants = merchants[:args.max_merchants]
        logger.info(f"Limited to first {args.max_merchants} merchants")

    # Analyze each merchant
    results: list[MerchantAnalysis] = []
    run_llm = not args.no_llm

    for i, (merchant_name, receipt_count) in enumerate(merchants):
        logger.info(f"[{i+1}/{len(merchants)}] Analyzing: {merchant_name} ({receipt_count} receipts)")

        analysis = analyze_merchant(
            dynamo_client,
            merchant_name,
            receipt_count,
            build_receipt_structure,
            build_discovery_prompt,
            discover_patterns_with_llm,
            PatternDiscoveryConfig,
            max_receipts=args.receipts_per_merchant,
            run_llm=run_llm,
        )
        results.append(analysis)

        # Print progress
        if analysis.error:
            logger.warning(f"  Error: {analysis.error}")
        elif analysis.llm_error:
            logger.warning(f"  LLM Error: {analysis.llm_error}")
        else:
            type_str = analysis.receipt_type.upper() if analysis.receipt_type else "N/A"
            logger.info(
                f"  Type: {type_str}, "
                f"Patterns: {analysis.has_patterns}, "
                f"Lines: {analysis.avg_lines_selected:.0f}"
            )

        # Small delay between LLM calls to avoid rate limiting
        if run_llm and i < len(merchants) - 1:
            time.sleep(0.5)

    # Print summary
    print("\n" + "=" * 80)
    print("ANALYSIS SUMMARY")
    print("=" * 80)

    total_merchants = len(results)
    merchants_with_data = len([r for r in results if r.receipts_with_words > 0])
    merchants_with_errors = len([r for r in results if r.error])

    print(f"Total merchants analyzed: {total_merchants}")
    print(f"Merchants with word data: {merchants_with_data} ({100*merchants_with_data/total_merchants:.1f}%)")
    print(f"Merchants with errors: {merchants_with_errors}")

    if run_llm:
        # Receipt type distribution
        itemized = [r for r in results if r.receipt_type == "itemized"]
        service = [r for r in results if r.receipt_type == "service"]
        llm_errors = [r for r in results if r.llm_error]

        print(f"\n=== RECEIPT TYPE CLASSIFICATION ===")
        print(f"ITEMIZED: {len(itemized)} merchants ({100*len(itemized)/total_merchants:.1f}%)")
        print(f"SERVICE: {len(service)} merchants ({100*len(service)/total_merchants:.1f}%)")
        print(f"LLM Errors: {len(llm_errors)}")

        # Show service merchants
        if service:
            print(f"\n=== SERVICE MERCHANTS (no line items) ===")
            for r in service:
                print(f"  {r.merchant_name}: {r.receipt_type_reason}")

        # Show itemized merchants with patterns
        itemized_with_patterns = [r for r in itemized if r.has_patterns]
        print(f"\n=== ITEMIZED MERCHANTS WITH PATTERNS ({len(itemized_with_patterns)}) ===")

        # Group by item structure
        single_line = [r for r in itemized_with_patterns if r.item_structure == "single-line"]
        multi_line = [r for r in itemized_with_patterns if r.item_structure == "multi-line"]

        print(f"  Single-line structure: {len(single_line)}")
        for r in single_line[:5]:
            print(f"    - {r.merchant_name} ({r.total_receipts} receipts)")

        print(f"  Multi-line structure: {len(multi_line)}")
        for r in multi_line[:5]:
            print(f"    - {r.merchant_name} ({r.total_receipts} receipts)")

    # Receipts summary
    total_receipts = sum(r.total_receipts for r in results)
    analyzed_receipts = sum(r.receipts_analyzed for r in results)

    print(f"\n=== RECEIPT COUNTS ===")
    print(f"Total receipts in DB: {total_receipts}")
    print(f"Receipts analyzed: {analyzed_receipts}")

    # Output to CSV if specified
    if args.output:
        with open(args.output, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "merchant_name",
                "total_receipts",
                "receipts_with_words",
                "receipts_analyzed",
                "avg_lines_selected",
                "avg_total_lines",
                "receipt_type",
                "receipt_type_reason",
                "has_patterns",
                "item_structure",
                "llm_error",
                "error",
            ])
            for r in results:
                writer.writerow([
                    r.merchant_name,
                    r.total_receipts,
                    r.receipts_with_words,
                    r.receipts_analyzed,
                    f"{r.avg_lines_selected:.2f}",
                    f"{r.avg_total_lines:.2f}",
                    r.receipt_type,
                    r.receipt_type_reason,
                    r.has_patterns,
                    r.item_structure,
                    r.llm_error or "",
                    r.error or "",
                ])
        logger.info(f"Results written to {args.output}")

    print("=" * 80)


if __name__ == "__main__":
    main()

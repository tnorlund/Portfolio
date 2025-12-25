#!/usr/bin/env python3
"""Evaluate labels for a single receipt with LangSmith tracing.

This dev script runs the same pattern discovery and issue detection logic
as the step function, but for a single receipt. Results are traced in LangSmith.

Usage:
    python scripts/evaluate_single_receipt.py <image_id> <receipt_id> [--verbose]

Examples:
    # Run pattern discovery for a receipt
    python scripts/evaluate_single_receipt.py abc123 0

    # Verbose output
    python scripts/evaluate_single_receipt.py abc123 0 --verbose

Requirements:
    pip install receipt-dynamo langsmith langchain-ollama
"""

import argparse
import importlib.util
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from typing import Any

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_config() -> dict[str, Any]:
    """Load configuration from Pulumi stack outputs and secrets."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    outputs = load_env("dev")
    secrets = load_secrets("dev")

    config = {
        "dynamodb_table_name": outputs.get("dynamodb_table_name"),
        "ollama_api_key": secrets.get("portfolio:OLLAMA_API_KEY"),
        "langchain_api_key": secrets.get("portfolio:LANGCHAIN_API_KEY"),
    }

    # Set environment variables for receipt_agent and langsmith
    if config["ollama_api_key"]:
        os.environ["OLLAMA_API_KEY"] = config["ollama_api_key"]
        os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = config["ollama_api_key"]

    if config["langchain_api_key"]:
        os.environ["LANGCHAIN_API_KEY"] = config["langchain_api_key"]
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = "label-evaluator-dev"

    # Set Ollama settings (same as step function)
    os.environ["OLLAMA_BASE_URL"] = "https://ollama.com"
    os.environ["OLLAMA_MODEL"] = "gpt-oss:20b"
    os.environ["RECEIPT_AGENT_OLLAMA_BASE_URL"] = "https://ollama.com"
    os.environ["RECEIPT_AGENT_OLLAMA_MODEL"] = "gpt-oss:20b"

    return config


def load_pattern_discovery():
    """Load pattern_discovery module using importlib to avoid chromadb issues."""
    pattern_discovery_path = os.path.join(
        PROJECT_ROOT,
        "receipt_agent/receipt_agent/agents/label_evaluator/pattern_discovery.py"
    )
    spec = importlib.util.spec_from_file_location("pattern_discovery", pattern_discovery_path)
    pattern_discovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pattern_discovery)
    return pattern_discovery


def load_receipt_data(
    dynamo_client: Any,
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


def find_potential_issues(
    words: list,
    labels: list,
    patterns: dict[str, Any] | None = None,
) -> list[dict]:
    """Find potential labeling issues using heuristics.

    This mimics the logic from the step function's evaluate_labels Lambda.
    """
    issues = []

    # Build label map
    label_map: dict[tuple[int, int], list] = {}
    for label in labels:
        key = (label.line_id, label.word_id)
        if key not in label_map:
            label_map[key] = []
        label_map[key].append(label)

    # Check for unlabeled words that look like products or prices
    for word in words:
        key = (word.line_id, word.word_id)
        word_labels = label_map.get(key, [])
        text = word.text.strip()

        # Skip if already labeled
        if word_labels:
            continue

        # Check for potential product names (uppercase words)
        if len(text) >= 3 and text.isupper() and text.isalpha():
            issues.append({
                "type": "potential_unlabeled_product",
                "image_id": word.image_id,
                "receipt_id": word.receipt_id,
                "line_id": word.line_id,
                "word_id": word.word_id,
                "word_text": text,
                "current_labels": [],
                "reasoning": "Unlabeled uppercase word that may be a product name",
            })

        # Check for potential prices (dollar amounts)
        if text.startswith("$") or (text.replace(".", "").isdigit() and "." in text):
            issues.append({
                "type": "potential_unlabeled_price",
                "image_id": word.image_id,
                "receipt_id": word.receipt_id,
                "line_id": word.line_id,
                "word_id": word.word_id,
                "word_text": text,
                "current_labels": [],
                "reasoning": "Unlabeled price/dollar amount",
            })

    # Limit to most relevant issues
    return issues[:20]


def format_patterns(patterns: dict[str, Any]) -> str:
    """Format patterns for display."""
    lines = []
    lines.append(f"Merchant: {patterns.get('merchant', 'Unknown')}")

    receipt_type = patterns.get("receipt_type")
    if receipt_type:
        lines.append(f"Receipt Type: {receipt_type}")
        if patterns.get("receipt_type_reason"):
            lines.append(f"Reason: {patterns['receipt_type_reason']}")

    if receipt_type == "service":
        return "\n".join(lines)

    if patterns.get("item_structure"):
        lines.append(f"Item Structure: {patterns['item_structure']}")

    lpi = patterns.get("lines_per_item")
    if lpi and isinstance(lpi, dict):
        lines.append(
            f"Lines per Item: typical={lpi.get('typical')}, "
            f"range=[{lpi.get('min')}, {lpi.get('max')}]"
        )

    if patterns.get("grouping_rule"):
        lines.append(f"Grouping Rule: {patterns['grouping_rule']}")

    label_positions = patterns.get("label_positions")
    if label_positions and isinstance(label_positions, dict):
        lines.append("Label Positions:")
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
        key=lambda lid: min(w.bounding_box["y"] for w in lines_dict[lid]),
        reverse=True,  # Higher y = top of receipt
    )

    result_lines = []
    for line_id in sorted_line_ids[:max_lines]:
        line_words = sorted(lines_dict[line_id], key=lambda w: w.bounding_box["x"])
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
    parser = argparse.ArgumentParser(
        description="Evaluate labels for a single receipt with LangSmith tracing"
    )
    parser.add_argument("image_id", help="The image ID")
    parser.add_argument("receipt_id", type=int, help="The receipt ID (0, 1, 2, ...)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--skip-patterns", action="store_true", help="Skip pattern discovery")
    parser.add_argument("--output-json", type=str, default=None, help="Output to JSON file")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load config from Pulumi
    logger.info("Loading configuration from Pulumi...")
    config = load_config()

    if not config["dynamodb_table_name"]:
        logger.error("Could not load dynamodb_table_name from Pulumi outputs")
        sys.exit(1)

    if not config["langchain_api_key"]:
        logger.warning("LANGCHAIN_API_KEY not found - traces will not be sent to LangSmith")

    logger.info(f"Using DynamoDB table: {config['dynamodb_table_name']}")

    # Import DynamoDB client
    from receipt_dynamo import DynamoClient
    dynamo_client = DynamoClient(table_name=config["dynamodb_table_name"])

    # Load pattern_discovery module
    pattern_discovery = load_pattern_discovery()

    # 1. Load receipt data
    receipt_data = load_receipt_data(dynamo_client, args.image_id, args.receipt_id)

    # 2. Discover patterns using the same code as step function
    if args.skip_patterns:
        patterns = pattern_discovery.get_default_patterns(
            receipt_data["merchant_name"], "skipped"
        )
    else:
        # Use langsmith traceable for tracing
        from langsmith import traceable
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage, SystemMessage
        import json as json_mod

        @traceable(
            name="evaluate_single_receipt",
            project_name="label-evaluator-dev",
            metadata={
                "image_id": args.image_id,
                "receipt_id": args.receipt_id,
                "merchant_name": receipt_data["merchant_name"],
            },
        )
        def run_evaluation(dynamo_client, merchant_name, receipt_data):
            """Run the full evaluation with tracing."""
            # Build receipt structure
            receipts_data = pattern_discovery.build_receipt_structure(
                dynamo_client,
                merchant_name,
                limit=3,
                focus_on_line_items=True,
                max_lines=80,
            )

            if not receipts_data:
                logger.warning("No receipt data found for pattern discovery")
                return pattern_discovery.get_default_patterns(merchant_name, "no_data")

            # Build prompt
            prompt = pattern_discovery.build_discovery_prompt(merchant_name, receipts_data)

            # Use LangChain ChatOllama for proper LangSmith tracing
            logger.info("Calling LLM for pattern discovery...")
            ollama_api_key = os.environ.get("OLLAMA_API_KEY", "")
            ollama_base_url = os.environ.get("OLLAMA_BASE_URL", "https://ollama.tylernorlund.com")
            ollama_model = os.environ.get("OLLAMA_MODEL", "llama3.2")

            try:
                llm = ChatOllama(
                    model=ollama_model,
                    base_url=ollama_base_url,
                    client_kwargs={
                        "headers": {"Authorization": f"Bearer {ollama_api_key}"},
                        "timeout": 120,
                    },
                    temperature=0,
                )

                messages = [
                    SystemMessage(content="You are a receipt analysis expert. Respond only with valid JSON."),
                    HumanMessage(content=prompt),
                ]

                response = llm.invoke(messages)
                content = response.content.strip()

                # Parse JSON from response
                if content.startswith("```json"):
                    content = content[7:]
                if content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

                patterns = json_mod.loads(content)
                patterns["discovered_from_receipts"] = len(receipts_data)
                patterns["auto_generated"] = False

                return patterns

            except Exception as e:
                logger.exception(f"LLM call failed: {e}")
                return pattern_discovery.get_default_patterns(merchant_name, f"llm_failed: {e}")

        patterns = run_evaluation(
            dynamo_client,
            receipt_data["merchant_name"],
            receipt_data,
        )

    logger.info(f"Receipt type: {patterns.get('receipt_type', 'unknown')}")
    logger.info(f"Item structure: {patterns.get('item_structure', 'unknown')}")

    # 3. Find potential issues
    issues = find_potential_issues(
        receipt_data["words"],
        receipt_data["labels"],
        patterns,
    )
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

    # Print LangSmith trace URL hint
    if config["langchain_api_key"]:
        print("\nTrace sent to LangSmith project: label-evaluator-dev")
        print("View at: https://smith.langchain.com/")


if __name__ == "__main__":
    main()

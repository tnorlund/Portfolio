#!/usr/bin/env python3
"""Evaluate labels for a single receipt with LangSmith tracing.

This dev script runs the SAME business logic as the step function:
1. Pattern discovery (LLM call #1)
2. Full label evaluation (6 issue detection strategies)
3. LLM review of issues with receipt context (LLM call #2)
4. Apply decisions to DynamoDB (optional)

Usage:
    # Full pipeline (pattern discovery + evaluation + LLM review)
    .venv/bin/python scripts/evaluate_single_receipt.py <image_id> <receipt_id>

    # Apply decisions to DynamoDB
    .venv/bin/python scripts/evaluate_single_receipt.py <image_id> <receipt_id> --apply

Requirements:
    pip install receipt-dynamo receipt-chroma langsmith langchain-ollama
"""

import argparse
import json
import logging
import os
import sys
import tempfile
from typing import Any

import boto3

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Add lambdas/utils to path for s3_helpers
sys.path.insert(0, os.path.join(
    PROJECT_ROOT,
    "infra/label_evaluator_step_functions/lambdas/utils"
))

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
        "chromadb_bucket": outputs.get("embedding_chromadb_bucket_name"),
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
    os.environ["OLLAMA_MODEL"] = "gpt-oss:120b-cloud"
    os.environ["RECEIPT_AGENT_OLLAMA_BASE_URL"] = "https://ollama.com"
    os.environ["RECEIPT_AGENT_OLLAMA_MODEL"] = "gpt-oss:120b-cloud"

    return config


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


def download_chromadb(s3_client, bucket: str, cache_path: str) -> str:
    """Download ChromaDB snapshot from S3."""
    from s3_helpers import download_chromadb_snapshot

    logger.info(f"Downloading ChromaDB from s3://{bucket}/words/")
    return download_chromadb_snapshot(s3_client, bucket, "words", cache_path)


def run_pattern_discovery(
    dynamo_client: Any,
    merchant_name: str,
    image_id: str,
    receipt_id: int,
) -> dict[str, Any]:
    """Run LLM-based pattern discovery for the merchant."""
    from langsmith import traceable
    from langchain_ollama import ChatOllama
    from langchain_core.messages import HumanMessage, SystemMessage
    from receipt_agent.agents.label_evaluator import (
        build_receipt_structure,
        build_discovery_prompt,
        get_default_patterns,
    )

    @traceable(name="pattern_discovery", run_type="chain")
    def _discover(dynamo_client, merchant_name):
        receipts_data = build_receipt_structure(
            dynamo_client,
            merchant_name,
            limit=3,
            focus_on_line_items=True,
            max_lines=80,
        )

        if not receipts_data:
            logger.warning("No receipt data found for pattern discovery")
            return get_default_patterns(merchant_name, "no_data")

        prompt = build_discovery_prompt(merchant_name, receipts_data)

        logger.info("Calling LLM for pattern discovery...")
        ollama_api_key = os.environ.get("OLLAMA_API_KEY", "")
        ollama_base_url = os.environ.get("OLLAMA_BASE_URL")
        ollama_model = os.environ.get("OLLAMA_MODEL")

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

            patterns = json.loads(content)
            patterns["discovered_from_receipts"] = len(receipts_data)
            patterns["auto_generated"] = False

            return patterns

        except Exception as e:
            logger.exception(f"LLM call failed: {e}")
            return get_default_patterns(merchant_name, f"llm_failed: {e}")

    return _discover(dynamo_client, merchant_name)


def run_full_evaluation(
    dynamo_client: Any,
    receipt_data: dict[str, Any],
    patterns: dict[str, Any],
    max_training_receipts: int = 10,
) -> list[Any]:
    """Run full label evaluation using the same logic as step function."""
    from langsmith import traceable
    from receipt_agent.agents.label_evaluator import (
        build_word_contexts,
        compute_merchant_patterns,
        evaluate_word_contexts,
        EvaluationIssue,
        OtherReceiptData,
    )

    @traceable(name="evaluate_labels", run_type="chain")
    def _evaluate(dynamo_client, receipt_data, patterns, max_training_receipts):
        image_id = receipt_data["image_id"]
        receipt_id = receipt_data["receipt_id"]
        merchant_name = receipt_data["merchant_name"]
        words = receipt_data["words"]
        labels = receipt_data["labels"]

        # Only use VALID labels
        valid_labels = [l for l in labels if l.validation_status == "VALID"]
        logger.info(f"  Using {len(valid_labels)} VALID labels (of {len(labels)} total)")

        # Build word contexts
        word_contexts = build_word_contexts(words, valid_labels)
        logger.info(f"  Built {len(word_contexts)} word contexts")

        # Load other receipts for merchant patterns (same as step function)
        logger.info(f"  Loading training receipts for {merchant_name}...")
        other_receipt_data: list[OtherReceiptData] = []
        last_key = None

        while len(other_receipt_data) < max_training_receipts:
            places, last_key = dynamo_client.get_receipt_places_by_merchant(
                merchant_name,
                limit=min(100, max_training_receipts - len(other_receipt_data)),
                last_evaluated_key=last_key,
            )

            if not places:
                break

            for place in places:
                if len(other_receipt_data) >= max_training_receipts:
                    break

                # Skip the receipt we're evaluating
                if place.image_id == image_id and place.receipt_id == receipt_id:
                    continue

                try:
                    other_words = dynamo_client.list_receipt_words_from_receipt(
                        place.image_id, place.receipt_id
                    )
                    other_labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(
                        place.image_id, place.receipt_id
                    )

                    other_receipt_data.append(
                        OtherReceiptData(
                            place=place,
                            words=other_words,
                            labels=other_labels,
                        )
                    )
                except Exception as e:
                    logger.warning(f"  Error loading receipt {place.image_id}#{place.receipt_id}: {e}")
                    continue

            if not last_key:
                break

        logger.info(f"  Loaded {len(other_receipt_data)} training receipts")

        # Compute merchant patterns from other receipts
        logger.info(f"  Computing merchant patterns...")
        merchant_patterns = compute_merchant_patterns(
            other_receipt_data,
            merchant_name,
        )

        if merchant_patterns:
            logger.info(f"  Got patterns from {merchant_patterns.receipt_count} receipts")
        else:
            logger.warning("  No merchant patterns found")

        # Run all 6 evaluation checks
        logger.info("  Running evaluation checks...")
        issues = evaluate_word_contexts(
            word_contexts=word_contexts,
            patterns=merchant_patterns,
        )

        logger.info(f"  Found {len(issues)} issues")
        return issues

    return _evaluate(dynamo_client, receipt_data, patterns, max_training_receipts)


def run_llm_review(
    receipt_data: dict[str, Any],
    issues: list[Any],
    patterns: dict[str, Any],
    chroma_client: Any,
    dynamo_client: Any,
) -> list[dict[str, Any]]:
    """Run LLM review on detected issues."""
    from langsmith import traceable
    from langchain_ollama import ChatOllama
    from langchain_core.messages import HumanMessage, SystemMessage
    from receipt_agent.agents.label_evaluator import (
        assemble_receipt_text,
        query_similar_validated_words,
    )
    from receipt_agent.prompts.label_evaluator import (
        build_receipt_context_prompt,
        parse_batched_llm_response,
    )

    if not issues:
        logger.info("No issues to review")
        return []

    @traceable(name="llm_review", run_type="chain")
    def _review(receipt_data, issues, patterns, chroma_client, dynamo_client):
        words = receipt_data["words"]
        labels = receipt_data["labels"]
        merchant_name = receipt_data["merchant_name"]

        # Convert ReceiptWord and ReceiptWordLabel objects to dicts for assemble_receipt_text
        from dataclasses import asdict
        words_dicts = [asdict(w) for w in words]
        labels_dicts = [asdict(l) for l in labels]

        # Assemble receipt text
        receipt_text = assemble_receipt_text(words_dicts, labels_dicts)

        # Build issues with similar word evidence
        issues_with_context = []
        for i, issue in enumerate(issues):
            word_text = issue.word.text
            # Query similar words from ChromaDB
            similar_words = []
            if chroma_client:
                try:
                    similar_words = query_similar_validated_words(
                        word=issue.word,
                        chroma_client=chroma_client,
                        n_results=10,
                        merchant_name=merchant_name,
                    )
                except Exception as e:
                    logger.warning(f"ChromaDB query failed for '{word_text}': {e}")

            # Convert SimilarWordResult to evidence dict
            similar_evidence = [
                {
                    "word_text": sw.word_text,
                    "similarity_score": sw.similarity_score,
                    "validated_as": [
                        {"label": lbl, "reasoning": "Previously validated"}
                        for lbl in sw.valid_labels
                    ] if sw.valid_labels else [],
                    "is_same_merchant": sw.merchant_name == merchant_name if sw.merchant_name else False,
                }
                for sw in similar_words
            ] if similar_words else []

            issues_with_context.append({
                "issue": {
                    "word_text": word_text,
                    "current_label": issue.current_label,
                    "type": issue.issue_type,
                    "reasoning": issue.reasoning,
                },
                "similar_evidence": similar_evidence,
            })

        # Build prompt
        prompt = build_receipt_context_prompt(
            receipt_text=receipt_text,
            issues_with_context=issues_with_context,
            merchant_name=merchant_name,
            merchant_receipt_count=10,  # Number of training receipts used
            line_item_patterns=patterns,
        )

        # Call LLM
        logger.info(f"Calling LLM to review {len(issues)} issues...")
        ollama_api_key = os.environ.get("OLLAMA_API_KEY", "")
        ollama_base_url = os.environ.get("OLLAMA_BASE_URL")
        ollama_model = os.environ.get("OLLAMA_MODEL")

        llm = ChatOllama(
            model=ollama_model,
            base_url=ollama_base_url,
            client_kwargs={
                "headers": {"Authorization": f"Bearer {ollama_api_key}"},
                "timeout": 180,
            },
            temperature=0,
        )

        messages = [
            SystemMessage(content="You are a receipt label validation expert. Review each issue and respond with valid JSON."),
            HumanMessage(content=prompt),
        ]

        response = llm.invoke(messages)

        # Parse response
        decisions = parse_batched_llm_response(response.content, len(issues))
        logger.info(f"Got {len(decisions)} decisions from LLM")

        # Merge decisions with issues
        reviewed = []
        for i, issue in enumerate(issues):
            decision = decisions[i] if i < len(decisions) else {
                "decision": "NEEDS_REVIEW",
                "reasoning": "No decision from LLM",
                "confidence": "low",
            }
            reviewed.append({
                "issue": issue,
                "decision": decision.get("decision", "NEEDS_REVIEW"),
                "reasoning": decision.get("reasoning", ""),
                "suggested_label": decision.get("suggested_label"),
                "confidence": decision.get("confidence", "medium"),
            })

        return reviewed

    return _review(receipt_data, issues, patterns, chroma_client, dynamo_client)


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

    return "\n".join(lines)


def print_results(
    receipt_data: dict[str, Any],
    patterns: dict[str, Any],
    issues: list[Any],
    reviewed_issues: list[dict[str, Any]] | None = None,
    verbose: bool = False,
):
    """Print results."""
    from receipt_agent.agents.label_evaluator import assemble_receipt_text

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
        )
        for line in receipt_text.split("\n")[:40]:
            print(line)

    print(f"\n--- Issues Found: {len(issues)} ---")
    for i, issue in enumerate(issues[:15]):
        print(f"\n  [{i}] {issue.issue_type}")
        print(f"      Word: \"{issue.word.text}\"")
        print(f"      Current: {issue.current_label}")
        print(f"      Reason: {issue.reasoning[:80]}...")

    if reviewed_issues:
        print(f"\n--- LLM Review Decisions ---")
        valid_count = sum(1 for r in reviewed_issues if r["decision"] == "VALID")
        invalid_count = sum(1 for r in reviewed_issues if r["decision"] == "INVALID")
        review_count = sum(1 for r in reviewed_issues if r["decision"] == "NEEDS_REVIEW")
        print(f"VALID: {valid_count}, INVALID: {invalid_count}, NEEDS_REVIEW: {review_count}")

        for i, r in enumerate(reviewed_issues[:15]):
            issue = r["issue"]
            print(f"\n  [{i}] {r['decision']} ({r['confidence']})")
            print(f"      Word: \"{issue.word.text}\"")
            if r.get("suggested_label"):
                print(f"      Suggested: {r['suggested_label']}")
            print(f"      Reasoning: {r['reasoning'][:80]}...")

    print("\n" + "=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate labels for a single receipt (same as step function)"
    )
    parser.add_argument("image_id", help="The image ID")
    parser.add_argument("receipt_id", type=int, help="The receipt ID (0, 1, 2, ...)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--skip-patterns", action="store_true", help="Skip pattern discovery")
    parser.add_argument("--apply", action="store_true", help="Apply decisions to DynamoDB")
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

    # Use langsmith traceable for the main flow
    from langsmith import traceable

    @traceable(
        name="evaluate_single_receipt",
        project_name="label-evaluator-dev",
        metadata={
            "image_id": args.image_id,
            "receipt_id": args.receipt_id,
            "llm_review": True,
            "apply": args.apply,
        },
    )
    def run_full_pipeline(args, config, dynamo_client):
        # 1. Load receipt data
        receipt_data = load_receipt_data(dynamo_client, args.image_id, args.receipt_id)

        # 2. Pattern discovery
        if args.skip_patterns:
            from receipt_agent.agents.label_evaluator import get_default_patterns
            patterns = get_default_patterns(receipt_data["merchant_name"], "skipped")
        else:
            patterns = run_pattern_discovery(
                dynamo_client,
                receipt_data["merchant_name"],
                args.image_id,
                args.receipt_id,
            )

        logger.info(f"Receipt type: {patterns.get('receipt_type', 'unknown')}")
        logger.info(f"Item structure: {patterns.get('item_structure', 'unknown')}")

        # 3. Full label evaluation
        issues = run_full_evaluation(dynamo_client, receipt_data, patterns)

        # 4. LLM review
        reviewed_issues = None
        chroma_client = None

        if issues:
            # Download ChromaDB snapshot
            if config.get("chromadb_bucket"):
                s3_client = boto3.client("s3")
                chroma_path = os.path.join(tempfile.gettempdir(), "chromadb_cache")
                download_chromadb(s3_client, config["chromadb_bucket"], chroma_path)

                from receipt_chroma import ChromaClient
                chroma_client = ChromaClient(persist_directory=chroma_path)
                logger.info("ChromaDB client ready")
            else:
                logger.warning("chromadb_bucket not configured - skipping similarity search")

            reviewed_issues = run_llm_review(
                receipt_data,
                issues,
                patterns,
                chroma_client,
                dynamo_client,
            )

            # 5. Apply decisions (if requested)
            if args.apply and reviewed_issues:
                from receipt_agent.agents.label_evaluator import apply_llm_decisions

                logger.info("Applying decisions to DynamoDB...")
                stats = apply_llm_decisions(
                    reviewed_issues,
                    dynamo_client,
                    execution_id=f"dev-{args.image_id[:8]}",
                )
                logger.info(f"Applied: {stats}")

        return receipt_data, patterns, issues, reviewed_issues

    # Run the pipeline
    receipt_data, patterns, issues, reviewed_issues = run_full_pipeline(
        args, config, dynamo_client
    )

    # Print results
    print_results(receipt_data, patterns, issues, reviewed_issues, args.verbose)

    # Optional JSON output
    if args.output_json:
        output = {
            "image_id": args.image_id,
            "receipt_id": args.receipt_id,
            "merchant_name": receipt_data["merchant_name"],
            "patterns": patterns,
            "issues": [
                {
                    "issue_type": i.issue_type,
                    "word_text": i.word_text,
                    "current_label": i.current_label,
                    "reasoning": i.reasoning,
                }
                for i in issues
            ],
            "reviewed_issues": [
                {
                    "word_text": r["issue"].word_text,
                    "decision": r["decision"],
                    "reasoning": r["reasoning"],
                    "suggested_label": r.get("suggested_label"),
                    "confidence": r["confidence"],
                }
                for r in (reviewed_issues or [])
            ],
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

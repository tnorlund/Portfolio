#!/usr/bin/env python3
"""
Local test script for LLM label validation.

Usage:
    # Test with Grok 4.1 Fast:
    OPENROUTER_MODEL=x-ai/grok-4.1-fast \
    OPENROUTER_API_KEY=<your-key> \
    ~/.coreml-venv/bin/python3 scripts/test_label_validation.py \
        --image-id eecd3157-9c85-4ad9-9dd9-067390d6514e

    # Test with default model (openai/gpt-oss-120b):
    OPENROUTER_API_KEY=<your-key> \
    ~/.coreml-venv/bin/python3 scripts/test_label_validation.py \
        --image-id eecd3157-9c85-4ad9-9dd9-067390d6514e

    # Just print the prompt (no LLM call):
    ~/.coreml-venv/bin/python3 scripts/test_label_validation.py \
        --image-id eecd3157-9c85-4ad9-9dd9-067390d6514e \
        --prompt-only
"""

import argparse
import json
import os
import re
import sys
import time
from decimal import Decimal

import boto3

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TABLE_NAME = os.environ.get("DYNAMO_TABLE_NAME", "ReceiptsTable-dc5be22")


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def query_all(table, **kwargs):
    """Query DynamoDB with automatic pagination."""
    items = []
    resp = table.query(**kwargs)
    items.extend(resp["Items"])
    while "LastEvaluatedKey" in resp:
        resp = table.query(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp["Items"])
    return items


def parse_sk(sk: str):
    """Parse SK like RECEIPT#00001#LINE#00003#WORD#00002#LABEL#STORE_HOURS."""
    parts = sk.split("#")
    result = {}
    for i in range(0, len(parts) - 1, 2):
        result[parts[i].lower()] = parts[i + 1]
    return result


def load_receipt_data(table, image_id: str, receipt_id: int):
    """Load words and labels for a specific receipt from DynamoDB."""
    pk = f"IMAGE#{image_id}"

    # Query all items for this image
    all_items = query_all(
        table,
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": pk},
    )

    # Separate by type
    words = []
    labels = []
    for item in all_items:
        item_type = item.get("TYPE", "")
        sk = item.get("SK", "")

        if item_type == "RECEIPT_WORD":
            parsed = parse_sk(sk)
            rid = int(parsed.get("receipt", "0"))
            if rid == receipt_id:
                lid = int(parsed.get("line", "0"))
                wid = int(parsed.get("word", "0"))
                # Extract bounding box for position
                bb = item.get("bounding_box", {})
                x = float(bb.get("x", 0)) if bb else 0
                y = float(bb.get("y", 0)) if bb else 0
                w = float(bb.get("width", 0)) if bb else 0
                h = float(bb.get("height", 0)) if bb else 0
                words.append(
                    {
                        "text": item.get("text", ""),
                        "line_id": lid,
                        "word_id": wid,
                        "x": x + w / 2,  # center x
                        "y": y + h / 2,  # center y
                    }
                )

        elif item_type == "RECEIPT_WORD_LABEL":
            parsed = parse_sk(sk)
            rid = int(parsed.get("receipt", "0"))
            if rid == receipt_id:
                lid = int(parsed.get("line", "0"))
                wid = int(parsed.get("word", "0"))
                label_name = parsed.get("label", "")
                status = item.get("validation_status", "")
                # Find the matching word text
                word_text = ""
                for w_item in all_items:
                    if w_item.get("TYPE") == "RECEIPT_WORD":
                        w_parsed = parse_sk(w_item["SK"])
                        if (
                            int(w_parsed.get("receipt", "0")) == receipt_id
                            and int(w_parsed.get("line", "0")) == lid
                            and int(w_parsed.get("word", "0")) == wid
                        ):
                            word_text = w_item.get("text", "")
                            break

                labels.append(
                    {
                        "line_id": lid,
                        "word_id": wid,
                        "label": label_name,
                        "word_text": word_text,
                        "validation_status": status,
                    }
                )

    # Sort words by line_id, word_id
    words.sort(key=lambda w: (w["line_id"], w["word_id"]))
    labels.sort(key=lambda l: (l["line_id"], l["word_id"]))

    return words, labels


def main():
    parser = argparse.ArgumentParser(description="Test LLM label validation locally")
    parser.add_argument("--image-id", required=True, help="Image ID to test with")
    parser.add_argument(
        "--receipt-id", type=int, default=1, help="Receipt ID (default: 1)"
    )
    parser.add_argument(
        "--prompt-only",
        action="store_true",
        help="Just print the prompt, don't call LLM",
    )
    parser.add_argument(
        "--all-labels",
        action="store_true",
        help="Send ALL labels (not just PENDING) to the LLM",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override OPENROUTER_MODEL (e.g. x-ai/grok-4.1-fast)",
    )
    args = parser.parse_args()

    if args.model:
        os.environ["OPENROUTER_MODEL"] = args.model

    # Connect to DynamoDB
    dynamo = boto3.resource("dynamodb", region_name="us-east-1")
    table = dynamo.Table(TABLE_NAME)

    print(f"Loading data for image {args.image_id}, receipt {args.receipt_id}...")
    words, labels = load_receipt_data(table, args.image_id, args.receipt_id)

    print(f"  Words: {len(words)}")
    print(f"  Labels: {len(labels)}")

    # Show label summary
    from collections import Counter

    status_counts = Counter(l["validation_status"] for l in labels)
    label_counts = Counter(l["label"] for l in labels)
    print(f"  Status: {dict(status_counts)}")
    print(f"  Labels: {dict(label_counts)}")

    # Select pending labels (or all if --all-labels)
    if args.all_labels:
        pending = labels
        print(f"\n  Using ALL {len(pending)} labels")
    else:
        pending = [l for l in labels if l["validation_status"] == "PENDING"]
        print(f"\n  PENDING labels: {len(pending)}")

    if not pending:
        print("No pending labels to validate!")
        return

    for p in pending:
        print(f"    [{p['line_id']}:{p['word_id']}] '{p['word_text']}' -> {p['label']}")

    # Build prompt
    from receipt_upload.label_validation.llm_validator import (
        build_validation_prompt,
    )

    # Empty evidence (we skip ChromaDB in local test)
    similar_evidence = {f"{l['line_id']}_{l['word_id']}": [] for l in pending}

    prompt = build_validation_prompt(
        pending_labels=pending,
        words=words,
        similar_evidence=similar_evidence,
        merchant_name=None,
    )

    print(f"\n{'='*60}")
    print(f"PROMPT ({len(prompt)} chars)")
    print(f"{'='*60}")
    print(prompt)

    if args.prompt_only:
        return

    # Call LLM
    model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-oss-120b")
    print(f"\n{'='*60}")
    print(f"Calling LLM: {model}")
    print(f"{'='*60}")

    from receipt_agent.utils.llm_factory import create_llm

    llm = create_llm(timeout=60)
    print(f"Model: {model}")

    start = time.time()
    from langchain_core.messages import HumanMessage

    response = llm.invoke([HumanMessage(content=prompt)])
    elapsed = time.time() - start

    print(f"\nResponse ({elapsed:.1f}s):")
    print(response.content)

    # Try to parse as structured output
    print(f"\n{'='*60}")
    print("PARSED RESULTS")
    print(f"{'='*60}")
    try:
        from receipt_upload.label_validation.llm_validator import (
            parse_validation_response,
        )

        results = parse_validation_response(response.content, pending)
        for r in results:
            status_emoji = {"VALID": "+", "INVALID": "!", "NEEDS_REVIEW": "?"}
            symbol = status_emoji.get(r.decision, " ")
            print(
                f"  [{symbol}] {r.word_id}: {r.decision} -> {r.label} ({r.confidence}) - {r.reasoning}"
            )
    except Exception as e:
        print(f"  Parse error: {e}")


if __name__ == "__main__":
    main()

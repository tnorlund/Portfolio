#!/usr/bin/env python3
"""
Show all words from a receipt, including their labels.

Usage:
    python scripts/show_receipt_words.py <image_id> <receipt_id>
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data._pulumi import load_env, load_secrets
import os

def setup_environment():
    """Load secrets and outputs from Pulumi and set environment variables."""
    infra_dir = project_root / "infra"
    env = load_env("dev", working_dir=str(infra_dir))
    secrets = load_secrets("dev", working_dir=str(infra_dir))

    # DynamoDB
    table_name = env.get("dynamodb_table_name") or env.get("receipts_table_name")
    if table_name:
        os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = table_name
        os.environ["DYNAMODB_TABLE_NAME"] = table_name
        print(f"📊 DynamoDB Table: {table_name}")

    return table_name

def main():
    parser = argparse.ArgumentParser(description="Show all words from a receipt")
    parser.add_argument("image_id", help="Image ID")
    parser.add_argument("receipt_id", type=int, help="Receipt ID")
    args = parser.parse_args()

    # Setup environment
    table_name = setup_environment()
    if not table_name:
        print("❌ Failed to load DynamoDB table name")
        sys.exit(1)

    # Create DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Get receipt metadata
    metadata = dynamo_client.get_receipt_metadata(args.image_id, args.receipt_id)
    print(f"\n📄 Receipt: {args.image_id} / {args.receipt_id}")
    if metadata:
        print(f"   Merchant: {metadata.merchant_name or 'Unknown'}")
        date = getattr(metadata, 'date', None) or getattr(metadata, 'receipt_date', None)
        if date:
            print(f"   Date: {date}")

    # Get all words
    words = dynamo_client.list_receipt_words_from_receipt(
        image_id=args.image_id,
        receipt_id=args.receipt_id,
    )

    # Get all labels
    labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(
        image_id=args.image_id,
        receipt_id=args.receipt_id,
    )

    # Create label map
    label_map = {}
    for label in labels:
        key = (label.line_id, label.word_id)
        if key not in label_map:
            label_map[key] = []
        label_map[key].append({
            "label": label.label,
            "status": label.validation_status,
        })

    # Group words by line
    lines = {}
    for word in words:
        if word.line_id not in lines:
            lines[word.line_id] = []
        lines[word.line_id].append(word)

    # Sort lines by line_id
    sorted_lines = sorted(lines.items())

    print(f"\n📝 Words ({len(words)} total):")
    print("=" * 80)

    for line_id, line_words in sorted_lines:
        print(f"\nLine {line_id}:")
        for word in sorted(line_words, key=lambda w: w.word_id):
            word_labels = label_map.get((word.line_id, word.word_id), [])
            label_str = ", ".join([f"{l['label']}({l['status']})" for l in word_labels]) if word_labels else "NO LABEL"
            noise_str = " [NOISE]" if getattr(word, 'is_noise', False) else ""
            print(f"  Word {word.word_id:3d}: '{word.text:20s}' | Labels: {label_str}{noise_str}")

    # Summary
    labeled_words = len([w for w in words if (w.line_id, w.word_id) in label_map])
    unlabeled_words = len(words) - labeled_words
    noise_words = len([w for w in words if getattr(w, 'is_noise', False)])

    print(f"\n📊 Summary:")
    print(f"   Total words: {len(words)}")
    print(f"   Labeled: {labeled_words}")
    print(f"   Unlabeled: {unlabeled_words}")
    print(f"   Noise: {noise_words}")

if __name__ == "__main__":
    main()


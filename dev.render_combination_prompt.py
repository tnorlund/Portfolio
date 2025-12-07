#!/usr/bin/env python3
"""
Render the LLM combination prompt for a given image/target receipt.

Usage:
  python dev.render_combination_prompt.py --image-id <image> [--target-receipt-id <id>] [--table-name ...] [--out prompt.txt]

If --target-receipt-id is omitted, the script picks the first receipt without metadata (or falls back to the first receipt).
Requires env (Pulumi) or explicit table env var (DYNAMODB_TABLE_NAME), or --table-name.
"""

import argparse
import os
from pathlib import Path
from typing import List

from receipt_agent.agent.combination_selector import ReceiptCombinationSelector
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env, load_secrets
from receipt_dynamo.entities import Receipt


def pick_target_without_metadata(client: DynamoClient, image_id: str) -> int:
    receipts: List[Receipt] = client.get_receipts_from_image(image_id)
    if not receipts:
        raise SystemExit("No receipts found for that image.")
    for r in receipts:
        try:
            meta = client.get_receipt_metadata(image_id, r.receipt_id)
            if not meta or not (meta.merchant_name or meta.place_id):
                return r.receipt_id
        except Exception:
            return r.receipt_id
    return receipts[0].receipt_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Render combination prompt")
    parser.add_argument("--image-id", required=True)
    parser.add_argument(
        "--target-receipt-id",
        type=int,
        help="Anchor receipt; if omitted, pick one without metadata",
    )
    parser.add_argument(
        "--table-name", help="DynamoDB table name (overrides env)"
    )
    parser.add_argument("--out", type=Path, default=Path("prompt.txt"))
    args = parser.parse_args()

    # Hydrate env to pick up DYNAMODB_TABLE_NAME if available
    try:
        stack_env = load_env()
        _ = load_secrets()
        table = stack_env.get("dynamodb_table_name") or stack_env.get(
            "receipts_table_name"
        )
        if table:
            os.environ.setdefault("DYNAMODB_TABLE_NAME", table)
    except Exception:
        pass

    table_name = args.table_name or os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise SystemExit(
            "DYNAMODB_TABLE_NAME not set; set env or use --table-name"
        )

    client = DynamoClient(table_name)
    target_id = args.target_receipt_id or pick_target_without_metadata(
        client, args.image_id
    )

    selector = ReceiptCombinationSelector(client)
    candidates = selector.build_candidates(args.image_id, target_id)
    if not candidates:
        raise SystemExit("No candidates found for that image/target")

    prompt = selector._build_prompt(candidates)  # noqa: SLF001 - dev helper
    args.out.write_text(prompt, encoding="utf-8")
    print(f"Wrote prompt to {args.out}")

    # Also echo to stdout (truncated)
    preview = "\n".join(prompt.splitlines()[:80])
    print("\n=== Metadata ===")
    print(f"image_id: {args.image_id}")
    print(f"target_receipt_id: {target_id}")
    print(f"candidate_pairs: {[c['combo'] for c in candidates]}")
    print(f"prompt_length: {len(prompt)}")
    print("\n=== Prompt Preview (first ~80 lines) ===\n")
    print(preview)


if __name__ == "__main__":
    main()

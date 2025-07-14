#!/usr/bin/env python3
"""
Minimal export script for receipt data - wraps receipt_dynamo's export_image.

This script fulfills the Makefile's expectation from PR #215.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_dynamo.data.export_image import export_image
from receipt_dynamo.data.dynamo_client import DynamoClient


def main():
    parser = argparse.ArgumentParser(description="Export receipt data")
    subparsers = parser.add_subparsers(dest="command")

    sample_parser = subparsers.add_parser("sample")
    sample_parser.add_argument("--size", type=int, default=20)
    sample_parser.add_argument("--output-dir", default="./receipt_data")

    args = parser.parse_args()

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        sys.exit(1)

    if args.command == "sample":
        client = DynamoClient(table_name)
        # Get sample images
        response = client.query_by_type("IMAGE", limit=args.size)

        for i, item in enumerate(response[: args.size]):
            image_id = item.get("SK", "").replace("IMAGE#", "")
            if image_id:
                try:
                    export_image(table_name, image_id, args.output_dir)
                    print(f"Exported {i+1}/{args.size}: {image_id}")
                except Exception as e:
                    print(f"Failed {image_id}: {e}")


if __name__ == "__main__":
    main()

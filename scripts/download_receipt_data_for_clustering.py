#!/usr/bin/env python3
"""
Download receipt data from DynamoDB to local JSON files for faster development/debugging.

This script downloads all receipt-related data (Receipt, ReceiptLine, ReceiptWord) for
specified images and saves them as JSON files. It checks if files already exist to avoid
re-downloading.

Usage:
    python scripts/download_receipt_data_for_clustering.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --image-id 752cf8e2-cb69-4643-8f28-0ac9e3d2df25 \
        --output-dir ./local_receipt_data
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import Receipt, ReceiptLine, ReceiptWord


def setup_environment() -> str:
    """Load environment variables and return DynamoDB table name."""
    import os

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")

    # Try loading from Pulumi if not set
    if not table_name:
        try:
            from receipt_dynamo.data._pulumi import load_env
            project_root = Path(__file__).parent.parent
            infra_dir = project_root / "infra"
            env = load_env("dev", working_dir=str(infra_dir))
            table_name = env.get("dynamodb_table_name") or env.get("receipts_table_name")
            if table_name:
                os.environ["DYNAMODB_TABLE_NAME"] = table_name
                print(f"📊 DynamoDB Table (from Pulumi): {table_name}")
        except Exception as e:
            print(f"⚠️  Could not load from Pulumi: {e}")

    if not table_name:
        print("⚠️  DYNAMODB_TABLE_NAME not set in environment")
        print("   Set it with: export DYNAMODB_TABLE_NAME=your-table-name")
        print("   Or ensure Pulumi config is available")
        return None

    if not os.environ.get("DYNAMODB_TABLE_NAME"):
        print(f"📊 DynamoDB Table: {table_name}")

    return table_name


def save_entity_to_json(entity, filepath: Path) -> None:
    """Save an entity to a JSON file using dict(entity)."""
    data = dict(entity)
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2, default=str)


def load_entity_from_json(entity_class, filepath: Path):
    """Load an entity from a JSON file using entity_class(**json_data)."""
    with open(filepath, 'r') as f:
        data = json.load(f)
    return entity_class(**data)


def download_receipt_data(
    client: DynamoClient,
    image_id: str,
    output_dir: Path,
    force: bool = False,
) -> Dict[str, int]:
    """
    Download all receipt data for an image.

    Returns:
        Dict with counts: {'receipts': N, 'lines': M, 'words': K}
    """
    print(f"\n📥 Downloading data for image: {image_id}")

    # Create image-specific directory
    image_dir = output_dir / image_id
    image_dir.mkdir(parents=True, exist_ok=True)

    # Get all receipts for this image
    receipts_file = image_dir / "receipts.json"
    if receipts_file.exists() and not force:
        print(f"   ✓ Receipts already exist: {receipts_file}")
        receipts = []
        with open(receipts_file, 'r') as f:
            receipts_data = json.load(f)
            for r_data in receipts_data:
                receipts.append(Receipt(**r_data))
    else:
        print(f"   📥 Fetching receipts from DynamoDB...")
        # Note: get_receipts_from_image expects str but type hint says int - using str
        receipts = client.get_receipts_from_image(image_id)  # type: ignore
        receipts_data = [dict(r) for r in receipts]
        with open(receipts_file, 'w') as f:
            json.dump(receipts_data, f, indent=2, default=str)
        print(f"   ✓ Saved {len(receipts)} receipts to {receipts_file}")

    if not receipts:
        print(f"   ⚠️  No receipts found for image {image_id}")
        return {'receipts': 0, 'lines': 0, 'words': 0}

    total_lines = 0
    total_words = 0

    # Download lines and words for each receipt
    for receipt in receipts:
        receipt_id = receipt.receipt_id
        receipt_dir = image_dir / f"receipt_{receipt_id:05d}"
        receipt_dir.mkdir(exist_ok=True)

        # Download receipt lines
        lines_file = receipt_dir / "lines.json"
        if lines_file.exists() and not force:
            print(f"   ✓ Lines already exist for receipt {receipt_id}: {lines_file}")
            with open(lines_file, 'r') as f:
                lines_data = json.load(f)
                lines = [ReceiptLine(**ld) for ld in lines_data]
        else:
            print(f"   📥 Fetching lines for receipt {receipt_id}...")
            lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
            lines_data = [dict(l) for l in lines]
            with open(lines_file, 'w') as f:
                json.dump(lines_data, f, indent=2, default=str)
            print(f"   ✓ Saved {len(lines)} lines to {lines_file}")

        total_lines += len(lines)

        # Download receipt words
        words_file = receipt_dir / "words.json"
        if words_file.exists() and not force:
            print(f"   ✓ Words already exist for receipt {receipt_id}: {words_file}")
            with open(words_file, 'r') as f:
                words_data = json.load(f)
                words = [ReceiptWord(**wd) for wd in words_data]
        else:
            print(f"   📥 Fetching words for receipt {receipt_id}...")
            words = client.list_receipt_words_from_receipt(image_id, receipt_id)
            words_data = [dict(w) for w in words]
            with open(words_file, 'w') as f:
                json.dump(words_data, f, indent=2, default=str)
            print(f"   ✓ Saved {len(words)} words to {words_file}")

        total_words += len(words)

    return {
        'receipts': len(receipts),
        'lines': total_lines,
        'words': total_words,
    }


def perform_angle_consistency_check(
    image_id: str,
    output_dir: Path,
    angle_tolerance: float = 5.0,
) -> Dict[str, any]:
    """
    Perform angle consistency check on receipt-based OCR records.

    Returns:
        Dict with analysis results for each receipt
    """
    print(f"\n🔍 Performing angle consistency check for image: {image_id}")
    print(f"   Angle tolerance: {angle_tolerance}°")

    image_dir = output_dir / image_id

    # Load receipts
    receipts_file = image_dir / "receipts.json"
    if not receipts_file.exists():
        print(f"   ❌ Receipts file not found: {receipts_file}")
        return {}

    with open(receipts_file, 'r') as f:
        receipts_data = json.load(f)
        receipts = [Receipt(**r_data) for r_data in receipts_data]

    results = {}

    for receipt in receipts:
        receipt_id = receipt.receipt_id
        receipt_dir = image_dir / f"receipt_{receipt_id:05d}"

        # Load lines
        lines_file = receipt_dir / "lines.json"
        if not lines_file.exists():
            print(f"   ⚠️  Lines file not found for receipt {receipt_id}")
            continue

        with open(lines_file, 'r') as f:
            lines_data = json.load(f)
            lines = [ReceiptLine(**ld) for ld in lines_data]

        if len(lines) < 2:
            print(f"   ⚠️  Receipt {receipt_id} has fewer than 2 lines, skipping")
            continue

        # Calculate mean angle (handle wraparound at 180/-180)
        angles = [ln.angle_degrees for ln in lines]
        mean_angle = _circular_mean(angles)

        # Find lines with angles outside tolerance
        outliers = []
        for line in lines:
            angle_diff = _angle_difference(line.angle_degrees, mean_angle)
            if angle_diff > angle_tolerance:
                outliers.append({
                    'line_id': line.line_id,
                    'angle': line.angle_degrees,
                    'difference': angle_diff,
                    'text': line.text[:50],  # First 50 chars
                })

        # Calculate statistics
        angle_diffs = [_angle_difference(ln.angle_degrees, mean_angle) for ln in lines]
        max_diff = max(angle_diffs) if angle_diffs else 0
        mean_diff = sum(angle_diffs) / len(angle_diffs) if angle_diffs else 0

        results[receipt_id] = {
            'receipt_id': receipt_id,
            'total_lines': len(lines),
            'mean_angle': mean_angle,
            'mean_angle_difference': mean_diff,
            'max_angle_difference': max_diff,
            'outlier_count': len(outliers),
            'outliers': outliers,
            'is_consistent': len(outliers) == 0,
        }

        # Print summary
        status = "✓" if len(outliers) == 0 else "⚠️"
        print(f"   {status} Receipt {receipt_id}: {len(lines)} lines, "
              f"mean angle: {mean_angle:.1f}°, "
              f"max diff: {max_diff:.1f}°, "
              f"outliers: {len(outliers)}")

        if outliers:
            print(f"      Outliers:")
            for outlier in outliers[:5]:  # Show first 5
                print(f"        Line {outlier['line_id']}: "
                      f"angle={outlier['angle']:.1f}° "
                      f"(diff={outlier['difference']:.1f}°), "
                      f"text='{outlier['text']}'")
            if len(outliers) > 5:
                print(f"        ... and {len(outliers) - 5} more")

    return results


def _circular_mean(angles: List[float]) -> float:
    """Calculate mean of angles handling wraparound."""
    import math
    angles_rad = [math.radians(a) for a in angles]
    mean_sin = sum(math.sin(a) for a in angles_rad) / len(angles_rad)
    mean_cos = sum(math.cos(a) for a in angles_rad) / len(angles_rad)
    return math.degrees(math.atan2(mean_sin, mean_cos))


def _angle_difference(a1: float, a2: float) -> float:
    """Calculate smallest angle difference between two angles."""
    diff = abs(a1 - a2)
    return min(diff, 360 - diff)


def main():
    parser = argparse.ArgumentParser(
        description="Download receipt data from DynamoDB and perform angle consistency checks"
    )
    parser.add_argument(
        "--image-id",
        action="append",
        required=True,
        help="Image ID to download (can be specified multiple times)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./local_receipt_data"),
        help="Output directory for JSON files (default: ./local_receipt_data)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if files exist",
    )
    parser.add_argument(
        "--angle-tolerance",
        type=float,
        default=5.0,
        help="Angle tolerance in degrees for consistency check (default: 5.0)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip download, only perform angle consistency check",
    )

    args = parser.parse_args()

    # Setup environment
    table_name = setup_environment()
    if not table_name:
        print("❌ Failed to load DynamoDB table name")
        sys.exit(1)

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n📁 Output directory: {args.output_dir.absolute()}")

    # Create DynamoDB client
    if not args.skip_download:
        client = DynamoClient(table_name)

    # Download data for each image
    all_results = {}
    for image_id in args.image_id:
        if not args.skip_download:
            counts = download_receipt_data(
                client,
                image_id,
                args.output_dir,
                force=args.force,
            )
            print(f"\n📊 Summary for {image_id}:")
            print(f"   Receipts: {counts['receipts']}")
            print(f"   Lines: {counts['lines']}")
            print(f"   Words: {counts['words']}")

        # Perform angle consistency check
        results = perform_angle_consistency_check(
            image_id,
            args.output_dir,
            angle_tolerance=args.angle_tolerance,
        )
        all_results[image_id] = results

    # Save analysis results
    results_file = args.output_dir / "angle_consistency_results.json"
    with open(results_file, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n💾 Saved analysis results to: {results_file}")

    # Print summary
    print("\n" + "=" * 80)
    print("📊 ANGLE CONSISTENCY SUMMARY")
    print("=" * 80)

    for image_id, results in all_results.items():
        print(f"\nImage: {image_id}")
        consistent_count = sum(1 for r in results.values() if r['is_consistent'])
        total_count = len(results)
        print(f"  Receipts: {total_count} total, {consistent_count} consistent, "
              f"{total_count - consistent_count} with outliers")

        for receipt_id, result in results.items():
            if not result['is_consistent']:
                print(f"    ⚠️  Receipt {receipt_id}: {result['outlier_count']} outliers "
                      f"(max diff: {result['max_angle_difference']:.1f}°)")


if __name__ == "__main__":
    main()


#!/usr/bin/env python3
"""
Split a receipt into multiple receipts, re-OCR each split, and map labels.

This script orchestrates the complete workflow:
1. Split receipt using clustering logic
2. Export JSON and clean images for each split
3. Re-OCR each cropped receipt image
4. Map old receipt labels to new OCR data
5. Optionally save mapped labels to DynamoDB

Usage:
    python scripts/split_receipt_and_map_labels.py \
        --image-id <image_id> \
        --receipt-id <receipt_id> \
        [--output-dir <dir>] \
        [--dry-run]
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

from receipt_dynamo import DynamoClient
from scripts.split_receipt import setup_environment
from scripts.visualize_final_clusters_cropped import visualize_final_clusters_cropped

# Import re-OCR functionality
try:
    from scripts.re_ocr_receipt_and_map_labels import (
        re_ocr_receipt_and_map_labels,
    )
    RE_OCR_AVAILABLE = True
except ImportError as e:
    RE_OCR_AVAILABLE = False
    print(f"⚠️  re_ocr_receipt_and_map_labels not available: {e}")


def split_receipt_and_map_labels(
    image_id: str,
    receipt_id: int,
    output_dir: Optional[Path] = None,
    dry_run: bool = True,
    x_eps: Optional[float] = None,
    angle_tolerance: Optional[float] = None,
    vertical_gap_threshold: Optional[float] = None,
    reassign_x_proximity_threshold: Optional[float] = None,
    reassign_y_proximity_threshold: Optional[float] = None,
    vertical_proximity_threshold: Optional[float] = None,
    vertical_x_proximity_threshold: Optional[float] = None,
    merge_x_proximity_threshold: Optional[float] = None,
    merge_min_score: Optional[float] = None,
    join_iou_threshold: Optional[float] = None,
) -> None:
    """
    Split a receipt into multiple receipts, re-OCR each, and map labels.
    
    Args:
        image_id: Image ID
        receipt_id: Receipt ID to split
        output_dir: Output directory for exports and visualizations
        dry_run: If True, don't save labels to DynamoDB
        x_eps, angle_tolerance, etc.: Clustering parameters (optional)
    """
    env = setup_environment()
    table_name = env.get("dynamo_table_name") or "ReceiptsTable-dc5be22"
    client = DynamoClient(table_name)
    raw_bucket = env.get("raw_bucket") or "raw-image-bucket-c779c32"
    
    # Set up output directory
    if output_dir is None:
        output_dir = Path(f"split_receipts_output_{image_id[:8]}")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print("=" * 70)
    print(f"SPLIT RECEIPT AND MAP LABELS")
    print("=" * 70)
    print(f"Image ID: {image_id}")
    print(f"Receipt ID: {receipt_id}")
    print(f"Output Directory: {output_dir}")
    print(f"Dry Run: {dry_run}")
    print()
    
    # Step 1: Split receipt and export JSON/images
    print("📊 Step 1: Splitting receipt and exporting clusters...")
    print("-" * 70)
    
    visualize_final_clusters_cropped(
        image_id=image_id,
        output_dir=output_dir,
        raw_bucket=raw_bucket,
        receipt_id=receipt_id,
        x_eps=x_eps,
        angle_tolerance=angle_tolerance,
        vertical_gap_threshold=vertical_gap_threshold,
        reassign_x_proximity_threshold=reassign_x_proximity_threshold,
        reassign_y_proximity_threshold=reassign_y_proximity_threshold,
        vertical_proximity_threshold=vertical_proximity_threshold,
        vertical_x_proximity_threshold=vertical_x_proximity_threshold,
        merge_x_proximity_threshold=merge_x_proximity_threshold,
        merge_min_score=merge_min_score,
        join_iou_threshold=join_iou_threshold,
    )
    print()
    
    # Step 2: Find all exported JSON files and re-OCR each cluster
    print("🔍 Step 2: Re-OCRing each split receipt and mapping labels...")
    print("-" * 70)
    
    # Find all receipt_*_ocr_export.json files
    json_files = sorted(output_dir.glob("receipt_*_ocr_export.json"))
    
    if not json_files:
        print("⚠️  No JSON export files found. Cannot proceed with re-OCR.")
        return
    
    print(f"   Found {len(json_files)} split receipt(s) to process")
    print()
    
    # Process each split receipt
    for json_file in json_files:
        # Extract cluster/receipt number from filename (e.g., receipt_1_ocr_export.json -> 1)
        cluster_id = json_file.stem.replace("receipt_", "").replace("_ocr_export", "")
        
        try:
            cluster_num = int(cluster_id)
        except ValueError:
            print(f"⚠️  Could not parse cluster ID from {json_file.name}, skipping")
            continue
        
        # Determine new receipt ID (use cluster number, or receipt_id + cluster_num)
        new_receipt_id = receipt_id + cluster_num
        
        # Find corresponding clean image
        clean_image_path = output_dir / f"receipt_{cluster_id}_clean.png"
        
        if not clean_image_path.exists():
            print(f"⚠️  Clean image not found: {clean_image_path}, skipping cluster {cluster_id}")
            continue
        
        print(f"📋 Processing cluster {cluster_id} (new receipt_id: {new_receipt_id})...")
        print(f"   JSON: {json_file.name}")
        print(f"   Image: {clean_image_path.name}")
        
        # Re-OCR and map labels
        if RE_OCR_AVAILABLE:
            try:
                re_ocr_receipt_and_map_labels(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    new_receipt_id=new_receipt_id,
                    cropped_image_path=clean_image_path,
                    ocr_export_json_path=json_file,
                    dry_run=dry_run,
                    output_dir=output_dir,
                )
            except Exception as e:
                print(f"   ❌ Error processing cluster {cluster_id}: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"   ⚠️  Re-OCR functionality not available, skipping")
        
        print()
    
    print("=" * 70)
    print("✅ Complete!")
    print(f"   Output directory: {output_dir}")
    print(f"   Processed {len(json_files)} split receipt(s)")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Split a receipt into multiple receipts, re-OCR each, and map labels"
    )
    parser.add_argument("--image-id", required=True, help="Image ID")
    parser.add_argument("--receipt-id", type=int, required=True, help="Receipt ID to split")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory for exports and visualizations",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Don't save labels to DynamoDB (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Actually save labels to DynamoDB",
    )
    
    # Clustering parameters (optional, will use defaults if not specified)
    parser.add_argument(
        "--x-eps",
        type=float,
        help="X-axis epsilon for DBSCAN clustering",
    )
    parser.add_argument(
        "--angle-tolerance",
        type=float,
        help="Angle tolerance for line clustering (degrees)",
    )
    parser.add_argument(
        "--vertical-gap-threshold",
        type=float,
        help="Vertical gap threshold for clustering",
    )
    parser.add_argument(
        "--reassign-x-proximity-threshold",
        type=float,
        help="X-proximity threshold for reassigning lines",
    )
    parser.add_argument(
        "--reassign-y-proximity-threshold",
        type=float,
        help="Y-proximity threshold for reassigning lines",
    )
    parser.add_argument(
        "--vertical-proximity-threshold",
        type=float,
        help="Vertical proximity threshold",
    )
    parser.add_argument(
        "--vertical-x-proximity-threshold",
        type=float,
        help="Vertical X-proximity threshold",
    )
    parser.add_argument(
        "--merge-x-proximity-threshold",
        type=float,
        help="X-proximity threshold for merging clusters",
    )
    parser.add_argument(
        "--merge-min-score",
        type=float,
        help="Minimum score for merging clusters",
    )
    parser.add_argument(
        "--join-iou-threshold",
        type=float,
        help="IoU threshold for joining clusters",
    )
    
    args = parser.parse_args()
    
    split_receipt_and_map_labels(
        args.image_id,
        args.receipt_id,
        args.output_dir,
        args.dry_run,
        args.x_eps,
        args.angle_tolerance,
        args.vertical_gap_threshold,
        args.reassign_x_proximity_threshold,
        args.reassign_y_proximity_threshold,
        args.vertical_proximity_threshold,
        args.vertical_x_proximity_threshold,
        args.merge_x_proximity_threshold,
        args.merge_min_score,
        args.join_iou_threshold,
    )


if __name__ == "__main__":
    main()


#!/usr/bin/env python3
"""
Compare receipt word labels across duplicate images and create a merge plan.

This script:
1. Loads the duplicate analysis report
2. For each duplicate group, fetches all receipt word labels
3. Compares labels across duplicate copies
4. Identifies which labels to keep/merge
5. Creates a detailed comparison report and merge plan

Usage:
    python scripts/compare_duplicate_labels.py --stack dev --report duplicate_analysis/duplicate_analysis_report.json
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data._pulumi import load_env

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class LabelComparison:
    """Comparison of labels for a specific word position across duplicate images."""

    receipt_id: int
    line_id: int
    word_id: int
    labels_by_image: Dict[str, Dict]  # image_id -> label data
    is_identical: bool
    is_conflicting: bool  # Different labels for same word
    is_unique: bool  # Only exists in one image


@dataclass
class DuplicateGroupComparison:
    """Comparison of labels across a duplicate group."""

    sha256: str
    image_ids: List[str]
    canonical_image_id: str  # Image to keep
    images_to_delete: List[str]
    total_labels: int
    unique_labels: int  # Labels unique to one image
    identical_labels: int  # Labels identical across all
    conflicting_labels: int  # Different labels for same word
    label_comparisons: List[LabelComparison]
    merge_plan: Dict


def get_table_name(stack: str) -> str:
    """Get the DynamoDB table name from Pulumi stack."""
    logger.info(f"Getting {stack.upper()} configuration from Pulumi...")

    env = load_env(env=stack)
    table_name = env.get("dynamodb_table_name")

    if not table_name:
        raise ValueError(
            f"Could not find dynamodb_table_name in Pulumi {stack} stack outputs"
        )

    logger.info(f"{stack.upper()} table: {table_name}")
    return table_name


def get_label_key(label) -> Tuple[int, int, int, str]:
    """Get a unique key for a label based on position and label type."""
    return (
        label.receipt_id,
        label.line_id,
        label.word_id,
        label.label,
    )


def compare_labels_for_group(
    dynamo_client: DynamoClient, image_ids: List[str]
) -> DuplicateGroupComparison:
    """Compare labels across duplicate images in a group."""
    logger.info(f"Comparing labels for {len(image_ids)} duplicate images...")

    # Fetch all labels for each image
    all_labels_by_image: Dict[str, List] = {}
    for image_id in image_ids:
        try:
            details = dynamo_client.get_image_details(image_id)
            all_labels_by_image[image_id] = details.receipt_word_labels
            logger.debug(
                f"  {image_id}: {len(details.receipt_word_labels)} labels"
            )
        except Exception as e:
            logger.error(f"Failed to get labels for {image_id}: {e}")
            all_labels_by_image[image_id] = []

    # Group labels by word position (receipt_id, line_id, word_id)
    labels_by_position: Dict[Tuple[int, int, int], Dict[str, List]] = defaultdict(
        lambda: defaultdict(list)
    )

    for image_id, labels in all_labels_by_image.items():
        for label in labels:
            position = (label.receipt_id, label.line_id, label.word_id)
            labels_by_position[position][image_id].append(label)

    # Compare labels at each position
    label_comparisons: List[LabelComparison] = []
    identical_count = 0
    unique_count = 0
    conflicting_count = 0

    for position, labels_by_img in labels_by_position.items():
        receipt_id, line_id, word_id = position

        # Check if labels are identical across all images
        all_label_keys = set()
        for img_id, labels in labels_by_img.items():
            for label in labels:
                all_label_keys.add(get_label_key(label))

        # Check if this position exists in all images
        images_with_position = set(labels_by_img.keys())
        is_unique = len(images_with_position) < len(image_ids)

        # Check for conflicts (same word position, different labels)
        label_types_by_image = {}
        for img_id, labels in labels_by_img.items():
            label_types = set(label.label for label in labels)
            label_types_by_image[img_id] = label_types

        # Check if all images have the same labels for this position
        if len(label_types_by_image) > 1:
            # Get the first image that has labels at this position as reference
            reference_image = next(iter(label_types_by_image.keys()))
            reference_labels = label_types_by_image[reference_image]
            all_same = all(
                label_types_by_image[img_id] == reference_labels
                for img_id in image_ids
                if img_id in label_types_by_image
            )
        else:
            all_same = True

        is_identical = all_same and not is_unique
        is_conflicting = (
            not all_same
            and len(set(frozenset(lt) for lt in label_types_by_image.values())) > 1
        )

        if is_identical:
            identical_count += 1
        elif is_unique:
            unique_count += 1
        elif is_conflicting:
            conflicting_count += 1

        # Create label data dict for comparison
        labels_data = {}
        for img_id, labels in labels_by_img.items():
            labels_data[img_id] = [
                {
                    "label": label.label,
                    "reasoning": label.reasoning,
                    "validation_status": label.validation_status,
                    "label_proposed_by": label.label_proposed_by,
                    "label_consolidated_from": label.label_consolidated_from,
                    "timestamp_added": str(label.timestamp_added),
                }
                for label in labels
            ]

        label_comparisons.append(
            LabelComparison(
                receipt_id=receipt_id,
                line_id=line_id,
                word_id=word_id,
                labels_by_image=labels_data,
                is_identical=is_identical,
                is_conflicting=is_conflicting,
                is_unique=is_unique,
            )
        )

    # Choose canonical image (keep the one with most labels, or earliest if tie)
    image_label_counts = {
        img_id: len(labels) for img_id, labels in all_labels_by_image.items()
    }
    canonical_image_id = max(
        image_label_counts.items(), key=lambda x: (x[1], -image_ids.index(x[0]))
    )[0]
    images_to_delete = [img_id for img_id in image_ids if img_id != canonical_image_id]

    # Count total unique labels
    all_label_keys = set()
    for labels in all_labels_by_image.values():
        for label in labels:
            all_label_keys.add(get_label_key(label))

    return DuplicateGroupComparison(
        sha256="",  # Will be set by caller
        image_ids=image_ids,
        canonical_image_id=canonical_image_id,
        images_to_delete=images_to_delete,
        total_labels=len(all_label_keys),
        unique_labels=unique_count,
        identical_labels=identical_count,
        conflicting_labels=conflicting_count,
        label_comparisons=label_comparisons,
        merge_plan={},  # Will be populated
    )


def create_merge_plan(comparison: DuplicateGroupComparison) -> Dict:
    """Create a detailed merge plan for consolidating labels."""
    plan = {
        "canonical_image_id": comparison.canonical_image_id,
        "images_to_delete": comparison.images_to_delete,
        "labels_to_add": [],
        "labels_to_update": [],
        "conflicts_to_resolve": [],
    }

    canonical_labels = {
        (comp.receipt_id, comp.line_id, comp.word_id): comp.labels_by_image.get(
            comparison.canonical_image_id, []
        )
        for comp in comparison.label_comparisons
    }

    for comp in comparison.label_comparisons:
        position = (comp.receipt_id, comp.line_id, comp.word_id)
        canonical_labels_at_pos = canonical_labels.get(position, [])

        # Check each duplicate image
        for img_id in comparison.images_to_delete:
            if img_id not in comp.labels_by_image:
                continue

            duplicate_labels = comp.labels_by_image[img_id]

            # For each label in the duplicate
            for dup_label in duplicate_labels:
                # Check if canonical already has this exact label
                canonical_has_label = any(
                    can_label["label"] == dup_label["label"]
                    for can_label in canonical_labels_at_pos
                )

                if not canonical_has_label:
                    # Need to add this label to canonical
                    new_label = dup_label.copy()
                    new_label["label_consolidated_from"] = img_id
                    plan["labels_to_add"].append(
                        {
                            "receipt_id": comp.receipt_id,
                            "line_id": comp.line_id,
                            "word_id": comp.word_id,
                            "label_data": new_label,
                            "source_image_id": img_id,
                        }
                    )
                elif comp.is_conflicting:
                    # Conflict: same word position, different labels
                    plan["conflicts_to_resolve"].append(
                        {
                            "receipt_id": comp.receipt_id,
                            "line_id": comp.line_id,
                            "word_id": comp.word_id,
                            "canonical_labels": canonical_labels_at_pos,
                            "duplicate_labels": duplicate_labels,
                            "source_image_id": img_id,
                        }
                    )

    return plan


def analyze_duplicate_labels(
    table_name: str, report_path: str, output_dir: str
) -> Dict:
    """Analyze and compare labels across duplicate images."""
    client = DynamoClient(table_name)

    # Load duplicate report
    with open(report_path, "r") as f:
        duplicate_report = json.load(f)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info(f"Analyzing {len(duplicate_report['duplicate_groups'])} duplicate groups...")

    # Compare labels for each duplicate group
    group_comparisons: List[DuplicateGroupComparison] = []

    for i, group in enumerate(duplicate_report["duplicate_groups"], 1):
        logger.info(
            f"\nProcessing group {i}/{len(duplicate_report['duplicate_groups'])}: "
            f"{len(group['image_ids'])} duplicates"
        )

        comparison = compare_labels_for_group(client, group["image_ids"])
        comparison.sha256 = group["sha256"]
        comparison.merge_plan = create_merge_plan(comparison)
        group_comparisons.append(comparison)

    # Generate detailed report
    report = {
        "summary": {
            "total_duplicate_groups": len(group_comparisons),
            "total_duplicate_images": sum(
                len(g.images_to_delete) for g in group_comparisons
            ),
            "total_labels_to_preserve": sum(g.total_labels for g in group_comparisons),
            "total_conflicts": sum(g.conflicting_labels for g in group_comparisons),
        },
        "group_comparisons": [
            {
                "sha256": g.sha256,
                "image_ids": g.image_ids,
                "canonical_image_id": g.canonical_image_id,
                "images_to_delete": g.images_to_delete,
                "statistics": {
                    "total_labels": g.total_labels,
                    "identical_labels": g.identical_labels,
                    "unique_labels": g.unique_labels,
                    "conflicting_labels": g.conflicting_labels,
                },
                "merge_plan": g.merge_plan,
                "label_comparisons": [
                    {
                        "receipt_id": comp.receipt_id,
                        "line_id": comp.line_id,
                        "word_id": comp.word_id,
                        "is_identical": comp.is_identical,
                        "is_conflicting": comp.is_conflicting,
                        "is_unique": comp.is_unique,
                        "labels_by_image": comp.labels_by_image,
                    }
                    for comp in g.label_comparisons
                ],
            }
            for g in group_comparisons
        ],
    }

    # Save report
    report_path = output_path / "label_comparison_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info(f"Report saved to {report_path}")

    # Generate summary text
    summary_path = output_path / "label_comparison_summary.txt"
    with open(summary_path, "w") as f:
        f.write("=" * 80 + "\n")
        f.write("RECEIPT WORD LABEL COMPARISON SUMMARY\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Total duplicate groups: {report['summary']['total_duplicate_groups']}\n")
        f.write(
            f"Total duplicate images to delete: {report['summary']['total_duplicate_images']}\n"
        )
        f.write(
            f"Total unique labels to preserve: {report['summary']['total_labels_to_preserve']}\n"
        )
        f.write(f"Total conflicts to resolve: {report['summary']['total_conflicts']}\n")
        f.write("\n" + "=" * 80 + "\n")
        f.write("DETAILED GROUP ANALYSIS\n")
        f.write("=" * 80 + "\n\n")

        for i, group in enumerate(report["group_comparisons"], 1):
            f.write(f"\nGroup {i}: {len(group['image_ids'])} duplicates\n")
            f.write(f"  SHA256: {group['sha256']}\n")
            f.write(f"  Canonical (keep): {group['canonical_image_id']}\n")
            f.write(f"  To delete: {', '.join(group['images_to_delete'])}\n")
            f.write(f"  Statistics:\n")
            f.write(f"    Total unique labels: {group['statistics']['total_labels']}\n")
            f.write(
                f"    Identical across all: {group['statistics']['identical_labels']}\n"
            )
            f.write(
                f"    Unique to one image: {group['statistics']['unique_labels']}\n"
            )
            f.write(
                f"    Conflicts (different labels): {group['statistics']['conflicting_labels']}\n"
            )
            f.write(f"  Merge plan:\n")
            f.write(
                f"    Labels to add: {len(group['merge_plan']['labels_to_add'])}\n"
            )
            f.write(
                f"    Conflicts to resolve: {len(group['merge_plan']['conflicts_to_resolve'])}\n"
            )

            if group["merge_plan"]["conflicts_to_resolve"]:
                f.write(f"  ⚠️  CONFLICTS FOUND:\n")
                for conflict in group["merge_plan"]["conflicts_to_resolve"][:5]:
                    f.write(
                        f"    Receipt {conflict['receipt_id']}, Line {conflict['line_id']}, "
                        f"Word {conflict['word_id']}\n"
                    )
                    f.write(
                        f"      Canonical: {[l['label'] for l in conflict['canonical_labels']]}\n"
                    )
                    f.write(
                        f"      Duplicate: {[l['label'] for l in conflict['duplicate_labels']]}\n"
                    )
                if len(group["merge_plan"]["conflicts_to_resolve"]) > 5:
                    f.write(
                        f"    ... and {len(group['merge_plan']['conflicts_to_resolve']) - 5} more\n"
                    )

    logger.info(f"Summary saved to {summary_path}")

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Compare receipt word labels across duplicate images"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack name (dev or prod)",
    )
    parser.add_argument(
        "--report",
        required=True,
        help="Path to duplicate_analysis_report.json",
    )
    parser.add_argument(
        "--output-dir",
        default="label_comparison",
        help="Output directory for results (default: label_comparison)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        # Get table name from Pulumi
        table_name = get_table_name(args.stack)

        # Analyze labels
        logger.info(f"Starting label comparison for {args.stack.upper()} stack...")
        report = analyze_duplicate_labels(
            table_name=table_name,
            report_path=args.report,
            output_dir=args.output_dir,
        )

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("COMPARISON SUMMARY")
        logger.info("=" * 60)
        logger.info(
            f"Total duplicate groups: {report['summary']['total_duplicate_groups']}"
        )
        logger.info(
            f"Total duplicate images to delete: {report['summary']['total_duplicate_images']}"
        )
        logger.info(
            f"Total unique labels to preserve: {report['summary']['total_labels_to_preserve']}"
        )
        logger.info(
            f"Total conflicts to resolve: {report['summary']['total_conflicts']}"
        )
        logger.info(f"Output directory: {Path(args.output_dir).absolute()}")

        logger.info("\n✅ Label comparison completed successfully")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


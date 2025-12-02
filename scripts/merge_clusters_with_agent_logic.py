#!/usr/bin/env python3
"""
Merge re-clustered results using combine agent evaluation logic.

This script:
1. Loads re-clustered results from recluster_and_visualize.py
2. Uses combine agent's evaluation logic to determine which clusters should be merged
3. Creates a final optimized grouping
4. Visualizes the final result

Usage:
    python scripts/merge_clusters_with_agent_logic.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --input-dir ./local_receipt_data
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from collections import defaultdict

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  PIL/Pillow not available - visualization will be limited")


def calculate_line_centroid(line_data: Dict) -> Tuple[float, float]:
    """Calculate centroid of a line from its corners."""
    x = (line_data['top_left']['x'] + line_data['top_right']['x'] +
         line_data['bottom_left']['x'] + line_data['bottom_right']['x']) / 4
    y = (line_data['top_left']['y'] + line_data['top_right']['y'] +
         line_data['bottom_left']['y'] + line_data['bottom_right']['y']) / 4
    return x, y


def evaluate_receipt_completeness(lines: List[Dict]) -> Dict[str, any]:
    """
    Evaluate if a group of lines forms a complete receipt.

    Uses the same logic as the combine agent's try_merge and evaluate_grouping tools.
    """
    if not lines:
        return {
            'has_merchant': False,
            'has_address': False,
            'has_phone': False,
            'has_total': False,
            'completeness_score': 0.0,
            'spatial_score': 0.0,
            'line_count_score': 0.0,
            'overall_score': 0.0,
        }

    # Build text
    receipt_text = " ".join(line['text'] for line in lines)
    text_lower = receipt_text.lower()

    # Check for receipt elements (same logic as combine agent)
    has_merchant = any(
        word in text_lower for word in ["market", "store", "restaurant", "cafe", "shop", "inc", "llc", "corp"]
    ) or len([w for w in lines[0]["text"].split() if len(w) > 3]) > 0

    has_address = any(
        word in text_lower for word in ["street", "st", "avenue", "ave", "road", "rd", "blvd", "boulevard", "drive", "dr", "way", "lane", "ln"]
    ) or any(char.isdigit() for char in receipt_text)

    has_phone = any(
        char in receipt_text for char in ["(", ")", "-"]
    ) or any(len(part) == 10 and part.isdigit() for part in receipt_text.replace("(", "").replace(")", "").replace("-", "").split())

    has_total = any(
        word in text_lower for word in ["total", "amount", "sum", "$", "subtotal"]
    ) or any(char == "$" for char in receipt_text)

    completeness_score = sum([has_merchant, has_address, has_phone, has_total]) / 4.0

    # Spatial analysis (same as evaluate_grouping)
    y_positions = [calculate_line_centroid(line)[1] for line in lines]
    y_range = max(y_positions) - min(y_positions) if y_positions else 0
    spatial_score = 1.0 / (1.0 + y_range * 0.1)  # Closer lines = higher score

    # Line count score
    line_count_score = min(len(lines) / 10.0, 1.0) if len(lines) >= 3 else len(lines) / 3.0

    # Overall score (same weights as evaluate_grouping)
    overall_score = (spatial_score * 0.3 + completeness_score * 0.5 + line_count_score * 0.2)

    return {
        'has_merchant': has_merchant,
        'has_address': has_address,
        'has_phone': has_phone,
        'has_total': has_total,
        'completeness_score': completeness_score,
        'spatial_score': spatial_score,
        'line_count_score': line_count_score,
        'overall_score': overall_score,
        'y_range': y_range,
        'line_count': len(lines),
    }


def try_merge_clusters(
    cluster1_lines: List[Dict],
    cluster2_lines: List[Dict],
    cluster1_id: int,
    cluster2_id: int,
) -> Dict[str, any]:
    """
    Try merging two clusters and evaluate if it makes sense.

    Uses the same logic as the combine agent's try_merge tool.
    """
    merged_lines = cluster1_lines + cluster2_lines

    # Evaluate merged receipt
    merged_eval = evaluate_receipt_completeness(merged_lines)

    # Check for large spatial gaps (bad sign for merge)
    all_y_positions = [calculate_line_centroid(line)[1] for line in merged_lines]
    all_y_positions.sort()

    large_gaps = []
    for i in range(len(all_y_positions) - 1):
        gap = all_y_positions[i + 1] - all_y_positions[i]
        if gap > 0.1:  # Large gap might indicate separate receipts
            large_gaps.append({
                "gap_size": round(gap, 3),
                "between_y": f"{all_y_positions[i]:.3f} and {all_y_positions[i+1]:.3f}",
            })

    # Check for duplicate merchants/addresses/phones
    # Look for merchant names in the text (not just first line)
    text1 = " ".join(line['text'] for line in cluster1_lines).lower()
    text2 = " ".join(line['text'] for line in cluster2_lines).lower()

    # Extract potential merchant names (look for common merchant indicators)
    # Merchant names are usually in early lines and contain business words
    merchant_indicators = ["market", "store", "restaurant", "cafe", "shop", "inc", "llc", "corp",
                          "homedepot", "vons", "target", "walmart", "costco", "safeway"]

    merchant_words_1 = [word for word in text1.split() if any(ind in word for ind in merchant_indicators)]
    merchant_words_2 = [word for word in text2.split() if any(ind in word for ind in merchant_indicators)]

    # Also check for business-like words (long words, capitalized patterns)
    # Look at first 5 lines for potential merchant names
    early_lines_1 = " ".join([line['text'] for line in cluster1_lines[:5]]).lower()
    early_lines_2 = " ".join([line['text'] for line in cluster2_lines[:5]]).lower()

    # Check if they share merchant indicators
    shared_merchants = set(merchant_words_1) & set(merchant_words_2)
    has_different_merchants = (
        len(merchant_words_1) > 0 and len(merchant_words_2) > 0 and
        len(shared_merchants) == 0 and
        # Only flag as different if both have clear merchant indicators
        len(merchant_words_1) >= 2 and len(merchant_words_2) >= 2
    )

    # Check X-coordinate proximity (closer = more likely same receipt)
    x_coords_1 = [calculate_line_centroid(line)[0] for line in cluster1_lines]
    x_coords_2 = [calculate_line_centroid(line)[0] for line in cluster2_lines]
    mean_x_1 = sum(x_coords_1) / len(x_coords_1) if x_coords_1 else 0
    mean_x_2 = sum(x_coords_2) / len(x_coords_2) if x_coords_2 else 0
    x_proximity = abs(mean_x_1 - mean_x_2)

    # If clusters are far apart horizontally, they're likely different receipts
    # This is a key signal for side-by-side receipts
    x_proximity_bonus = 0.0
    x_proximity_penalty = False

    if x_proximity < 0.2:  # Within 20% of image width - same receipt likely
        x_proximity_bonus = 0.1  # Small bonus for proximity
    elif x_proximity > 0.4:  # Far apart (>40% of image width) - likely different receipts
        x_proximity_penalty = True  # Prevent merge if too far apart

    # Calculate coherence (same as try_merge)
    coherence = merged_eval['overall_score']

    # Add X-proximity bonus
    coherence += x_proximity_bonus
    coherence = min(coherence, 1.0)  # Cap at 1.0

    # Penalize for issues
    issues = []
    if has_different_merchants:
        issues.append(f"Different merchants detected")
        coherence *= 0.5  # Penalize heavily
    if large_gaps:
        issues.append(f"Large spatial gaps detected ({len(large_gaps)} gaps)")
        coherence *= 0.8
    # Adjust Y-range penalty - receipts can be tall, so be more lenient
    if merged_eval['y_range'] > 0.8:  # Only penalize for very large spreads
        issues.append(f"Very large vertical spread ({merged_eval['y_range']:.3f})")
        coherence *= 0.95  # Less penalty
    if merged_eval['line_count'] < 3:
        issues.append(f"Very few lines ({merged_eval['line_count']})")

    # More lenient merge criteria:
    # - Allow merges if coherence is good even with some issues
    # - Consider X-proximity as positive signal
    # - Prevent merging if clusters are far apart horizontally (side-by-side receipts)
    makes_sense = (
        coherence > 0.5 and  # Lower threshold
        not has_different_merchants and
        not x_proximity_penalty and  # Don't merge if too far apart horizontally
        merged_eval['line_count'] >= 3 and
        (len(large_gaps) == 0 or x_proximity < 0.15)  # Allow small gaps if close horizontally
    )

    return {
        'cluster1_id': cluster1_id,
        'cluster2_id': cluster2_id,
        'merged_line_count': len(merged_lines),
        'coherence_score': round(coherence, 3),
        'makes_sense': makes_sense,
        'has_different_merchants': has_different_merchants,
        'spatial_analysis': {
            'y_range': round(merged_eval['y_range'], 3),
            'large_gaps': large_gaps,
            'gap_count': len(large_gaps),
        },
        'completeness': merged_eval,
        'issues': issues,
        'recommendation': "Merge makes sense" if makes_sense else "Merge does NOT make sense",
    }


def merge_clusters_greedy(
    clusters: Dict[int, List[Dict]],
    min_score: float = 0.6,
) -> Dict[int, List[Dict]]:
    """
    Greedily merge clusters using combine agent evaluation logic.

    Strategy:
    1. Evaluate all pairs of clusters
    2. Merge the best pair that makes sense
    3. Repeat until no more good merges found
    """
    # Start with original clusters
    merged_clusters = {cid: lines[:] for cid, lines in clusters.items()}
    next_cluster_id = max(merged_clusters.keys()) + 1 if merged_clusters else 1

    iteration = 0
    max_iterations = len(clusters)  # Prevent infinite loops

    while iteration < max_iterations:
        iteration += 1

        # Evaluate all pairs
        cluster_ids = sorted(merged_clusters.keys())
        best_merge = None
        best_score = min_score

        for i, cid1 in enumerate(cluster_ids):
            for cid2 in cluster_ids[i+1:]:
                merge_result = try_merge_clusters(
                    merged_clusters[cid1],
                    merged_clusters[cid2],
                    cid1,
                    cid2,
                )

                if merge_result['makes_sense'] and merge_result['coherence_score'] > best_score:
                    best_score = merge_result['coherence_score']
                    best_merge = (cid1, cid2, merge_result)

        if best_merge is None:
            # No more good merges
            break

        # Perform the merge
        cid1, cid2, merge_result = best_merge
        merged_lines = merged_clusters[cid1] + merged_clusters[cid2]

        # Remove old clusters
        del merged_clusters[cid1]
        del merged_clusters[cid2]

        # Add merged cluster
        merged_clusters[next_cluster_id] = merged_lines
        next_cluster_id += 1

        print(f"  Iteration {iteration}: Merged clusters {cid1} + {cid2} "
              f"(score: {merge_result['coherence_score']:.3f}, "
              f"lines: {len(merged_lines)})")

    return merged_clusters


def create_final_visualization(
    original_lines: List[Dict],
    initial_clusters: Dict[int, List[Dict]],
    final_clusters: Dict[int, List[Dict]],
    output_path: Path,
    image_width: int = 2000,
    image_height: int = 2800,
) -> None:
    """Create visualization showing initial re-clustering and final merged result."""
    if not PIL_AVAILABLE:
        print("⚠️  Cannot create visualization - PIL not available")
        return

    # Create image
    img = Image.new('RGB', (image_width, image_height), color='white')
    draw = ImageDraw.Draw(img)

    # Color palette
    colors = [
        (255, 0, 0),      # Red
        (0, 255, 0),      # Green
        (0, 0, 255),      # Blue
        (255, 165, 0),    # Orange
        (128, 0, 128),    # Purple
        (255, 192, 203),  # Pink
        (0, 255, 255),    # Cyan
        (255, 255, 0),    # Yellow
    ]

    # Draw original lines in light gray (background)
    for line in original_lines:
        tl = line['top_left']
        tr = line['top_right']
        br = line['bottom_right']
        bl = line['bottom_left']

        points = [
            (int(tl['x'] * image_width), int((1 - tl['y']) * image_height)),
            (int(tr['x'] * image_width), int((1 - tr['y']) * image_height)),
            (int(br['x'] * image_width), int((1 - br['y']) * image_height)),
            (int(bl['x'] * image_width), int((1 - bl['y']) * image_height)),
        ]
        draw.polygon(points, outline=(220, 220, 220), width=1)

    # Draw final clusters (thick, colored)
    for cluster_id, cluster_lines in final_clusters.items():
        color = colors[(cluster_id - 1) % len(colors)]

        for line in cluster_lines:
            tl = line['top_left']
            tr = line['top_right']
            br = line['bottom_right']
            bl = line['bottom_left']

            points = [
                (int(tl['x'] * image_width), int((1 - tl['y']) * image_height)),
                (int(tr['x'] * image_width), int((1 - tr['y']) * image_height)),
                (int(br['x'] * image_width), int((1 - br['y']) * image_height)),
                (int(bl['x'] * image_width), int((1 - bl['y']) * image_height)),
            ]

            draw.polygon(points, outline=color, width=4)

    # Add legend
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
        small_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
    except:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    legend_y = 20
    draw.text((20, legend_y), "Final Merged Clusters (colored)", fill=(0, 0, 0), font=font)
    legend_y += 25

    for cluster_id in sorted(final_clusters.keys()):
        color = colors[(cluster_id - 1) % len(colors)]
        lines = final_clusters[cluster_id]
        eval_result = evaluate_receipt_completeness(lines)

        draw.rectangle([20, legend_y, 40, legend_y + 15], outline=color, width=4)
        draw.text((45, legend_y),
                 f"Cluster {cluster_id}: {len(lines)} lines, score: {eval_result['overall_score']:.2f}",
                 fill=(0, 0, 0), font=small_font)
        legend_y += 20

    # Save image
    img.save(output_path)
    print(f"💾 Saved final visualization to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Merge re-clustered results using combine agent evaluation logic"
    )
    parser.add_argument(
        "--image-id",
        required=True,
        help="Image ID to process",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("./local_receipt_data"),
        help="Directory containing receipt data",
    )
    parser.add_argument(
        "--output-image",
        type=Path,
        help="Output path for visualization (default: <input-dir>/<image-id>/<image-id>_merged.png)",
    )
    parser.add_argument(
        "--min-merge-score",
        type=float,
        default=0.6,
        help="Minimum coherence score to merge clusters (default: 0.6)",
    )

    args = parser.parse_args()

    # Load cluster assignments from recluster script
    image_dir = args.input_dir / args.image_id
    assignments_file = image_dir / f"{args.image_id}_cluster_assignments.json"

    if not assignments_file.exists():
        print(f"❌ Cluster assignments file not found: {assignments_file}")
        print(f"   Run recluster_and_visualize.py first!")
        sys.exit(1)

    with open(assignments_file) as f:
        cluster_assignments = json.load(f)

    # Load lines
    receipts_file = image_dir / "receipts.json"
    with open(receipts_file) as f:
        receipts_data = json.load(f)

    receipt = receipts_data[0]
    receipt_id = receipt['receipt_id']

    lines_file = image_dir / f"receipt_{receipt_id:05d}" / "lines.json"
    with open(lines_file) as f:
        lines = json.load(f)

    # Create line_id -> line mapping
    line_map = {line['line_id']: line for line in lines}

    # Build initial clusters from assignments
    initial_clusters = defaultdict(list)
    for line_id, cluster_id in cluster_assignments.items():
        line_id_int = int(line_id)
        if line_id_int in line_map:
            initial_clusters[cluster_id].append(line_map[line_id_int])

    print(f"📊 Processing image: {args.image_id}")
    print(f"   Initial clusters: {len(initial_clusters)}")
    for cid in sorted(initial_clusters.keys()):
        print(f"      Cluster {cid}: {len(initial_clusters[cid])} lines")

    # Evaluate initial clusters
    print(f"\n📈 Evaluating initial clusters:")
    initial_scores = {}
    for cluster_id, cluster_lines in initial_clusters.items():
        eval_result = evaluate_receipt_completeness(cluster_lines)
        initial_scores[cluster_id] = eval_result
        print(f"   Cluster {cluster_id}: score={eval_result['overall_score']:.3f}, "
              f"completeness={eval_result['completeness_score']:.3f}, "
              f"spatial={eval_result['spatial_score']:.3f}")

    # Merge clusters
    print(f"\n🔄 Merging clusters (min_score={args.min_merge_score})...")
    final_clusters = merge_clusters_greedy(initial_clusters, min_score=args.min_merge_score)

    print(f"\n✅ Final clusters: {len(final_clusters)}")
    for cid in sorted(final_clusters.keys()):
        eval_result = evaluate_receipt_completeness(final_clusters[cid])
        print(f"   Cluster {cid}: {len(final_clusters[cid])} lines, "
              f"score={eval_result['overall_score']:.3f}")

    # Create visualization
    if PIL_AVAILABLE:
        output_image = args.output_image or (image_dir / f"{args.image_id}_merged.png")
        print(f"\n🎨 Creating visualization...")
        create_final_visualization(
            lines,
            initial_clusters,
            final_clusters,
            output_image,
            image_width=receipt['width'],
            image_height=receipt['height'],
        )

    # Save final grouping
    final_grouping = {
        cluster_id: [line['line_id'] for line in cluster_lines]
        for cluster_id, cluster_lines in final_clusters.items()
    }

    grouping_file = image_dir / f"{args.image_id}_final_grouping.json"
    with open(grouping_file, 'w') as f:
        json.dump(final_grouping, f, indent=2)
    print(f"💾 Saved final grouping to: {grouping_file}")

    # Summary
    print(f"\n" + "=" * 80)
    print(f"📊 SUMMARY")
    print(f"=" * 80)
    print(f"Initial clusters: {len(initial_clusters)}")
    print(f"Final clusters: {len(final_clusters)}")
    print(f"Merges performed: {len(initial_clusters) - len(final_clusters)}")


if __name__ == "__main__":
    main()


#!/usr/bin/env python3
"""
Sweep clustering parameters to find optimal values for receipt-level OCR.

This script tests different parameter combinations and reports the number of clusters
found for each combination.
"""

import argparse
import subprocess
import sys
from pathlib import Path

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))


def sweep_parameters(
    image_id: str,
    receipt_id: int,
    base_output_dir: Path,
    raw_bucket: str,
):
    """Sweep different parameter combinations."""
    results = []

    # Test different combinations
    test_cases = [
        # (x_eps, merge_x_proximity_threshold, vertical_gap_threshold, merge_min_score, description)
        (0.08, 0.4, 0.15, 0.5, "default_image_level"),
        (0.12, 0.4, 0.15, 0.5, "eps_0.12"),
        (0.15, 0.4, 0.15, 0.5, "eps_0.15"),
        (0.18, 0.4, 0.15, 0.5, "eps_0.18"),
        (0.15, 0.5, 0.15, 0.5, "eps_0.15_merge_0.5"),
        (0.15, 0.6, 0.15, 0.5, "eps_0.15_merge_0.6"),
        (0.15, 0.7, 0.15, 0.5, "eps_0.15_merge_0.7"),
        (0.15, 0.6, 0.20, 0.5, "eps_0.15_merge_0.6_gap_0.20"),
        (0.15, 0.6, 0.25, 0.5, "eps_0.15_merge_0.6_gap_0.25"),
        (0.15, 0.6, 0.30, 0.5, "eps_0.15_merge_0.6_gap_0.30"),
        (0.15, 0.6, 0.25, 0.3, "eps_0.15_merge_0.6_gap_0.25_score_0.3"),
        (0.15, 0.6, 0.25, 0.4, "eps_0.15_merge_0.6_gap_0.25_score_0.4"),
        (0.18, 0.6, 0.25, 0.4, "eps_0.18_merge_0.6_gap_0.25_score_0.4"),
    ]

    print(f"🧪 Testing {len(test_cases)} parameter combinations...\n")

    for x_eps, merge_x_prox, vert_gap, min_score, description in test_cases:
        output_dir = base_output_dir / description
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"Testing: {description}")
        print(f"  x_eps={x_eps}, merge_x_proximity={merge_x_prox}, vertical_gap={vert_gap}, min_score={min_score}")

        # Run visualization script
        cmd = [
            sys.executable,
            "scripts/visualize_final_clusters_cropped.py",
            "--image-id",
            image_id,
            "--receipt-id",
            str(receipt_id),
            "--x-eps",
            str(x_eps),
            "--merge-x-proximity-threshold",
            str(merge_x_prox),
            "--vertical-gap-threshold",
            str(vert_gap),
            "--merge-min-score",
            str(min_score),
            "--output-dir",
            str(output_dir),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )

            # Parse output to find number of clusters
            num_clusters = None
            cluster_sizes = []
            for line in result.stdout.split("\n"):
                if "Total clusters:" in line:
                    num_clusters = int(line.split(":")[1].strip())
                elif "Cluster" in line and "lines" in line:
                    # Extract cluster size
                    parts = line.split(":")
                    if len(parts) >= 2:
                        size_part = parts[1].split("lines")[0].strip()
                        try:
                            cluster_sizes.append(int(size_part))
                        except ValueError:
                            pass

            if num_clusters is not None:
                results.append(
                    {
                        "description": description,
                        "x_eps": x_eps,
                        "merge_x_proximity_threshold": merge_x_prox,
                        "vertical_gap_threshold": vert_gap,
                        "merge_min_score": min_score,
                        "num_clusters": num_clusters,
                        "cluster_sizes": cluster_sizes,
                    }
                )
                print(f"  ✅ Found {num_clusters} clusters: {cluster_sizes}")
            else:
                print(f"  ⚠️  Could not parse cluster count")
                if result.stderr:
                    print(f"  Error: {result.stderr[:200]}")

        except subprocess.TimeoutExpired:
            print(f"  ⚠️  Timeout")
        except Exception as e:
            print(f"  ⚠️  Error: {e}")

        print()

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"{'Description':<40} {'Clusters':<10} {'Sizes':<30}")
    print("-" * 80)

    # Sort by number of clusters (prefer fewer clusters closer to 2)
    results.sort(key=lambda x: (abs(x["num_clusters"] - 2), x["num_clusters"]))

    for r in results:
        sizes_str = ", ".join(str(s) for s in r["cluster_sizes"][:5])
        if len(r["cluster_sizes"]) > 5:
            sizes_str += f" (+{len(r['cluster_sizes']) - 5} more)"
        print(
            f"{r['description']:<40} {r['num_clusters']:<10} {sizes_str:<30}"
        )

    # Find best result (closest to 2 clusters)
    if results:
        best = results[0]
        print("\n" + "=" * 80)
        print("BEST RESULT (closest to 2 clusters):")
        print("=" * 80)
        print(f"Description: {best['description']}")
        print(f"Parameters:")
        print(f"  --x-eps {best['x_eps']}")
        print(f"  --merge-x-proximity-threshold {best['merge_x_proximity_threshold']}")
        print(f"  --vertical-gap-threshold {best['vertical_gap_threshold']}")
        print(f"  --merge-min-score {best['merge_min_score']}")
        print(f"Result: {best['num_clusters']} clusters")
        print(f"Sizes: {best['cluster_sizes']}")


def main():
    parser = argparse.ArgumentParser(
        description="Sweep clustering parameters to find optimal values"
    )
    parser.add_argument(
        "--image-id", required=True, help="Image ID to test"
    )
    parser.add_argument(
        "--receipt-id", type=int, required=True, help="Receipt ID to use"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./clustering_parameter_sweep"),
        help="Base output directory for all test results",
    )

    args = parser.parse_args()

    # Get raw bucket from environment
    import os
    from scripts.split_receipt import setup_environment

    env = setup_environment()
    raw_bucket = env.get("raw_bucket", "")
    if not raw_bucket:
        print("⚠️  RAW_BUCKET not set")
        sys.exit(1)

    sweep_parameters(
        args.image_id,
        args.receipt_id,
        args.output_dir,
        raw_bucket,
    )


if __name__ == "__main__":
    main()


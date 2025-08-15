#!/usr/bin/env python3
"""
Laptop script for testing noise detection on real receipt data.

This script:
1. Exports receipt data from DynamoDB using existing tools
2. Analyzes noise patterns across different receipt types
3. Generates a report showing potential cost savings
4. Validates edge cases against real data

Usage:
    python scripts/test_noise_detection.py --table-name <table> --output-dir ./noise-analysis
    python scripts/test_noise_detection.py --local-data ./exported-receipts --output-dir ./noise-analysis
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_label.utils.noise_detection import is_noise_text

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.export_image import export_image


class NoiseDetectionAnalyzer:
    """Analyze noise detection patterns on real receipt data."""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize counters
        self.stats = {
            "total_receipts": 0,
            "total_words": 0,
            "noise_words": 0,
            "meaningful_words": 0,
            "by_merchant": defaultdict(
                lambda: {
                    "receipts": 0,
                    "total_words": 0,
                    "noise_words": 0,
                    "examples": [],
                }
            ),
            "noise_patterns": defaultdict(int),
            "edge_cases": [],
        }

    def analyze_receipt_data(self, data: dict, image_id: str) -> dict:
        """Analyze noise patterns in a single receipt export."""
        receipt_words = data.get("receipt_words", [])
        receipt_metadata = data.get("receipt_metadatas", [])

        if not receipt_words:
            return {"skipped": True, "reason": "No receipt words found"}

        # Get merchant type from metadata
        merchant_type = "unknown"
        if receipt_metadata:
            merchant_type = receipt_metadata[0].get("merchant_type", "unknown")

        # Analyze each word
        word_analysis = []
        for word_data in receipt_words:
            text = word_data.get("text", "")
            is_noise = is_noise_text(text)

            analysis = {
                "text": text,
                "is_noise": is_noise,
                "line_id": word_data.get("line_id"),
                "word_id": word_data.get("word_id"),
                "confidence": word_data.get("confidence", 0),
            }
            word_analysis.append(analysis)

            # Update statistics
            self.stats["total_words"] += 1
            if is_noise:
                self.stats["noise_words"] += 1
                self.stats["noise_patterns"][text] += 1
            else:
                self.stats["meaningful_words"] += 1

            # Track merchant-specific stats
            merchant_stats = self.stats["by_merchant"][merchant_type]
            merchant_stats["total_words"] += 1
            if is_noise:
                merchant_stats["noise_words"] += 1

            # Collect interesting examples
            if is_noise and text not in [".", ",", "-", "|"]:
                if len(merchant_stats["examples"]) < 10:
                    merchant_stats["examples"].append(text)

        # Update receipt count
        self.stats["total_receipts"] += 1
        self.stats["by_merchant"][merchant_type]["receipts"] += 1

        return {
            "image_id": image_id,
            "merchant_type": merchant_type,
            "total_words": len(receipt_words),
            "noise_count": sum(1 for w in word_analysis if w["is_noise"]),
            "noise_percentage": sum(1 for w in word_analysis if w["is_noise"])
            / len(receipt_words)
            * 100,
            "word_analysis": word_analysis,
        }

    def export_from_dynamo(
        self, table_name: str, limit: int = 10
    ) -> List[str]:
        """Export sample receipts from DynamoDB."""
        print(f"Exporting receipts from DynamoDB table: {table_name}")

        dynamo_client = DynamoClient(table_name)
        export_dir = self.output_dir / "exports"
        export_dir.mkdir(exist_ok=True)

        # Get recent images
        # Note: This is a simplified query - adjust based on your data model
        images = dynamo_client.query(
            pk_value="IMAGE", sk_prefix="IMAGE#", limit=limit
        )

        exported_files = []
        for i, item in enumerate(images):
            image_id = item.get("image_id")
            if image_id:
                print(f"Exporting {i+1}/{limit}: {image_id}")
                try:
                    export_image(table_name, image_id, str(export_dir))
                    exported_files.append(str(export_dir / f"{image_id}.json"))
                except Exception as e:
                    print(f"  Error exporting {image_id}: {e}")

        return exported_files

    def analyze_files(self, file_paths: List[str]) -> None:
        """Analyze multiple exported receipt files."""
        print(f"\nAnalyzing {len(file_paths)} receipt files...")

        detailed_results = []

        for i, file_path in enumerate(file_paths):
            print(
                f"Processing {i+1}/{len(file_paths)}: {Path(file_path).name}"
            )

            with open(file_path, "r") as f:
                data = json.load(f)

            image_id = Path(file_path).stem
            result = self.analyze_receipt_data(data, image_id)

            if not result.get("skipped"):
                detailed_results.append(result)
                print(
                    f"  - Words: {result['total_words']}, "
                    f"Noise: {result['noise_count']} ({result['noise_percentage']:.1f}%)"
                )

        # Save detailed results
        with open(self.output_dir / "detailed_analysis.json", "w") as f:
            json.dump(detailed_results, f, indent=2)

    def generate_report(self) -> None:
        """Generate a comprehensive analysis report."""
        report = []

        # Header
        report.append("# Noise Detection Analysis Report")
        report.append(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )

        # Overall Statistics
        report.append("## Overall Statistics")
        report.append(
            f"- Total Receipts Analyzed: {self.stats['total_receipts']}"
        )
        report.append(f"- Total Words: {self.stats['total_words']:,}")
        report.append(f"- Noise Words: {self.stats['noise_words']:,}")
        report.append(
            f"- Meaningful Words: {self.stats['meaningful_words']:,}"
        )

        if self.stats["total_words"] > 0:
            noise_percentage = (
                self.stats["noise_words"] / self.stats["total_words"]
            ) * 100
            report.append(
                f"- Overall Noise Percentage: {noise_percentage:.2f}%\n"
            )

        # Cost Savings Estimate
        report.append("## Estimated Cost Savings")
        tokens_per_word = 1.5  # Average tokens per word
        cost_per_token = 0.00002  # $0.00002 per token (OpenAI embedding)

        tokens_saved = self.stats["noise_words"] * tokens_per_word
        cost_saved = tokens_saved * cost_per_token

        report.append(f"- Tokens Saved: {tokens_saved:,.0f}")
        report.append(f"- Cost Saved (this sample): ${cost_saved:.4f}")

        # Extrapolate to monthly
        if self.stats["total_receipts"] > 0:
            avg_words_per_receipt = (
                self.stats["total_words"] / self.stats["total_receipts"]
            )
            avg_noise_percentage = (
                self.stats["noise_words"] / self.stats["total_words"]
            ) * 100

            # Assume 1000 receipts per day
            daily_receipts = 1000
            monthly_receipts = daily_receipts * 30
            monthly_words = monthly_receipts * avg_words_per_receipt
            monthly_noise_words = monthly_words * (avg_noise_percentage / 100)
            monthly_tokens_saved = monthly_noise_words * tokens_per_word
            monthly_cost_saved = monthly_tokens_saved * cost_per_token

            report.append(
                f"\n### Monthly Projection (at {daily_receipts} receipts/day):"
            )
            report.append(f"- Monthly Receipts: {monthly_receipts:,}")
            report.append(
                f"- Noise Words Filtered: {monthly_noise_words:,.0f}"
            )
            report.append(f"- Monthly Savings: ${monthly_cost_saved:.2f}")
            report.append(
                f"- Annual Savings: ${monthly_cost_saved * 12:.2f}\n"
            )

        # By Merchant Type
        report.append("## Analysis by Merchant Type")
        for merchant_type, stats in sorted(self.stats["by_merchant"].items()):
            if stats["receipts"] > 0:
                merchant_noise_pct = (
                    (stats["noise_words"] / stats["total_words"]) * 100
                    if stats["total_words"] > 0
                    else 0
                )
                report.append(f"\n### {merchant_type.title()}")
                report.append(f"- Receipts: {stats['receipts']}")
                report.append(f"- Total Words: {stats['total_words']:,}")
                report.append(f"- Noise Percentage: {merchant_noise_pct:.2f}%")
                if stats["examples"]:
                    report.append(
                        f"- Example Noise: {', '.join(repr(e) for e in stats['examples'][:5])}"
                    )

        # Most Common Noise Patterns
        report.append("\n## Most Common Noise Patterns")
        top_patterns = sorted(
            self.stats["noise_patterns"].items(),
            key=lambda x: x[1],
            reverse=True,
        )[:20]
        for pattern, count in top_patterns:
            report.append(f"- `{repr(pattern)}`: {count} occurrences")

        # Edge Cases Found
        if self.stats["edge_cases"]:
            report.append("\n## Interesting Edge Cases")
            for case in self.stats["edge_cases"][:10]:
                report.append(f"- {case}")

        # Save report
        report_text = "\n".join(report)
        with open(self.output_dir / "noise_detection_report.md", "w") as f:
            f.write(report_text)

        print(
            f"\nReport saved to: {self.output_dir / 'noise_detection_report.md'}"
        )

        # Also save raw statistics
        with open(self.output_dir / "statistics.json", "w") as f:
            json.dump(self.stats, f, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(
        description="Test noise detection on real receipt data"
    )
    parser.add_argument(
        "--table-name", help="DynamoDB table name to export from"
    )
    parser.add_argument(
        "--local-data", help="Directory with already exported JSON files"
    )
    parser.add_argument(
        "--output-dir",
        default="./noise-analysis",
        help="Output directory for analysis",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of receipts to export (if using --table-name)",
    )

    args = parser.parse_args()

    if not args.table_name and not args.local_data:
        parser.error("Either --table-name or --local-data must be specified")

    analyzer = NoiseDetectionAnalyzer(args.output_dir)

    # Get receipt data
    if args.table_name:
        # Export from DynamoDB
        file_paths = analyzer.export_from_dynamo(args.table_name, args.limit)
    else:
        # Use local files
        local_dir = Path(args.local_data)
        file_paths = list(local_dir.glob("*.json"))
        file_paths = [str(p) for p in file_paths]

    if not file_paths:
        print("No receipt files found to analyze")
        return

    # Analyze the data
    analyzer.analyze_files(file_paths)

    # Generate report
    analyzer.generate_report()

    print("\nAnalysis complete!")


if __name__ == "__main__":
    main()

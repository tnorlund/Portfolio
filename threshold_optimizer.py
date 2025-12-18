#!/usr/bin/env python3
"""
Threshold optimizer for geometric anomaly detection.

Uses ROC analysis data to find optimal σ thresholds for each label pair.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass


@dataclass
class ThresholdEvaluation:
    """Results for a specific threshold value."""
    threshold_std: float
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int

    @property
    def precision(self) -> float:
        """True positives / (true positives + false positives)"""
        total = self.true_positives + self.false_positives
        return self.true_positives / total if total > 0 else 0.0

    @property
    def recall(self) -> float:
        """True positives / (true positives + false negatives)"""
        total = self.true_positives + self.false_negatives
        return self.true_positives / total if total > 0 else 0.0

    @property
    def f1_score(self) -> float:
        """Harmonic mean of precision and recall"""
        if self.precision + self.recall == 0:
            return 0.0
        return 2 * (self.precision * self.recall) / (self.precision + self.recall)

    @property
    def accuracy(self) -> float:
        """Correctly classified / total"""
        total = self.true_positives + self.false_positives + self.false_negatives + self.true_negatives
        correct = self.true_positives + self.true_negatives
        return correct / total if total > 0 else 0.0


def find_optimal_threshold(
    roc_data: List[dict],
    issue_type: str = "geometric_anomaly",
) -> Dict[str, any]:
    """
    Find optimal threshold using ROC data.

    Args:
        roc_data: List of dicts with {word_text, label, issue_type, llm_decision, ...}
        issue_type: Filter to specific issue type (default: geometric_anomaly)

    Returns:
        Dict with optimal threshold and detailed metrics
    """

    # Filter to specific issue type
    filtered_data = [
        item for item in roc_data
        if item.get("issue_type") == issue_type
    ]

    if not filtered_data:
        print(f"No data for issue_type: {issue_type}")
        return None

    print(f"\nAnalyzing {len(filtered_data)} anomalies of type: {issue_type}")

    # For now, we don't have deviation scores in the ROC data
    # (we'd need to add those to the output)
    # So we'll use a simpler approach: calculate invalid rate at each threshold

    # Count valid vs invalid
    valid_count = sum(1 for item in filtered_data if item.get("is_valid"))
    invalid_count = sum(1 for item in filtered_data if item.get("is_invalid"))

    print(f"  Valid: {valid_count} ({valid_count/len(filtered_data)*100:.1f}%)")
    print(f"  Invalid: {invalid_count} ({invalid_count/len(filtered_data)*100:.1f}%)")

    # Without actual deviation scores, we can only say:
    # - If threshold is 0σ: catches all (100% sensitivity, 0% specificity)
    # - If threshold is ∞σ: catches none (0% sensitivity, 100% specificity)

    # Recommendation: adjust threshold based on invalid rate
    invalid_rate = invalid_count / len(filtered_data) if filtered_data else 0

    if invalid_rate >= 0.8:
        # Most flagged items are invalid → threshold is good or too lenient
        recommended_threshold = 2.0
        reason = "High invalid rate (80%+) - threshold is catching real errors"
    elif invalid_rate >= 0.6:
        # Decent precision → keep current or increase slightly
        recommended_threshold = 2.0
        reason = "Moderate invalid rate (60-80%) - current threshold is reasonable"
    elif invalid_rate >= 0.5:
        # Mixed results → might want to increase threshold
        recommended_threshold = 2.5
        reason = "50% invalid rate - consider increasing threshold to reduce false positives"
    else:
        # Mostly false positives → increase threshold significantly
        recommended_threshold = 3.0
        reason = f"Low invalid rate ({invalid_rate*100:.0f}%) - increase threshold to reduce false positives"

    return {
        "issue_type": issue_type,
        "total_anomalies": len(filtered_data),
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "invalid_rate": invalid_rate,
        "recommended_threshold": recommended_threshold,
        "reason": reason,
    }


def analyze_roc_data(roc_file: Path) -> None:
    """Load and analyze ROC data."""

    if not roc_file.exists():
        print(f"ROC file not found: {roc_file}")
        return

    with open(roc_file, "r") as f:
        roc_data = json.load(f)

    print(f"\nLoaded {len(roc_data)} anomaly records from {roc_file.name}")

    # Analyze by issue type
    issue_types = set(item.get("issue_type") for item in roc_data)
    print(f"Issue types: {issue_types}\n")

    results = {}
    for issue_type in issue_types:
        result = find_optimal_threshold(roc_data, issue_type)
        if result:
            results[issue_type] = result

    # Summary
    print("\n" + "="*70)
    print("THRESHOLD RECOMMENDATIONS")
    print("="*70)

    for issue_type, result in results.items():
        print(f"\n{issue_type}:")
        print(f"  Recommended threshold: {result['recommended_threshold']:.1f}σ")
        print(f"  Reason: {result['reason']}")

    print("\n" + "="*70)


if __name__ == "__main__":
    # Find latest ROC file
    roc_dir = Path(__file__).parent / "logs" / "roc_analysis"

    if roc_dir.exists():
        roc_files = sorted(roc_dir.glob("roc_data_*.json"))
        if roc_files:
            latest_roc = roc_files[-1]
            analyze_roc_data(latest_roc)
        else:
            print("No ROC data files found")
    else:
        print(f"ROC analysis directory not found: {roc_dir}")

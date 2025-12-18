#!/usr/bin/env python3
"""
Detailed threshold analysis and recommendations for geometric anomaly detection.

Uses empirical ROC data to recommend optimal thresholds.
"""

import json
from pathlib import Path

def analyze_thresholds():
    """Analyze current thresholds based on ROC data."""

    roc_file = Path(__file__).parent / "logs" / "roc_analysis" / "roc_data_from_log.json"

    if not roc_file.exists():
        print(f"ROC file not found: {roc_file}")
        return

    with open(roc_file, "r") as f:
        roc_data = json.load(f)

    print(f"\n{'='*80}")
    print("THRESHOLD ANALYSIS: Geometric Anomaly Detection")
    print(f"{'='*80}")

    print(f"\nData Summary:")
    print(f"  Total flagged anomalies: {len(roc_data)}")

    valid = [item for item in roc_data if item["is_valid"]]
    invalid = [item for item in roc_data if item["is_invalid"]]

    print(f"  Valid (correct labels): {len(valid)}")
    print(f"  Invalid (mislabeled): {len(invalid)}")
    print(f"  Invalid rate: {len(invalid)/len(roc_data)*100:.1f}%")
    print(f"  False positive rate: {len(valid)/len(roc_data)*100:.1f}%")

    print(f"\n{'='*80}")
    print("CURRENT THRESHOLD: 2.0σ")
    print(f"{'='*80}")

    print(f"\nAt 2.0σ threshold:")
    print(f"  ✓ Sensitivity (catches {len(invalid)} real errors)")
    print(f"  ✗ Precision (67% - has {len(valid)} false positives)")
    print(f"  ✗ User impact: 1 in 3 flagged items is a false positive")

    print(f"\n{'='*80}")
    print("THRESHOLD OPTIMIZATION ANALYSIS")
    print(f"{'='*80}")

    print(f"\nAssuming threshold increase reduces detection linearly:")
    print(f"\n→ Increase to 2.5σ:")
    print(f"   Estimated: Would reduce detections by ~25%")
    print(f"   Expected: ~7-8 real errors caught (vs 10)")
    print(f"   Expected: ~3-4 false positives (vs 5)")
    print(f"   Benefit: Lower false positive rate")
    print(f"   Cost: Miss ~2-3 real errors")

    print(f"\n→ Increase to 3.0σ:")
    print(f"   Estimated: Would reduce detections by ~50%")
    print(f"   Expected: ~5 real errors caught (vs 10)")
    print(f"   Expected: ~2-3 false positives (vs 5)")
    print(f"   Benefit: Much lower false positive rate")
    print(f"   Cost: Miss ~5 real errors")

    print(f"\n{'='*80}")
    print("RECOMMENDATION")
    print(f"{'='*80}")

    print(f"""
Based on the ROC data (15 anomalies, 67% invalid rate):

KEEP CURRENT THRESHOLD: 2.0σ

Reasoning:
  1. A 67% hit rate is actually quite good for anomaly detection
  2. The 5 false positives are still useful for flagging borderline cases
  3. The 10 real errors represent actual mislabelings that need fixing
  4. Missing real errors (by increasing threshold) is worse than false positives

Implementation:
  ✓ Use per-pattern thresholds (adaptive based on tightness)
  ✓ For TIGHT patterns (std < 0.1): Use 1.5σ (stricter)
  ✓ For MODERATE patterns (std 0.1-0.2): Use 2.0σ (current)
  ✓ For LOOSE patterns (std > 0.2): Use 2.5σ (lenient)

This adaptive approach will:
  - Reduce false positives on loose patterns
  - Maintain sensitivity on tight patterns
  - Be merchant-specific and data-driven
""")

    print(f"\n{'='*80}")
    print("NEXT STEPS")
    print(f"{'='*80}")

    print(f"""
1. Implement per-pattern thresholds based on tightness
2. Store learned thresholds per merchant
3. Monitor false positive rate over time
4. Adjust thresholds as more data is collected
5. Test on new merchants with baseline thresholds
""")


if __name__ == "__main__":
    analyze_thresholds()

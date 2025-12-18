#!/usr/bin/env python3
"""
Extract ROC data from existing LLM review log file.

Parses the log to extract geometric anomaly decisions and creates
a JSON file for threshold optimization analysis.
"""

import json
import re
from pathlib import Path

# Log file from the successful LLM review run
LOG_FILE = Path(__file__).parent / "logs" / "label_evaluator" / "evaluation_20251218_083921.log"

def extract_roc_data_from_log():
    """Extract LLM decisions from log file."""

    if not LOG_FILE.exists():
        print(f"Log file not found: {LOG_FILE}")
        return None

    with open(LOG_FILE, "r") as f:
        content = f.read()

    # Extract all "Reviewed" lines which contain LLM decisions
    # Format: "Reviewed 'word': DECISION - reasoning..."
    reviewed_pattern = r"Reviewed '([^']+)': (VALID|INVALID|NEEDS_REVIEW) - (.+?)(?=\n|$)"
    matches = re.findall(reviewed_pattern, content)

    print(f"Found {len(matches)} LLM reviews in log")

    roc_data = []
    for i, (word_text, decision, reasoning) in enumerate(matches):
        roc_data.append({
            "index": i,
            "word_text": word_text,
            "decision": decision,
            "is_valid": decision == "VALID",
            "is_invalid": decision == "INVALID",
            "issue_type": "geometric_anomaly",
            "reasoning_summary": reasoning[:150] + "..." if len(reasoning) > 150 else reasoning,
        })

    # Save to JSON
    output_dir = Path(__file__).parent / "logs" / "roc_analysis"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "roc_data_from_log.json"
    with open(output_file, "w") as f:
        json.dump(roc_data, f, indent=2)

    print(f"✓ Saved {len(roc_data)} anomalies to: {output_file}")

    # Summary
    valid_count = sum(1 for item in roc_data if item["is_valid"])
    invalid_count = sum(1 for item in roc_data if item["is_invalid"])

    print(f"\nSummary:")
    print(f"  VALID: {valid_count} ({valid_count/len(roc_data)*100:.1f}%)")
    print(f"  INVALID: {invalid_count} ({invalid_count/len(roc_data)*100:.1f}%)")

    return roc_data


if __name__ == "__main__":
    extract_roc_data_from_log()

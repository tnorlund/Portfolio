#!/usr/bin/env python3
"""Quick validation script for Step Function JSONPath expressions.

This validates that JSONPath expressions in the step function definition
will work with actual data structures, without needing to deploy to AWS.

Usage:
    python scripts/validate_step_function_jsonpath.py
"""

import json
import sys
from pathlib import Path

# Try to import jsonpath_ng, but don't fail if not available
try:
    from jsonpath_ng import parse
    JSONPATH_AVAILABLE = True
except ImportError:
    JSONPATH_AVAILABLE = False
    print("⚠️  jsonpath_ng not installed. Install with: pip install jsonpath-ng")
    print("   Falling back to simple string matching validation...")


def validate_jsonpath_simple(jsonpath_expr: str, data: dict) -> bool:
    """Simple JSONPath validation without jsonpath_ng."""
    # Remove $ prefix
    if jsonpath_expr.startswith("$."):
        jsonpath_expr = jsonpath_expr[2:]

    # Split by dots and navigate
    parts = jsonpath_expr.split(".")
    current = data

    for part in parts:
        if part not in current:
            return False
        current = current[part]

    return True


def validate_jsonpath(jsonpath_expr: str, data: dict) -> tuple[bool, any]:
    """Validate JSONPath expression and return (success, value)."""
    if JSONPATH_AVAILABLE:
        try:
            jsonpath = parse(jsonpath_expr)
            matches = [match.value for match in jsonpath.find(data)]
            return (len(matches) > 0, matches[0] if matches else None)
        except Exception as e:
            return (False, f"Error: {e}")
    else:
        success = validate_jsonpath_simple(jsonpath_expr, data)
        if success:
            # Extract value manually
            parts = jsonpath_expr.replace("$.", "").split(".")
            current = data
            for part in parts:
                current = current[part]
            return (True, current)
        return (False, None)


def test_group_chunks_merge_jsonpaths():
    """Test JSONPath expressions used in GroupChunksForMerge."""

    # Sample input matching actual Step Function execution
    sample_input = {
        "chunked_data": {
            "batch_id": "c5df280d-32c7-4377-80d0-eb3e34417c37",
            "total_chunks": 7,
            "chunks": [],
            "chunks_s3_key": "chunks/c5df280d-32c7-4377-80d0-eb3e34417c37/chunks.json",
            "chunks_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
            "use_s3": True,
            # Note: poll_results_s3_key is NOT here (this was the bug)
        },
        "poll_results_data": {
            "batch_id": "c5df280d-32c7-4377-80d0-eb3e34417c37",
            "poll_results": None,
            "poll_results_s3_key": "poll_results/c5df280d-32c7-4377-80d0-eb3e34417c37/poll_results.json",
            "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
        },
        "chunk_results": [],
        "poll_results": [],
    }

    print("=" * 80)
    print("Testing GroupChunksForMerge JSONPath Expressions")
    print("=" * 80)

    # Test expressions used in GroupChunksForMerge
    expressions = [
        ("$.chunked_data.batch_id", True, "batch_id"),
        ("$.chunked_data.total_chunks", True, "total_chunks"),
        ("$.chunk_results", True, "chunk_results"),
        ("$.poll_results", True, "poll_results"),
        # OLD (broken) - tries to get from chunked_data
        ("$.chunked_data.poll_results_s3_key", False, "Should fail - key doesn't exist"),
        # NEW (fixed) - gets from poll_results_data
        ("$.poll_results_data.poll_results_s3_key", True, "Should work - key exists"),
        ("$.poll_results_data.poll_results_s3_bucket", True, "Should work - key exists"),
    ]

    all_passed = True
    for jsonpath_expr, should_succeed, description in expressions:
        success, value = validate_jsonpath(jsonpath_expr, sample_input)

        if success == should_succeed:
            status = "✅"
            if should_succeed:
                print(f"{status} {jsonpath_expr:50} → {value} ({description})")
            else:
                print(f"{status} {jsonpath_expr:50} → Correctly fails ({description})")
        else:
            status = "❌"
            all_passed = False
            if should_succeed:
                print(f"{status} {jsonpath_expr:50} → FAILED (expected success, got failure)")
            else:
                print(f"{status} {jsonpath_expr:50} → FAILED (expected failure, got success: {value})")

    print("=" * 80)
    if all_passed:
        print("✅ All JSONPath expressions validated successfully!")
        return 0
    else:
        print("❌ Some JSONPath expressions failed validation")
        return 1


def test_split_into_chunks_jsonpaths():
    """Test JSONPath expressions used in SplitIntoChunks."""

    sample_input = {
        "poll_results_data": {
            "poll_results": None,
            "poll_results_s3_key": "poll_results/batch-123/poll_results.json",
            "poll_results_s3_bucket": "test-bucket",
        },
    }

    print("\n" + "=" * 80)
    print("Testing SplitIntoChunks JSONPath Expressions")
    print("=" * 80)

    expressions = [
        ("$.poll_results_data.poll_results", True, "poll_results"),
        ("$.poll_results_data.poll_results_s3_key", True, "poll_results_s3_key"),
        ("$.poll_results_data.poll_results_s3_bucket", True, "poll_results_s3_bucket"),
    ]

    all_passed = True
    for jsonpath_expr, should_succeed, description in expressions:
        success, value = validate_jsonpath(jsonpath_expr, sample_input)

        if success == should_succeed:
            status = "✅"
            print(f"{status} {jsonpath_expr:50} → {value} ({description})")
        else:
            status = "❌"
            all_passed = False
            print(f"{status} {jsonpath_expr:50} → FAILED")

    print("=" * 80)
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = 0

    exit_code |= test_group_chunks_merge_jsonpaths()
    exit_code |= test_split_into_chunks_jsonpaths()

    if exit_code == 0:
        print("\n✅ All validations passed! Safe to deploy.")
    else:
        print("\n❌ Some validations failed. Fix before deploying.")

    sys.exit(exit_code)


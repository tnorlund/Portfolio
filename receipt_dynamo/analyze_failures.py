#!/usr/bin/env python
"""Analyze test failures and categorize them for systematic fixing."""

import re
import subprocess
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

def run_tests_and_capture_failures():
    """Run tests and capture detailed failure information."""
    cmd = ["python", "-m", "pytest", "tests/integration", "-v", "--tb=short", "-x"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout + result.stderr

def categorize_failures(output: str) -> Dict[str, List[Tuple[str, str]]]:
    """Categorize test failures by type."""
    categories = defaultdict(list)
    
    # Pattern to match test failures
    test_pattern = r"FAILED (tests/integration/test__\w+\.py::\w+)"
    
    # Patterns for different failure types
    patterns = {
        "ParamValidationError": r"expecting ParamValidationError but got EntityValidationError",
        "Negative limit validation": r"negative.*limit|limit.*negative|limit.*-\d+",
        "Error message mismatch": r"assert.*==.*\n.*\+.*\n.*-",
        "UUID validation": r"uuid.*invalid|Invalid UUID",
        "Unknown error message": r"Unknown error|UnknownError",
        "Table not found message": r"Table not found|ResourceNotFoundException",
        "Could not list": r"Could not list.*from",
        "Throughput exceeded": r"ProvisionedThroughputExceededException|Throughput exceeded",
    }
    
    lines = output.split('\n')
    current_test = None
    
    for i, line in enumerate(lines):
        # Check for test name
        test_match = re.search(test_pattern, line)
        if test_match:
            current_test = test_match.group(1)
            
            # Look ahead for the error details
            error_details = []
            for j in range(i+1, min(i+20, len(lines))):
                if lines[j].strip():
                    error_details.append(lines[j])
                if "AssertionError" in lines[j] or "ValueError" in lines[j]:
                    break
            
            error_text = '\n'.join(error_details)
            
            # Categorize based on patterns
            categorized = False
            for category, pattern in patterns.items():
                if re.search(pattern, error_text, re.IGNORECASE):
                    categories[category].append((current_test, error_text[:200]))
                    categorized = True
                    break
            
            if not categorized:
                categories["Other"].append((current_test, error_text[:200]))
    
    return categories

def main():
    print("Analyzing test failures...")
    output = run_tests_and_capture_failures()
    
    categories = categorize_failures(output)
    
    print("\n=== Test Failure Analysis ===\n")
    
    total_failures = sum(len(tests) for tests in categories.values())
    print(f"Total failures: {total_failures}\n")
    
    for category, tests in sorted(categories.items(), key=lambda x: -len(x[1])):
        print(f"\n{category}: {len(tests)} failures")
        print("-" * 50)
        for test, error in tests[:3]:  # Show first 3 examples
            print(f"  {test}")
            if error:
                print(f"    Error: {error[:100]}...")
        if len(tests) > 3:
            print(f"  ... and {len(tests) - 3} more")

    # Suggest fixes
    print("\n\n=== Suggested Fixes ===\n")
    
    if "ParamValidationError" in categories:
        print("1. ParamValidationError vs EntityValidationError:")
        print("   - These tests expect boto3's ParamValidationError for negative limits")
        print("   - Our code correctly validates early with EntityValidationError")
        print("   - Consider updating test expectations\n")
    
    if "Error message mismatch" in categories:
        print("2. Error Message Mismatches:")
        print("   - Update error_config.py to match expected messages")
        print("   - Or update error_handlers.py for specific operations\n")
    
    if "Table not found message" in categories:
        print("3. Table Not Found Messages:")
        print("   - Some operations expect specific formats")
        print("   - Check _handle_resource_not_found in error_handlers.py\n")

if __name__ == "__main__":
    main()
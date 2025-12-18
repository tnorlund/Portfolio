#!/usr/bin/env python3
"""Analyze Phase 4 pylint violations for receipt_dynamo"""

import re
import subprocess
from collections import defaultdict


def analyze_violations():
    """Run pylint and analyze specific violation types"""

    # Violation types to check
    checks = {
        "C0114": "Missing module docstring",
        "C0115": "Missing class docstring",
        "C0116": "Missing function/method docstring",
        "C0103": "Invalid name (naming convention)",
        "W0719": "Broad exception raised",
        "W0707": "Raise missing from",
        "R0903": "Too few public methods",
    }

    results = defaultdict(list)

    for check_id, description in checks.items():
        print(f"\nAnalyzing {check_id}: {description}")

        cmd = f"cd /Users/tnorlund/GitHub/example/receipt_dynamo && pylint . --disable=all --enable={check_id} 2>/dev/null | grep -E '(receipt_dynamo|{check_id})'"

        try:
            output = subprocess.check_output(cmd, shell=True, text=True)
            lines = output.strip().split("\n")

            current_file = None
            for line in lines:
                if line.startswith("*"):
                    # Extract module name
                    match = re.search(r"Module (receipt_dynamo\.[^\s]+)", line)
                    if match:
                        current_file = match.group(1)
                elif check_id in line and current_file:
                    # Extract line number and violation
                    match = re.search(r":(\d+):\d+: " + check_id, line)
                    if match:
                        line_num = match.group(1)
                        results[check_id].append(f"{current_file}:{line_num}")
        except subprocess.CalledProcessError:
            # No violations found
            pass

    # Print summary
    print("\n=== PHASE 4 VIOLATION SUMMARY ===")
    total_violations = 0
    for check_id, violations in sorted(results.items(), key=lambda x: -len(x[1])):
        count = len(violations)
        total_violations += count
        print(f"\n{check_id} ({checks[check_id]}): {count} violations")
        # Show first 5 examples
        for v in violations[:5]:
            print(f"  - {v}")
        if count > 5:
            print(f"  ... and {count - 5} more")

    print(f"\nTOTAL VIOLATIONS: {total_violations}")

    # Check for camelCase methods
    print("\n=== CAMELCASE METHOD ANALYSIS ===")
    cmd = "rg 'def [a-z]+[A-Z]' /Users/tnorlund/GitHub/example/receipt_dynamo -t py --no-heading"
    try:
        output = subprocess.check_output(cmd, shell=True, text=True)
        camel_methods = output.strip().split("\n")
        print(f"Found {len(camel_methods)} camelCase methods")

        # Group by file
        file_methods = defaultdict(list)
        for method in camel_methods[:10]:  # Show first 10
            parts = method.split(":def ")
            if len(parts) == 2:
                filepath = parts[0].replace(
                    "/Users/tnorlund/GitHub/example/receipt_dynamo/", ""
                )
                method_name = parts[1].split("(")[0]
                file_methods[filepath].append(method_name)

        for filepath, methods in sorted(file_methods.items()):
            print(f"\n{filepath}:")
            for method in methods:
                print(f"  - {method}")
    except subprocess.CalledProcessError:
        print("No camelCase methods found")


if __name__ == "__main__":
    analyze_violations()

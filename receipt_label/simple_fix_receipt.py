#!/usr/bin/env python3
"""Simple replacement script for ReceiptWord constructor."""

import re
from pathlib import Path


def fix_file(filepath):
    with open(filepath, "r") as f:
        content = f.read()

    # Add import if not present
    if "create_test_receipt_word" not in content and "ReceiptWord(" in content:
        # Find the line with receipt_dynamo imports
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "from receipt_dynamo.entities import" in line:
                lines.insert(
                    i + 1, "from tests.helpers import create_test_receipt_word"
                )
                content = "\n".join(lines)
                break

    # Simple replacement - just replace ReceiptWord with create_test_receipt_word
    # when x1, y1, x2, y2 are present
    content = re.sub(r"\bReceiptWord\(", "create_test_receipt_word(", content)

    with open(filepath, "w") as f:
        f.write(content)

    print(f"Fixed {filepath}")


# Fix all test files
test_files = [
    "tests/unit/pattern_detection/test_contact_detector.py",
    "tests/unit/pattern_detection/test_currency_detector.py",
    "tests/unit/pattern_detection/test_datetime_detector.py",
    "tests/unit/pattern_detection/test_parallel_orchestrator.py",
    "tests/unit/pattern_detection/test_quantity_detector.py",
]

for file in test_files:
    if Path(file).exists():
        fix_file(file)

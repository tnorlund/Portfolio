#!/usr/bin/env python3
"""Fix duplicate lines in Python files"""

import re
from pathlib import Path


def fix_duplicates(filepath: Path) -> int:
    """Remove duplicate consecutive lines"""
    with open(filepath, "r") as f:
        lines = f.readlines()

    new_lines = []
    prev_line = None
    duplicates_removed = 0

    for line in lines:
        # Skip duplicate lines (exact match)
        if line == prev_line and line.strip() and "raise" in line:
            duplicates_removed += 1
            continue
        new_lines.append(line)
        prev_line = line

    if duplicates_removed > 0:
        with open(filepath, "w") as f:
            f.writelines(new_lines)
        print(f"Fixed {filepath.name}: removed {duplicates_removed} duplicate lines")

    return duplicates_removed


def main():
    """Fix duplicates in all Python files"""
    data_dir = Path("/Users/tnorlund/GitHub/example/receipt_dynamo/receipt_dynamo/data")

    total_fixed = 0

    for filepath in data_dir.rglob("*.py"):
        fixed = fix_duplicates(filepath)
        total_fixed += fixed

    print(f"\nTotal: Removed {total_fixed} duplicate lines")


if __name__ == "__main__":
    main()

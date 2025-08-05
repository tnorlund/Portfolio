#!/usr/bin/env python3
"""Fix invalid 'from e' usage outside except blocks"""

import re
from pathlib import Path


def fix_invalid_from_e(filepath: Path) -> int:
    """Fix 'from e' used outside of except blocks"""
    with open(filepath, "r") as f:
        content = f.read()

    lines = content.split("\n")
    new_lines = []
    changes = 0
    in_except_block = False

    for i, line in enumerate(lines):
        # Track except blocks
        if re.match(r"\s*except.*as\s+\w+:", line):
            in_except_block = True
        elif in_except_block and line and not line[0].isspace():
            in_except_block = False

        # Fix invalid 'from e' outside except blocks
        if " from e" in line and not in_except_block:
            # Remove the invalid 'from e'
            new_line = line.replace(" from e", "")
            new_lines.append(new_line)
            changes += 1
        else:
            new_lines.append(line)

    if changes > 0:
        with open(filepath, "w") as f:
            f.write("\n".join(new_lines))
        print(
            f"Fixed {filepath.name}: removed {changes} invalid 'from e' references"
        )

    return changes


def main():
    """Fix all invalid 'from e' references"""
    data_dir = Path(
        "/Users/tnorlund/GitHub/example/receipt_dynamo/receipt_dynamo/data"
    )

    total_fixed = 0

    for filepath in data_dir.rglob("*.py"):
        fixed = fix_invalid_from_e(filepath)
        total_fixed += fixed

    print(f"\nTotal: Fixed {total_fixed} invalid 'from e' references")


if __name__ == "__main__":
    main()

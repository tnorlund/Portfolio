#!/usr/bin/env python3
"""Script to remove duplicate validation code that CommonValidationMixin now handles."""

import ast
import os
import re
from typing import List, Tuple


def find_validation_patterns(file_path: str) -> List[Tuple[int, str, str]]:
    """Find validation patterns that can be replaced with mixin methods."""
    patterns = []

    with open(file_path, "r") as f:
        content = f.read()
        lines = content.split("\n")

    # Pattern 1: image_id validation
    image_id_patterns = [
        (
            r'if image_id is None:\s*raise EntityValidationError\("image_id cannot be None"\)',
            "_validate_image_id",
        ),
        (
            r"assert_valid_uuid\(image_id\)",
            None,
        ),  # This is part of _validate_image_id
    ]

    # Pattern 2: receipt_id validation
    receipt_id_patterns = [
        (
            r'if receipt_id is None:\s*raise EntityValidationError\("receipt_id cannot be None"\)',
            "_validate_receipt_id",
        ),
        (
            r'if not isinstance\(receipt_id, int\) or receipt_id <= 0:\s*raise EntityValidationError\(\s*["\']receipt_id must be a positive integer',
            "_validate_receipt_id",
        ),
    ]

    # Pattern 3: last_evaluated_key validation
    lek_patterns = [
        (
            r'if last_evaluated_key is not None:\s*if not isinstance\(last_evaluated_key, dict\):\s*raise EntityValidationError\(\s*["\']last_evaluated_key must be a dictionary',
            "_validate_pagination_key",
        ),
        (
            r'if last_evaluated_key is not None and not isinstance\(\s*last_evaluated_key, dict\s*\):\s*raise EntityValidationError\(\s*["\']last_evaluated_key must be a dictionary',
            "_validate_pagination_key",
        ),
    ]

    for i, line in enumerate(lines):
        # Check image_id patterns
        for pattern, replacement in image_id_patterns:
            if re.search(pattern, line):
                patterns.append((i + 1, line.strip(), replacement))

        # Check receipt_id patterns
        for pattern, replacement in receipt_id_patterns:
            if re.search(pattern, line):
                patterns.append((i + 1, line.strip(), replacement))

        # Check last_evaluated_key patterns
        for pattern, replacement in lek_patterns:
            if re.search(pattern, line):
                patterns.append((i + 1, line.strip(), replacement))

    return patterns


def should_add_mixin(file_path: str) -> bool:
    """Check if file should have CommonValidationMixin added."""
    with open(file_path, "r") as f:
        content = f.read()

    # Check if already has CommonValidationMixin
    if "CommonValidationMixin" in content:
        return False

    # Check if it has validation patterns
    patterns = find_validation_patterns(file_path)
    return len(patterns) > 0


def get_files_to_update() -> List[str]:
    """Get list of files that need updating."""
    data_dir = "receipt_dynamo/data"
    files_to_update = []

    for file in os.listdir(data_dir):
        if file.startswith("_") and file.endswith(".py"):
            file_path = os.path.join(data_dir, file)
            patterns = find_validation_patterns(file_path)
            if patterns:
                files_to_update.append((file_path, patterns))

    return files_to_update


def main():
    """Main function to analyze validation patterns."""
    files_to_update = get_files_to_update()

    print(
        f"Found {len(files_to_update)} files with duplicate validation code:\n"
    )

    for file_path, patterns in files_to_update:
        print(f"\n{file_path}:")
        for line_num, line, replacement in patterns:
            if replacement:
                print(
                    f"  Line {line_num}: Replace with self.{replacement}(...)"
                )
            else:
                print(f"  Line {line_num}: Remove (handled by mixin method)")
            print(f"    {line[:80]}...")

    # Group by pattern type
    print("\n\nSummary by validation type:")
    image_id_count = sum(
        1
        for f, patterns in files_to_update
        for _, _, r in patterns
        if r == "_validate_image_id"
    )
    receipt_id_count = sum(
        1
        for f, patterns in files_to_update
        for _, _, r in patterns
        if r == "_validate_receipt_id"
    )
    lek_count = sum(
        1
        for f, patterns in files_to_update
        for _, _, r in patterns
        if r == "_validate_pagination_key"
    )

    print(f"  image_id validations: {image_id_count}")
    print(f"  receipt_id validations: {receipt_id_count}")
    print(f"  last_evaluated_key validations: {lek_count}")

    # Show which files need CommonValidationMixin added
    print("\n\nFiles that need CommonValidationMixin added:")
    for file_path, _ in files_to_update:
        with open(file_path, "r") as f:
            if "CommonValidationMixin" not in f.read():
                print(f"  {file_path}")


if __name__ == "__main__":
    main()

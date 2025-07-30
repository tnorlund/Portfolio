#!/usr/bin/env python3
"""Script to remove duplicate validation code that CommonValidationMixin now handles."""

import os
import re
from typing import Dict, List, Tuple


def find_validation_blocks(
    file_path: str,
) -> Dict[str, List[Tuple[int, int, str]]]:
    """Find validation blocks that can be replaced with mixin methods."""
    with open(file_path, "r") as f:
        lines = f.readlines()

    validation_blocks = {
        "image_id": [],
        "receipt_id": [],
        "last_evaluated_key": [],
    }

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Pattern 1: image_id validation block
        if "image_id is None:" in line or "image_id cannot be None" in line:
            start = i
            # Look for assert_valid_uuid within next few lines
            j = i + 1
            while j < min(i + 5, len(lines)):
                if "assert_valid_uuid(image_id)" in lines[j]:
                    validation_blocks["image_id"].append(
                        (start, j + 1, "self._validate_image_id(image_id)")
                    )
                    i = j
                    break
                j += 1

        # Pattern 2: receipt_id validation block
        elif (
            "receipt_id is None:" in line
            or "receipt_id cannot be None" in line
        ):
            start = i
            # Look for positive integer check within next few lines
            j = i + 1
            found_complete = False
            while j < min(i + 10, len(lines)):
                if (
                    "positive integer" in lines[j]
                    or "receipt_id <= 0" in lines[j]
                ):
                    validation_blocks["receipt_id"].append(
                        (start, j + 1, "self._validate_receipt_id(receipt_id)")
                    )
                    found_complete = True
                    i = j
                    break
                j += 1
            if not found_complete:
                # Just the None check
                validation_blocks["receipt_id"].append(
                    (start, start + 1, "self._validate_receipt_id(receipt_id)")
                )

        # Pattern 3: last_evaluated_key validation
        elif (
            "last_evaluated_key is not None" in line
            and "isinstance(last_evaluated_key, dict)"
            in lines[i : i + 5].join("")
        ):
            start = i
            j = i
            while j < min(i + 10, len(lines)):
                if "dictionary" in lines[j] and (
                    "raise" in lines[j] or "raise" in lines[j - 1]
                ):
                    validation_blocks["last_evaluated_key"].append(
                        (
                            start,
                            j + 1,
                            "self._validate_pagination_key(last_evaluated_key)",
                        )
                    )
                    i = j
                    break
                j += 1

        i += 1

    return validation_blocks


def count_validation_calls(file_path: str) -> Dict[str, int]:
    """Count how many times each validation would be called."""
    with open(file_path, "r") as f:
        content = f.read()

    counts = {
        "image_id": len(re.findall(r"assert_valid_uuid\(image_id\)", content)),
        "receipt_id": len(
            re.findall(
                r"receipt_id.*cannot be None|receipt_id.*positive integer",
                content,
            )
        ),
        "last_evaluated_key": len(
            re.findall(r"last_evaluated_key.*must be a dictionary", content)
        ),
    }

    return counts


def needs_common_validation_mixin(file_path: str) -> bool:
    """Check if file needs CommonValidationMixin."""
    with open(file_path, "r") as f:
        content = f.read()

    if "CommonValidationMixin" in content:
        return False

    counts = count_validation_calls(file_path)
    return sum(counts.values()) > 0


def main():
    """Main function to analyze validation patterns."""
    data_dir = "receipt_dynamo/data"
    files_with_validation = []

    for file in sorted(os.listdir(data_dir)):
        if file.startswith("_") and file.endswith(".py"):
            file_path = os.path.join(data_dir, file)
            counts = count_validation_calls(file_path)
            if sum(counts.values()) > 0:
                needs_mixin = needs_common_validation_mixin(file_path)
                files_with_validation.append((file_path, counts, needs_mixin))

    print(f"Found {len(files_with_validation)} files with validation code:\n")

    # Group by whether they need mixin
    needs_mixin_files = [f for f in files_with_validation if f[2]]
    has_mixin_files = [f for f in files_with_validation if not f[2]]

    print("Files that NEED CommonValidationMixin added:")
    total_validations = 0
    for file_path, counts, _ in needs_mixin_files:
        total = sum(counts.values())
        total_validations += total
        print(f"  {os.path.basename(file_path):40} - {total} validations")
        if counts["image_id"] > 0:
            print(f"    - image_id: {counts['image_id']}")
        if counts["receipt_id"] > 0:
            print(f"    - receipt_id: {counts['receipt_id']}")
        if counts["last_evaluated_key"] > 0:
            print(f"    - last_evaluated_key: {counts['last_evaluated_key']}")

    print(f"\nTotal validations to replace: {total_validations}")

    print("\n\nFiles that already have CommonValidationMixin:")
    for file_path, counts, _ in has_mixin_files:
        total = sum(counts.values())
        if total > 0:
            print(
                f"  {os.path.basename(file_path):40} - {total} validations (check if using mixin)"
            )

    # Show example replacements
    if needs_mixin_files:
        print("\n\nExample replacements needed:")
        example_file = needs_mixin_files[0][0]
        with open(example_file, "r") as f:
            lines = f.readlines()

        print(f"\nIn {os.path.basename(example_file)}:")

        # Find an image_id validation example
        for i, line in enumerate(lines):
            if "assert_valid_uuid(image_id)" in line:
                print(f"\n  Line {i+1}: Replace validation block with:")
                print(f"    self._validate_image_id(image_id)")
                break


if __name__ == "__main__":
    main()

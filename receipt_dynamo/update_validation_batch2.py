#!/usr/bin/env python3
"""Script to update remaining files to use CommonValidationMixin methods - batch 2."""

import os
import re
from typing import List, Tuple


def update_validation_in_file(
    file_path: str, add_mixin: bool = False
) -> List[str]:
    """Update validation code to use mixin methods."""
    with open(file_path, "r") as f:
        lines = f.readlines()

    changes = []
    modified_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]
        modified = False

        # Pattern 1: Replace image_id validation blocks
        if (
            "if image_id is None:" in line
            and i + 1 < len(lines)
            and "raise EntityValidationError" in lines[i + 1]
        ):
            # Look ahead for assert_valid_uuid
            j = i + 2
            while j < min(i + 10, len(lines)):
                if "assert_valid_uuid(image_id)" in lines[j]:
                    # Replace entire block with mixin call
                    indent = len(line) - len(line.lstrip())
                    modified_lines.append(
                        " " * indent + "self._validate_image_id(image_id)\n"
                    )
                    changes.append(
                        f"Lines {i+1}-{j+1}: Replaced image_id validation with self._validate_image_id(image_id)"
                    )
                    i = j
                    modified = True
                    break
                j += 1

        # Pattern 2: Replace receipt_id validation blocks
        elif (
            "if receipt_id is None:" in line
            and i + 1 < len(lines)
            and "raise EntityValidationError" in lines[i + 1]
        ):
            # Look ahead for positive integer check
            j = i + 2
            found_complete = False
            while j < min(i + 10, len(lines)):
                if (
                    "positive integer" in lines[j]
                    or "receipt_id <= 0" in lines[j]
                ):
                    # Replace entire block with mixin call
                    indent = len(line) - len(line.lstrip())
                    modified_lines.append(
                        " " * indent
                        + "self._validate_receipt_id(receipt_id)\n"
                    )
                    changes.append(
                        f"Lines {i+1}-{j+3}: Replaced receipt_id validation with self._validate_receipt_id(receipt_id)"
                    )
                    # Skip to end of validation block
                    while j < len(lines) and (
                        "raise" not in lines[j]
                        or "EntityValidationError" in lines[j]
                    ):
                        j += 1
                    i = j - 1
                    found_complete = True
                    modified = True
                    break
                j += 1

            if not found_complete and not modified:
                # Just replace the None check
                indent = len(line) - len(line.lstrip())
                modified_lines.append(
                    " " * indent + "self._validate_receipt_id(receipt_id)\n"
                )
                changes.append(
                    f"Lines {i+1}-{i+2}: Replaced receipt_id None check with self._validate_receipt_id(receipt_id)"
                )
                i += 1
                modified = True

        # Pattern 3: Replace last_evaluated_key validation
        elif "if last_evaluated_key is not None:" in line:
            # Check if this is a validation block
            if (
                i + 1 < len(lines)
                and "isinstance(last_evaluated_key, dict)" in lines[i + 1]
            ):
                j = i + 1
                while j < min(i + 10, len(lines)):
                    if (
                        "raise EntityValidationError" in lines[j]
                        and "dictionary" in lines[j]
                    ):
                        # Replace entire block with mixin call
                        indent = len(line) - len(line.lstrip())
                        modified_lines.append(
                            " " * indent
                            + "self._validate_pagination_key(last_evaluated_key)\n"
                        )
                        changes.append(
                            f"Lines {i+1}-{j+1}: Replaced last_evaluated_key validation with self._validate_pagination_key(last_evaluated_key)"
                        )
                        i = j
                        modified = True
                        break
                    j += 1

        # Pattern 4: Standalone assert_valid_uuid calls
        elif "assert_valid_uuid(image_id)" in line and not any(
            "_validate_image_id" in l
            for l in modified_lines[-5:]
            if modified_lines
        ):
            indent = len(line) - len(line.lstrip())
            modified_lines.append(
                " " * indent + "self._validate_image_id(image_id)\n"
            )
            changes.append(
                f"Line {i+1}: Replaced assert_valid_uuid(image_id) with self._validate_image_id(image_id)"
            )
            modified = True

        # Pattern 5: Combined last_evaluated_key check
        elif (
            "if last_evaluated_key is not None and not isinstance(" in line
            and "last_evaluated_key, dict" in line
        ):
            # Look for the raise statement
            j = i + 1
            while j < min(i + 5, len(lines)):
                if (
                    "raise EntityValidationError" in lines[j]
                    and "dictionary" in lines[j]
                ):
                    indent = len(line) - len(line.lstrip())
                    modified_lines.append(
                        " " * indent
                        + "self._validate_pagination_key(last_evaluated_key)\n"
                    )
                    changes.append(
                        f"Lines {i+1}-{j+1}: Replaced combined last_evaluated_key check with self._validate_pagination_key(last_evaluated_key)"
                    )
                    i = j
                    modified = True
                    break
                j += 1

        if not modified:
            modified_lines.append(line)

        i += 1

    # Add CommonValidationMixin to imports and class if needed
    if add_mixin:
        modified_lines = add_common_validation_mixin(modified_lines)
        changes.append(
            "Added CommonValidationMixin to imports and class definition"
        )

    # Write back to file
    if changes:
        with open(file_path, "w") as f:
            f.writelines(modified_lines)

    return changes


def add_common_validation_mixin(lines: List[str]) -> List[str]:
    """Add CommonValidationMixin to imports and class definition."""
    modified_lines = []
    import_added = False

    i = 0
    while i < len(lines):
        line = lines[i]

        # Add to imports
        if (
            "from receipt_dynamo.data.base_operations import" in line
            and not import_added
        ):
            # Check if it's a multi-line import
            j = i
            while j < len(lines) and ")" not in lines[j]:
                j += 1

            # Find where to insert CommonValidationMixin
            inserted = False
            for k in range(i, j + 1):
                if "QueryByTypeMixin" in lines[k] and not inserted:
                    lines[k] = lines[k].rstrip() + "\n"
                    modified_lines.append(lines[k])
                    indent = len(lines[k]) - len(lines[k].lstrip())
                    modified_lines.append(
                        " " * indent + "CommonValidationMixin,\n"
                    )
                    import_added = True
                    inserted = True
                elif (
                    "handle_dynamodb_errors" in lines[k]
                    and not import_added
                    and not inserted
                ):
                    # Insert before handle_dynamodb_errors
                    indent = len(lines[k]) - len(lines[k].lstrip())
                    modified_lines.append(
                        " " * indent + "CommonValidationMixin,\n"
                    )
                    modified_lines.append(lines[k])
                    import_added = True
                    inserted = True
                elif k <= j:
                    modified_lines.append(lines[k])

            # Skip to after the import
            i = j

        # Add to class definition
        elif line.strip().startswith("class _") and "(" in line:
            # Find the class definition
            j = i
            while j < len(lines) and "):" not in "".join(lines[i : j + 1]):
                j += 1

            # Check if CommonValidationMixin is already there
            class_def = "".join(lines[i : j + 1])
            if "CommonValidationMixin" not in class_def:
                # Add before the closing parenthesis
                for k in range(i, j + 1):
                    if k == j and "):" in lines[k]:
                        # Add CommonValidationMixin before the closing
                        lines[k] = lines[k].replace(
                            "):", ",\n    CommonValidationMixin,\n):"
                        )
                    modified_lines.append(lines[k])

                # Skip to after class definition
                i = j
            else:
                modified_lines.append(line)
        else:
            modified_lines.append(line)

        i += 1

    return modified_lines


def main():
    """Main function to update validation code."""
    data_dir = "receipt_dynamo/data"

    # Files that need CommonValidationMixin added (batch 2)
    files_need_mixin = [
        "_ocr_job.py",
        "_ocr_routing_decision.py",
        "_receipt.py",
        "_receipt_chatgpt_validation.py",
        "_receipt_letter.py",
        "_receipt_line.py",
        "_receipt_line_item_analysis.py",
        "_receipt_metadata.py",
        "_receipt_structure_analysis.py",
        "_receipt_validation_category.py",
    ]

    total_changes = 0

    print("Updating batch 2 files that need CommonValidationMixin...")
    for file_name in files_need_mixin:
        file_path = os.path.join(data_dir, file_name)
        if os.path.exists(file_path):
            changes = update_validation_in_file(file_path, add_mixin=True)
            if changes:
                print(f"\n{file_name}:")
                for change in changes:
                    print(f"  - {change}")
                total_changes += len(changes)

    print(f"\n\nTotal changes made: {total_changes}")
    print(
        f"Files updated: {len([f for f in files_need_mixin if os.path.exists(os.path.join(data_dir, f))])}"
    )


if __name__ == "__main__":
    main()

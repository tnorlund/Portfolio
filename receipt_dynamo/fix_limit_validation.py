#!/usr/bin/env python3
"""
Fix inconsistent limit validation across the codebase.

Some files skip limit <= 0 validation due to a previous "compatibility" decision,
but the tests and Pythonic best practices expect EntityValidationError for
negative limits. This script restores consistent early parameter validation.
"""

import os
import re
from typing import List, Tuple


def find_files_with_limit_validation_issues():
    """Find files that need limit validation fixes."""
    files_to_fix = []

    # Files that likely need validation fixes
    files_to_check = [
        "receipt_dynamo/data/_receipt.py",
        "receipt_dynamo/data/_receipt_letter.py",
        "receipt_dynamo/data/_receipt_validation_result.py",
        "receipt_dynamo/data/base_operations/base.py",
    ]

    for file_path in files_to_check:
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                content = f.read()
                # Look for missing limit validation or explicit skips
                if (
                    "Skip validation for negative limits" in content
                    or "limit <= 0" not in content
                    and "def list_" in content
                ):
                    files_to_fix.append(file_path)

    return files_to_fix


def fix_base_operations_validation():
    """Fix the base operations _validate_pagination_params method."""
    file_path = "receipt_dynamo/data/base_operations/base.py"

    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return False

    with open(file_path, "r") as f:
        content = f.read()

    # Find the _validate_pagination_params method and restore limit validation
    old_validation = """        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("Limit must be an integer")
            # Skip validation for negative limits to allow boto3
            # ParamValidationError for test compatibility"""

    new_validation = """        if limit is not None:
            if not isinstance(limit, int):
                raise EntityValidationError("Limit must be an integer")
            if limit <= 0:
                raise EntityValidationError("Limit must be greater than 0")"""

    if old_validation in content:
        content = content.replace(old_validation, new_validation)

        with open(file_path, "w") as f:
            f.write(content)
        print(f"Fixed limit validation in {file_path}")
        return True
    else:
        print(f"No matching validation pattern found in {file_path}")
        return False


def fix_receipt_letter_validation():
    """Fix the receipt letter validation."""
    file_path = "receipt_dynamo/data/_receipt_letter.py"

    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return False

    with open(file_path, "r") as f:
        content = f.read()

    # Add limit validation after the type check
    old_pattern = r'(\s+if limit is not None and not isinstance\(limit, int\):\n\s+raise EntityValidationError\("limit must be an integer or None\."\)\n)(\s+# Skip validation for negative limits to allow boto3 ParamValidationError\n)?'

    new_validation = r'\1        if limit is not None and limit <= 0:\n            raise EntityValidationError("Limit must be greater than 0")\n'

    if re.search(old_pattern, content):
        content = re.sub(old_pattern, new_validation, content)

        with open(file_path, "w") as f:
            f.write(content)
        print(f"Fixed limit validation in {file_path}")
        return True
    else:
        print(f"No matching pattern found in {file_path}")
        return False


def fix_receipt_validation():
    """Fix the receipt validation."""
    file_path = "receipt_dynamo/data/_receipt.py"

    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return False

    with open(file_path, "r") as f:
        content = f.read()

    # Look for the list_receipts method and add limit validation
    # The method calls _validate_pagination_params first, then has an additional check
    pattern = r'(\s+# Additional validation specific to list_receipts\n)(\s+if limit is not None and limit <= 0:\n\s+raise EntityValidationError\("Limit must be greater than 0"\)\n)?'

    replacement = r'\1        if limit is not None and limit <= 0:\n            raise EntityValidationError("Limit must be greater than 0")\n'

    if re.search(pattern, content):
        # Check if the validation is already there
        if (
            "Additional validation specific to list_receipts" in content
            and "limit <= 0" in content
        ):
            print(f"Limit validation already exists in {file_path}")
            return True
        else:
            content = re.sub(pattern, replacement, content)
            with open(file_path, "w") as f:
                f.write(content)
            print(f"Fixed limit validation in {file_path}")
            return True
    else:
        print(f"No matching pattern found in {file_path}")
        return False


def fix_receipt_validation_result():
    """Fix receipt validation result validation."""
    file_path = "receipt_dynamo/data/_receipt_validation_result.py"

    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return False

    with open(file_path, "r") as f:
        content = f.read()

    # Look for methods that need limit validation
    methods_needing_validation = [
        "list_receipt_validation_results",
        "list_receipt_validation_results_by_type",
    ]

    changes_made = False
    for method in methods_needing_validation:
        # Look for the method definition and add validation if missing
        method_pattern = rf'(def {method}\([^)]+\):[^{{]+?)(\n\s+"""[^"]+"""[^{{]+?)(\n\s+# Validate parameters\n)?(\s+self\._validate_pagination_params\([^)]+\))'

        replacement = r'\1\2\n        # Validate parameters\n        self._validate_pagination_params(limit, last_evaluated_key)\n        if limit is not None and limit <= 0:\n            raise EntityValidationError("Limit must be greater than 0")\4'

        if re.search(method_pattern, content, re.DOTALL):
            if f"limit <= 0" not in content or content.count("limit <= 0") < 2:
                content = re.sub(
                    method_pattern, replacement, content, flags=re.DOTALL
                )
                changes_made = True

    if changes_made:
        with open(file_path, "w") as f:
            f.write(content)
        print(f"Fixed limit validation in {file_path}")
        return True
    else:
        print(f"No changes needed or pattern not found in {file_path}")
        return False


def main():
    """Main function to fix limit validation issues."""
    print("Fixing inconsistent limit validation across the codebase...")

    fixes_applied = []

    # Fix base operations first
    if fix_base_operations_validation():
        fixes_applied.append("base_operations/base.py")

    # Now fix specific files
    if fix_receipt_letter_validation():
        fixes_applied.append("_receipt_letter.py")

    if fix_receipt_validation():
        fixes_applied.append("_receipt.py")

    if fix_receipt_validation_result():
        fixes_applied.append("_receipt_validation_result.py")

    print(f"\nCompleted limit validation fixes:")
    for fix in fixes_applied:
        print(f"  - {fix}")

    if fixes_applied:
        print(
            f"\nFixed {len(fixes_applied)} files to use consistent EntityValidationError for negative limits"
        )
        print(
            "This aligns with Pythonic early parameter validation and test expectations."
        )
    else:
        print("No fixes were needed or patterns weren't found.")


if __name__ == "__main__":
    main()

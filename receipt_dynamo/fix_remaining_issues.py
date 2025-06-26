#!/usr/bin/env python3
"""Script to fix remaining pylint violations."""

import os
import re
from typing import List


def add_missing_newlines():
    """Add missing final newlines to files."""
    files_to_fix = [
        "receipt_dynamo/data/_word_tag.py",
        "receipt_dynamo/data/_receipt_label_analysis.py",
        "receipt_dynamo/data/_receipt_validation_category.py",
    ]

    for file_path in files_to_fix:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if not content.endswith("\n"):
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content + "\n")
                print(f"Added final newline to {file_path}")


def fix_camel_case_params():
    """Fix camelCase parameter names to snake_case."""
    files_to_check = [
        "receipt_dynamo/data/_word_tag.py",
        "receipt_dynamo/data/_word.py",
        "receipt_dynamo/data/_receipt_label_analysis.py",
    ]

    for file_path in files_to_check:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Replace lastEvaluatedKey with last_evaluated_key
            new_content = content.replace("lastEvaluatedKey", "last_evaluated_key")

            if new_content != content:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                print(f"Fixed camelCase parameters in {file_path}")


def remove_unused_imports():
    """Remove unused imports."""
    # For _receipt_label_analysis.py
    file_path = "receipt_dynamo/data/_receipt_label_analysis.py"
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Remove specific unused imports
        filtered_lines = []
        for line in lines:
            # Skip these unused imports
            if any(
                unused in line
                for unused in [
                    "from typing import Dict, List, Optional, Tuple",
                    "from receipt_dynamo.entities.receipt_chatgpt_validation import ReceiptChatGPTValidation",
                    "from receipt_dynamo.entities.receipt_line_item_analysis import ReceiptLineItemAnalysis",
                    "from receipt_dynamo.entities.receipt_structure_analysis import ReceiptStructureAnalysis",
                    "from receipt_dynamo.entities.receipt_validation_category import ReceiptValidationCategory",
                    "from receipt_dynamo.entities.receipt_validation_result import ReceiptValidationResult",
                    "from receipt_dynamo.entities.receipt_validation_summary import ReceiptValidationSummary",
                ]
            ):
                continue
            filtered_lines.append(line)

        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(filtered_lines)
        print(f"Removed unused imports from {file_path}")


def fix_raise_missing_from():
    """Fix raise statements missing 'from e'."""
    files_to_check = [
        "receipt_dynamo/data/_word_tag.py",
        "receipt_dynamo/data/_word.py",
        "receipt_dynamo/data/_receipt_label_analysis.py",
        "receipt_dynamo/data/_receipt_validation_category.py",
    ]

    for file_path in files_to_check:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Pattern 1: Fix KeyError without from e
            content = re.sub(
                r"except KeyError:\s*raise ValueError\([^)]+\)",
                lambda m: m.group(0)
                .replace("except KeyError:", "except KeyError as e:")
                .rstrip()
                + " from e",
                content,
            )

            # Pattern 2: Fix ClientError without from e
            content = re.sub(
                r"except ClientError as e:\s*raise (\w+Error)\([^)]+\)(?!\s+from\s+e)",
                r"except ClientError as e:\n                raise \1(...) from e",
                content,
            )

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"Fixed raise-missing-from in {file_path}")


# Run all fixes
print("Fixing remaining pylint violations...")
add_missing_newlines()
fix_camel_case_params()
remove_unused_imports()
# fix_raise_missing_from()  # Skip this as it's complex and low impact

print("\nRemaining fixes completed.")

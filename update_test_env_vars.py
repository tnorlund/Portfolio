#!/usr/bin/env python3
"""
Script to update test files to use DYNAMODB_TABLE_NAME instead of DYNAMO_TABLE_NAME.
"""

import os
import re
from pathlib import Path

# Files to update in receipt_label tests
TEST_FILES = [
    "receipt_label/receipt_label/tests/conftest.py",
    "receipt_label/receipt_label/tests/test_labeler_integration.py",
    "receipt_label/receipt_label/tests/test_places_api.py",
    "receipt_label/receipt_label/tests/test_ai_usage_performance_integration.py",
    "receipt_label/receipt_label/tests/merchant_validation/conftest.py",
    "receipt_label/receipt_label/tests/merchant_validation/test_result_processor.py",
    "receipt_label/receipt_label/tests/merchant_validation/test_merchant_validation.py",
    "receipt_label/receipt_label/tests/merchant_validation/test_helpers.py",
    "receipt_label/receipt_label/tests/test_label_validation.py",
    "receipt_label/receipt_label/tests/test_label_validation_integration.py",
    "receipt_label/receipt_label/tests/test_ai_usage_integration.py",
]


def update_file(file_path: str):
    """Update a single file to use DYNAMODB_TABLE_NAME."""
    full_path = Path(file_path)
    if not full_path.exists():
        print(f"❌ File not found: {file_path}")
        return False

    try:
        with open(full_path, "r") as f:
            content = f.read()

        original_content = content

        # Replace "DYNAMO_TABLE_NAME" with "DYNAMODB_TABLE_NAME" in strings
        content = re.sub(
            r'"DYNAMO_TABLE_NAME"', '"DYNAMODB_TABLE_NAME"', content
        )
        content = re.sub(
            r"'DYNAMO_TABLE_NAME'", "'DYNAMODB_TABLE_NAME'", content
        )

        # Update comments mentioning the old variable
        content = re.sub(
            r"DYNAMO_TABLE_NAME(?= is set)", "DYNAMODB_TABLE_NAME", content
        )

        if content != original_content:
            with open(full_path, "w") as f:
                f.write(content)
            print(f"✅ Updated: {file_path}")
            return True
        else:
            print(f"ℹ️  No changes needed: {file_path}")
            return False

    except Exception as e:
        print(f"❌ Error updating {file_path}: {e}")
        return False


def main():
    """Update all test files."""
    print("Updating test files to use DYNAMODB_TABLE_NAME...")

    updated_count = 0
    for file_path in TEST_FILES:
        if update_file(file_path):
            updated_count += 1

    print(
        f"\n📊 Summary: {updated_count} files updated out of {len(TEST_FILES)} total"
    )

    if updated_count > 0:
        print(
            "\n🎉 Migration complete! All test files now use the standardized DYNAMODB_TABLE_NAME."
        )
    else:
        print("\n✨ All files already up to date!")


if __name__ == "__main__":
    main()

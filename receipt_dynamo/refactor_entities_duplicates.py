#!/usr/bin/env python3
"""
Refactor entity files to use shared utilities and eliminate duplicate code.

This script:
1. Updates imports to use the new shared modules
2. Replaces duplicate DynamoDB parsing functions with imports
3. Updates entities to use CDNFieldsMixin
4. Replaces duplicate validation patterns
"""

import os
import re
from typing import List, Tuple


def refactor_dynamodb_parsing_functions(content: str, filename: str) -> str:
    """Replace duplicate DynamoDB parsing functions with imports."""

    # Check if file has the duplicate parsing functions
    if "_parse_dynamodb_map" in content or "_dict_to_dynamodb_map" in content:
        # Add import at the top after other imports
        import_line = "from receipt_dynamo.entities.dynamodb_utils import (\n    parse_dynamodb_map,\n    parse_dynamodb_value,\n    dict_to_dynamodb_map,\n    to_dynamodb_value,\n    validate_required_keys,\n)\n"

        # Find where to insert the import
        lines = content.split("\n")
        import_index = 0
        for i, line in enumerate(lines):
            if line.startswith("from") or line.startswith("import"):
                import_index = i + 1

        # Insert the import
        lines.insert(import_index, import_line)
        content = "\n".join(lines)

        # Replace function definitions with aliases
        content = re.sub(
            r"def _parse_dynamodb_map\(.*?\n(?:.*?\n)*?return result\n",
            "",
            content,
            flags=re.MULTILINE | re.DOTALL,
        )

        content = re.sub(
            r"def _parse_dynamodb_value\(.*?\n(?:.*?\n)*?return None\n",
            "",
            content,
            flags=re.MULTILINE | re.DOTALL,
        )

        content = re.sub(
            r"def _dict_to_dynamodb_map\(self,.*?\n(?:.*?\n)*?return result\n",
            "",
            content,
            flags=re.MULTILINE | re.DOTALL,
        )

        content = re.sub(
            r'def _to_dynamodb_value\(self,.*?\n(?:.*?\n)*?return \{"S": str\(value\)\}\n',
            "",
            content,
            flags=re.MULTILINE | re.DOTALL,
        )

        # Update function calls to use the imported versions
        content = content.replace(
            "_parse_dynamodb_map(", "parse_dynamodb_map("
        )
        content = content.replace(
            "_parse_dynamodb_value(", "parse_dynamodb_value("
        )
        content = content.replace(
            "self._dict_to_dynamodb_map(", "dict_to_dynamodb_map("
        )
        content = content.replace(
            "self._to_dynamodb_value(", "to_dynamodb_value("
        )

    return content


def refactor_cdn_fields(content: str, filename: str) -> str:
    """Update Image and Receipt classes to use CDNFieldsMixin."""

    if filename in ["image.py", "receipt.py"] and "cdn_s3_bucket" in content:
        # Add CDNFieldsMixin import
        import_line = (
            "from receipt_dynamo.entities.cdn_mixin import CDNFieldsMixin\n"
        )

        lines = content.split("\n")
        import_index = 0
        for i, line in enumerate(lines):
            if line.startswith("from") or line.startswith("import"):
                import_index = i + 1

        lines.insert(import_index, import_line)
        content = "\n".join(lines)

        # Add CDNFieldsMixin to class inheritance
        content = re.sub(
            r"class (Image|Receipt)\(DynamoDBEntity\):",
            r"class \1(DynamoDBEntity, CDNFieldsMixin):",
            content,
        )

        # Replace CDN validation code in __post_init__
        # Find and replace the CDN validation block
        cdn_validation_pattern = (
            r"if self\.cdn_.*?_s3_key.*?raise ValueError.*?\n"
        )
        content = re.sub(
            cdn_validation_pattern, "", content, flags=re.MULTILINE | re.DOTALL
        )

        # Add call to validate_cdn_fields
        content = re.sub(
            r"(def __post_init__\(self\):.*?\n)",
            r"\1        self.validate_cdn_fields()\n",
            content,
            flags=re.MULTILINE,
        )

        # Replace CDN fields in to_item method
        # This is more complex - we need to find the CDN fields section and replace it
        # with a call to cdn_fields_to_dynamodb_item

    return content


def refactor_validation_patterns(content: str, filename: str) -> str:
    """Replace duplicate validation patterns with shared function."""

    # Replace the validation pattern in item_to_* functions
    validation_pattern = r"required_keys = \{.*?\}\s*if not required_keys\.issubset\(item\.keys\(\)\):.*?raise ValueError\(.*?\)"

    if re.search(validation_pattern, content, re.DOTALL):
        # Extract entity name from function name
        entity_match = re.search(r"def item_to_(\w+)\(", content)
        if entity_match:
            entity_name = entity_match.group(1).replace("_", " ")

            # Replace the validation block
            def replace_validation(match):
                # Extract the required keys
                keys_match = re.search(
                    r"required_keys = \{([^}]+)\}", match.group(0)
                )
                if keys_match:
                    return f'validate_required_keys(item, {{{keys_match.group(1)}}}, "{entity_name}")'
                return match.group(0)

            content = re.sub(
                validation_pattern,
                replace_validation,
                content,
                flags=re.DOTALL,
            )

    return content


def process_file(filepath: str) -> bool:
    """Process a single entity file."""
    try:
        with open(filepath, "r") as f:
            content = f.read()

        original_content = content
        filename = os.path.basename(filepath)

        # Apply refactorings
        content = refactor_dynamodb_parsing_functions(content, filename)
        content = refactor_cdn_fields(content, filename)
        content = refactor_validation_patterns(content, filename)

        # Write back if changed
        if content != original_content:
            with open(filepath, "w") as f:
                f.write(content)
            print(f"✓ Refactored {filename}")
            return True

        return False

    except Exception as e:
        print(f"✗ Error processing {filepath}: {e}")
        return False


def main():
    """Main function to refactor entity files."""
    print("=== Refactoring Entity Files to Eliminate Duplicate Code ===\n")

    entities_dir = "receipt_dynamo/entities"

    # Files known to have duplicate code
    target_files = [
        "job.py",
        "instance_job.py",
        "job_metric.py",
        "job_resource.py",
        "image.py",
        "receipt.py",
        "receipt_letter.py",
        "receipt_line.py",
    ]

    success_count = 0
    total_count = 0

    for filename in target_files:
        filepath = os.path.join(entities_dir, filename)
        if os.path.exists(filepath):
            total_count += 1
            if process_file(filepath):
                success_count += 1

    # Also check for item_to_* functions in all files
    for filename in os.listdir(entities_dir):
        if filename.endswith(".py") and filename not in target_files:
            filepath = os.path.join(entities_dir, filename)
            with open(filepath, "r") as f:
                if "def item_to_" in f.read():
                    total_count += 1
                    if process_file(filepath):
                        success_count += 1

    print(f"\n✅ Successfully refactored {success_count}/{total_count} files")
    print("\nNext steps:")
    print("1. Run tests to ensure refactoring didn't break anything")
    print("2. Run pylint to check improvement in duplicate code score")


if __name__ == "__main__":
    main()

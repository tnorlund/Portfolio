#!/usr/bin/env python3
"""Fix naming conventions to snake_case"""

import re
from pathlib import Path


def camel_to_snake(name):
    """Convert camelCase to snake_case"""
    # Handle special cases first
    if name == "itemToReceiptWord_Label":
        return "item_to_receipt_word_label"
    if name == "to_ReceiptWord_key":
        return "to_receipt_word_key"

    # Insert underscore before capitals and lowercase everything
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def fix_file_naming(filepath: Path) -> int:
    """Fix naming conventions in a single file"""
    with open(filepath, "r") as f:
        content = f.read()

    original_content = content
    changes = 0

    # Find all function definitions
    function_pattern = r"^(def )([a-z][a-zA-Z_]*[A-Z][a-zA-Z_]*)(\()"
    method_pattern = r"^(\s+def )([a-z][a-zA-Z_]*[A-Z][a-zA-Z_]*)(\()"

    # Replace function definitions
    for pattern in [function_pattern, method_pattern]:
        matches = list(re.finditer(pattern, content, re.MULTILINE))
        for match in reversed(matches):  # Process in reverse to maintain positions
            old_name = match.group(2)
            new_name = camel_to_snake(old_name)

            # Replace definition
            content = content[: match.start(2)] + new_name + content[match.end(2) :]

            # Replace all calls to this function
            # Look for word boundaries to avoid partial matches
            content = re.sub(rf"\b{re.escape(old_name)}\b", new_name, content)

            changes += 1

    # Write back if changed
    if content != original_content:
        with open(filepath, "w") as f:
            f.write(content)
        print(f"Fixed {filepath.name}: {changes} naming conventions")

    return changes


def main():
    """Fix naming conventions in all Python files"""
    receipt_dynamo_path = Path("/Users/tnorlund/GitHub/example/receipt_dynamo")

    total_fixes = 0
    files_fixed = 0

    # Process entities first (they define the functions)
    entities_dir = receipt_dynamo_path / "receipt_dynamo/entities"
    for filepath in entities_dir.glob("*.py"):
        if filepath.name == "__init__.py":
            continue

        fixes = fix_file_naming(filepath)
        if fixes > 0:
            files_fixed += 1
            total_fixes += fixes

    # Then process data layer (they use the functions)
    data_dir = receipt_dynamo_path / "receipt_dynamo/data"
    for filepath in data_dir.glob("*.py"):
        if filepath.name == "__init__.py":
            continue

        fixes = fix_file_naming(filepath)
        if fixes > 0:
            files_fixed += 1
            total_fixes += fixes

    # Process other directories
    for directory in ["receipt_dynamo/services", "receipt_dynamo"]:
        dir_path = receipt_dynamo_path / directory
        if dir_path.exists():
            for filepath in dir_path.glob("*.py"):
                if filepath.name == "__init__.py" or filepath.parent != dir_path:
                    continue

                fixes = fix_file_naming(filepath)
                if fixes > 0:
                    files_fixed += 1
                    total_fixes += fixes

    print(f"\nTotal: Fixed {total_fixes} naming conventions in {files_fixed} files")


if __name__ == "__main__":
    main()

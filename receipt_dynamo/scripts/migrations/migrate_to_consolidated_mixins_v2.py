#!/usr/bin/env python3
"""
Script to migrate data access classes to use consolidated mixins.
This version handles the actual patterns found in the codebase more accurately.
"""

import os
import re
from typing import Dict, List, Optional, Tuple


def extract_class_info(file_path: str) -> Optional[Dict]:
    """Extract class name, mixins, and class definition lines from a file."""
    with open(file_path, "r") as f:
        lines = f.readlines()

    class_start = None
    class_name = None

    for i, line in enumerate(lines):
        if line.strip().startswith("class _") and "(" in line:
            class_start = i
            class_name = re.match(r"class (\w+)", line).group(1)
            break

    if class_start is None:
        return None

    # Find the end of class definition
    class_end = class_start
    paren_count = 0
    for i in range(class_start, len(lines)):
        paren_count += lines[i].count("(") - lines[i].count(")")
        if ")" in lines[i] and paren_count == 0:
            class_end = i
            break

    # Extract mixins
    class_def = "".join(lines[class_start : class_end + 1])
    mixins = re.findall(r"(\w+Mixin)\b", class_def)

    # Check if DynamoDBBaseOperations is present
    has_base = "DynamoDBBaseOperations" in class_def

    return {
        "class_name": class_name,
        "mixins": mixins,
        "class_start": class_start,
        "class_end": class_end,
        "has_base": has_base,
        "file_path": file_path,
    }


def determine_consolidated_mixin(mixins: List[str]) -> Optional[str]:
    """Determine which consolidated mixin to use based on current mixins."""
    mixin_set = set(mixins)

    # Pattern 1: Full featured entity (most common)
    if all(
        m in mixin_set
        for m in [
            "SingleEntityCRUDMixin",
            "BatchOperationsMixin",
            "TransactionalOperationsMixin",
            "QueryByTypeMixin",
            "CommonValidationMixin",
        ]
    ):
        return "FullDynamoEntityMixin"

    # Pattern 2: Standard entity without validation
    if (
        all(
            m in mixin_set
            for m in [
                "SingleEntityCRUDMixin",
                "BatchOperationsMixin",
                "TransactionalOperationsMixin",
                "QueryByTypeMixin",
            ]
        )
        and "CommonValidationMixin" not in mixin_set
    ):
        return "StandardDynamoEntityMixin"

    # Pattern 3: Write operations with validation
    if (
        all(
            m in mixin_set
            for m in [
                "SingleEntityCRUDMixin",
                "BatchOperationsMixin",
                "TransactionalOperationsMixin",
                "CommonValidationMixin",
            ]
        )
        and "QueryByTypeMixin" not in mixin_set
    ):
        return "WriteOperationsMixin + CommonValidationMixin"

    # Pattern 4: Write operations without validation
    if all(
        m in mixin_set
        for m in [
            "SingleEntityCRUDMixin",
            "BatchOperationsMixin",
            "TransactionalOperationsMixin",
        ]
    ) and not any(
        m in mixin_set for m in ["QueryByTypeMixin", "CommonValidationMixin"]
    ):
        return "WriteOperationsMixin"

    # Pattern 5: Batch only (metrics/cache)
    if mixin_set == {"BatchOperationsMixin"}:
        return None  # Keep as is - too minimal to consolidate

    # Pattern 6: Batch + Query
    if mixin_set == {"BatchOperationsMixin", "QueryByTypeMixin"}:
        return "CacheDynamoEntityMixin"

    # Pattern 7: Simple CRUD + Validation
    if mixin_set == {"SingleEntityCRUDMixin", "CommonValidationMixin"}:
        return "SimpleDynamoEntityMixin"

    return None


def migrate_file(info: Dict, dry_run: bool = True) -> Optional[str]:
    """Migrate a single file to use consolidated mixins."""
    if not info["mixins"]:
        return None

    consolidated = determine_consolidated_mixin(info["mixins"])
    if not consolidated:
        return f"{info['class_name']}: No consolidation needed ({', '.join(info['mixins'])})"

    # Handle cases where we need multiple mixins
    if " + " in consolidated:
        consolidated_list = consolidated.split(" + ")
    else:
        consolidated_list = [consolidated]

    if not dry_run:
        with open(info["file_path"], "r") as f:
            lines = f.readlines()

        # Build new class definition
        if info["has_base"]:
            mixins_str = ",\n    ".join(consolidated_list)
            new_class_def = f"class {info['class_name']}(\n    DynamoDBBaseOperations,\n    {mixins_str},\n):"
        else:
            mixins_str = ",\n    ".join(consolidated_list)
            new_class_def = (
                f"class {info['class_name']}(\n    {mixins_str},\n):"
            )

        # Find where the docstring or first method starts
        next_line_idx = info["class_end"] + 1
        while (
            next_line_idx < len(lines) and lines[next_line_idx].strip() == ""
        ):
            next_line_idx += 1

        # Replace lines
        new_lines = (
            lines[: info["class_start"]]
            + [new_class_def + "\n"]
            + lines[next_line_idx:]
        )

        # Update imports
        new_lines = update_imports(new_lines, consolidated_list)

        with open(info["file_path"], "w") as f:
            f.writelines(new_lines)

    return (
        f"{info['class_name']}: {len(info['mixins'])} mixins → {consolidated}"
    )


def update_imports(
    lines: List[str], consolidated_mixins: List[str]
) -> List[str]:
    """Update imports to include the consolidated mixins and remove old ones."""
    new_lines = []
    import_block_start = None
    import_block_end = None

    # Find the base_operations import block
    for i, line in enumerate(lines):
        if "from receipt_dynamo.data.base_operations import" in line:
            import_block_start = i
            j = i
            while j < len(lines) and ")" not in lines[j]:
                j += 1
            import_block_end = j
            break

    if import_block_start is None:
        return lines

    # Extract current imports
    import_block = "".join(lines[import_block_start : import_block_end + 1])

    # Find what to keep (non-mixin imports)
    imports_to_keep = []
    for match in re.findall(r"(\w+),?", import_block):
        if (
            match
            and not match.endswith("Mixin")
            and match != "from"
            and match != "import"
        ):
            imports_to_keep.append(match)

    # Add consolidated mixins
    for mixin in consolidated_mixins:
        if mixin not in imports_to_keep:
            imports_to_keep.append(mixin)

    # Remove duplicates and sort (keeping DynamoDBBaseOperations first)
    unique_imports = []
    if "DynamoDBBaseOperations" in imports_to_keep:
        unique_imports.append("DynamoDBBaseOperations")
    for imp in sorted(imports_to_keep):
        if imp != "DynamoDBBaseOperations" and imp not in unique_imports:
            unique_imports.append(imp)

    # Build new import statement
    new_import = "from receipt_dynamo.data.base_operations import (\n"
    for i, imp in enumerate(unique_imports):
        new_import += f"    {imp},"
        if i < len(unique_imports) - 1:
            new_import += "\n"
    new_import += "\n)\n"

    # Replace the import block
    new_lines = (
        lines[:import_block_start]
        + [new_import]
        + lines[import_block_end + 1 :]
    )

    return new_lines


def main():
    """Main function to migrate all data files."""
    data_dir = "receipt_dynamo/data"

    # Files to migrate (start with a subset for testing)
    test_files = [
        "_receipt.py",  # FullDynamoEntityMixin (already done)
        "_job.py",  # StandardDynamoEntityMixin (already done)
        "_receipt_line.py",  # FullDynamoEntityMixin
        "_image.py",  # WriteOperationsMixin + CommonValidationMixin
        "_receipt_field.py",  # FullDynamoEntityMixin
    ]

    print("=== Migrating test files ===\n")

    for file_name in test_files:
        file_path = os.path.join(data_dir, file_name)
        if os.path.exists(file_path):
            info = extract_class_info(file_path)
            if info:
                # Skip if already migrated
                if len(info["mixins"]) <= 2:
                    print(f"{file_name}: Already migrated")
                    continue

                result = migrate_file(info, dry_run=False)
                if result:
                    print(result)

    print("\n✅ Test migration complete!")
    print("\nNext steps:")
    print("1. Run tests for the migrated files")
    print("2. Check pylint to verify warnings are resolved")
    print("3. If tests pass, run full migration for all files")


if __name__ == "__main__":
    main()

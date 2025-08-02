#!/usr/bin/env python3
"""
Script to migrate data access classes to use consolidated mixins.

This reduces the number of ancestors and eliminates pylint warnings
about too-many-ancestors.
"""

import os
import re
from typing import List, Optional, Tuple

# Mixin patterns to consolidated mixin mapping
CONSOLIDATION_PATTERNS = {
    # Full pattern (most common)
    frozenset(
        [
            "SingleEntityCRUDMixin",
            "BatchOperationsMixin",
            "TransactionalOperationsMixin",
            "QueryByTypeMixin",
            "CommonValidationMixin",
        ]
    ): "FullDynamoEntityMixin",
    # Write operations pattern
    frozenset(
        [
            "SingleEntityCRUDMixin",
            "BatchOperationsMixin",
            "TransactionalOperationsMixin",
        ]
    ): "WriteOperationsMixin",
    # Cache pattern
    frozenset(
        ["BatchOperationsMixin", "QueryByTypeMixin"]
    ): "CacheDynamoEntityMixin",
    # Simple pattern
    frozenset(
        ["SingleEntityCRUDMixin", "CommonValidationMixin"]
    ): "SimpleDynamoEntityMixin",
    # Read-heavy pattern
    frozenset(
        ["QueryByTypeMixin", "QueryByParentMixin", "CommonValidationMixin"]
    ): "ReadOptimizedDynamoEntityMixin",
}


def extract_class_definition(
    file_path: str,
) -> Optional[Tuple[str, List[str], int, int]]:
    """Extract class name and mixins from a file."""
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

    return class_name, mixins, class_start, class_end, has_base


def determine_consolidated_mixin(mixins: List[str]) -> Optional[str]:
    """Determine which consolidated mixin to use based on current mixins."""
    mixin_set = frozenset(mixins)

    # Check exact matches first
    if mixin_set in CONSOLIDATION_PATTERNS:
        return CONSOLIDATION_PATTERNS[mixin_set]

    # Check if it's a superset of any pattern
    for pattern, consolidated in CONSOLIDATION_PATTERNS.items():
        if pattern.issubset(mixin_set):
            # Special case: if it has all full pattern mixins, use FullDynamoEntityMixin
            full_pattern = frozenset(
                [
                    "SingleEntityCRUDMixin",
                    "BatchOperationsMixin",
                    "TransactionalOperationsMixin",
                    "QueryByTypeMixin",
                    "CommonValidationMixin",
                ]
            )
            if full_pattern.issubset(mixin_set):
                return "FullDynamoEntityMixin"
            return consolidated

    return None


def migrate_file(file_path: str, dry_run: bool = True) -> Optional[str]:
    """Migrate a single file to use consolidated mixins."""
    result = extract_class_definition(file_path)
    if not result:
        return None

    class_name, mixins, class_start, class_end, has_base = result

    if not mixins:
        return None

    consolidated = determine_consolidated_mixin(mixins)
    if not consolidated:
        return f"{class_name}: No matching consolidation pattern for {mixins}"

    if not dry_run:
        with open(file_path, "r") as f:
            lines = f.readlines()

        # Build new class definition
        if has_base:
            new_class_def = f"class {class_name}(\n    DynamoDBBaseOperations,\n    {consolidated},\n):"
        else:
            new_class_def = f"class {class_name}(\n    {consolidated},\n):"

        # Replace class definition
        # Find where the docstring or first method starts
        next_line_idx = class_end + 1
        while (
            next_line_idx < len(lines) and lines[next_line_idx].strip() == ""
        ):
            next_line_idx += 1

        # Replace lines
        new_lines = (
            lines[:class_start]
            + [new_class_def + "\n"]
            + lines[next_line_idx:]
        )

        # Update imports
        new_lines = update_imports(new_lines, consolidated)

        with open(file_path, "w") as f:
            f.writelines(new_lines)

    return f"{class_name}: {len(mixins)} mixins → {consolidated}"


def update_imports(lines: List[str], consolidated_mixin: str) -> List[str]:
    """Update imports to include the consolidated mixin."""
    import_updated = False
    new_lines = []

    for i, line in enumerate(lines):
        if (
            "from receipt_dynamo.data.base_operations import" in line
            and not import_updated
        ):
            # Find the import block
            j = i
            while j < len(lines) and ")" not in lines[j]:
                j += 1

            # Check if consolidated mixin is already imported
            import_block = "".join(lines[i : j + 1])
            if consolidated_mixin not in import_block:
                # Add the consolidated mixin
                for k in range(i, j + 1):
                    if k == j:  # Last line of import
                        new_lines.append(f"    {consolidated_mixin},\n")
                    new_lines.append(lines[k])
                import_updated = True
                # Skip the lines we just processed
                lines = lines[:i] + [""] * (j - i + 1) + lines[j + 1 :]
                continue

        new_lines.append(line)

    # Also check if we need to add the import for consolidated_mixins_v2
    has_consolidated_import = any(
        "consolidated_mixins_v2" in line for line in new_lines
    )
    if not has_consolidated_import:
        # Add after the base_operations import
        for i, line in enumerate(new_lines):
            if "from receipt_dynamo.data.base_operations import" in line:
                # Find end of this import
                j = i
                while j < len(new_lines) and ")" not in new_lines[j]:
                    j += 1
                # Insert new import after
                new_lines.insert(
                    j + 1,
                    f"from receipt_dynamo.data.base_operations.consolidated_mixins_v2 import (\n    {consolidated_mixin},\n)\n",
                )
                break

    return new_lines


def main():
    """Main function to migrate all data files."""
    data_dir = "receipt_dynamo/data"

    # First, do a dry run to show what would be changed
    print("=== DRY RUN - Showing potential migrations ===\n")

    migrations = []
    for file in sorted(os.listdir(data_dir)):
        if file.startswith("_") and file.endswith(".py"):
            file_path = os.path.join(data_dir, file)
            result = migrate_file(file_path, dry_run=True)
            if result and "→" in result:
                migrations.append((file_path, result))
                print(f"{file}: {result}")

    if not migrations:
        print("No files need migration.")
        return

    print(f"\n\nFound {len(migrations)} files that can be migrated.")
    response = input("\nProceed with migration? (y/N): ")

    if response.lower() == "y":
        print("\n=== Migrating files ===\n")
        for file_path, _ in migrations:
            result = migrate_file(file_path, dry_run=False)
            print(f"Migrated: {result}")
        print(f"\n✅ Migration complete! Updated {len(migrations)} files.")
        print("\n⚠️  Remember to:")
        print("1. Run tests to ensure everything still works")
        print("2. Update any imports in other files if needed")
        print("3. Run pylint to verify the warnings are resolved")
    else:
        print("\nMigration cancelled.")


if __name__ == "__main__":
    main()

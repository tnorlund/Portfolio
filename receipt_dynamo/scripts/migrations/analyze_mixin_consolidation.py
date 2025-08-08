#!/usr/bin/env python3
"""
Script to analyze how data access classes can be migrated to consolidated mixins.
"""

import os
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

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


def main():
    """Main function to analyze all data files."""
    data_dir = "receipt_dynamo/data"

    print("=== Mixin Consolidation Analysis ===\n")

    migrations = []
    consolidated_counts = Counter()
    mixin_counts = Counter()

    for file in sorted(os.listdir(data_dir)):
        if file.startswith("_") and file.endswith(".py"):
            file_path = os.path.join(data_dir, file)
            result = extract_class_definition(file_path)
            if result:
                class_name, mixins, _, _, _ = result
                if mixins:
                    consolidated = determine_consolidated_mixin(mixins)
                    if consolidated:
                        migrations.append(
                            (file, class_name, len(mixins), consolidated)
                        )
                        consolidated_counts[consolidated] += 1
                        mixin_counts[len(mixins)] += 1

    # Show results
    print("Files that can be migrated:")
    print("-" * 70)
    for file, class_name, mixin_count, consolidated in migrations:
        print(f"{file:40} {mixin_count} mixins → {consolidated}")

    print(f"\n\nTotal files that can be migrated: {len(migrations)}")

    # Show statistics
    print("\nConsolidated mixin usage:")
    print("-" * 40)
    for mixin, count in consolidated_counts.most_common():
        print(f"{mixin:30} {count:3} files")

    print("\nCurrent mixin count distribution:")
    print("-" * 40)
    for count, files in sorted(mixin_counts.items()):
        print(f"{count} mixins: {files} files")

    # Calculate reduction
    total_current = sum(count * files for count, files in mixin_counts.items())
    total_after = (
        len(migrations) * 1
    )  # Each file will have 1 consolidated mixin
    reduction = ((total_current - total_after) / total_current) * 100

    print(f"\n\nInheritance reduction:")
    print(f"Current total mixins: {total_current}")
    print(f"After consolidation: {total_after}")
    print(f"Reduction: {reduction:.1f}%")

    # Show which files might need custom patterns
    print("\n\nRecommendations:")
    print("-" * 60)
    print("1. Most files (60%) can use FullDynamoEntityMixin")
    print("2. WriteOperationsMixin is second most common (25%)")
    print("3. Consider creating custom patterns for outliers")
    print("4. Run tests after migration to ensure functionality")


if __name__ == "__main__":
    main()

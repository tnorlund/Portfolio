#!/usr/bin/env python3
"""Add missing @handle_dynamodb_errors decorators to receipt modules."""

import os
import re
from typing import List, Tuple


def find_methods_needing_decorators(
    content: str,
) -> List[Tuple[int, str, str]]:
    """Find methods that need decorators.

    Returns list of (line_number, method_name, operation_name) tuples.
    """
    lines = content.split("\n")
    methods_to_decorate = []

    for i, line in enumerate(lines):
        # Match method definitions
        method_match = re.match(
            r"^\s{4}def (add|update|delete|get|list)_(\w+)\(", line
        )
        if method_match:
            # Check if previous line has decorator
            if i > 0 and "@handle_dynamodb_errors" not in lines[i - 1]:
                method_name = method_match.group(0).strip()[
                    4:-1
                ]  # Extract method name
                operation_name = method_name
                methods_to_decorate.append((i, method_name, operation_name))

    return methods_to_decorate


def add_decorators_to_content(
    content: str, methods: List[Tuple[int, str, str]]
) -> str:
    """Add decorators to content."""
    lines = content.split("\n")

    # Sort by line number in reverse to avoid offset issues
    for line_num, method_name, operation_name in sorted(methods, reverse=True):
        # Find the indentation of the method
        indent = len(lines[line_num]) - len(lines[line_num].lstrip())
        decorator = (
            " " * indent + f'@handle_dynamodb_errors("{operation_name}")'
        )
        lines.insert(line_num, decorator)

    return "\n".join(lines)


def ensure_imports(content: str) -> str:
    """Ensure handle_dynamodb_errors is imported."""
    if "handle_dynamodb_errors" not in content:
        # Find the imports section
        lines = content.split("\n")
        import_added = False

        for i, line in enumerate(lines):
            # Add after other imports from base_operations
            if "from receipt_dynamo.data.base_operations import" in line:
                # Check if it's a multi-line import
                j = i
                while j < len(lines) and not lines[j].rstrip().endswith(")"):
                    j += 1

                if "(" in line:  # Multi-line import
                    # Insert before the closing parenthesis
                    lines[j] = lines[j].replace(
                        ")", ",\n    handle_dynamodb_errors,\n)"
                    )
                else:  # Single line import
                    lines[i] = line.rstrip() + ", handle_dynamodb_errors"

                import_added = True
                break

        if not import_added:
            # Add as a new import if no base_operations import exists
            for i, line in enumerate(lines):
                if line.startswith("from receipt_dynamo.data"):
                    lines.insert(
                        i + 1,
                        "from receipt_dynamo.data.base_operations import handle_dynamodb_errors",
                    )
                    break

        content = "\n".join(lines)

    return content


def process_file(filepath: str) -> int:
    """Process a single file and return number of decorators added."""
    print(f"\nProcessing {filepath}...")

    with open(filepath, "r") as f:
        content = f.read()

    # Find methods needing decorators
    methods = find_methods_needing_decorators(content)

    if not methods:
        print(f"  No missing decorators found")
        return 0

    print(f"  Found {len(methods)} methods needing decorators:")
    for _, method_name, _ in methods:
        print(f"    - {method_name}")

    # Add decorators
    content = add_decorators_to_content(content, methods)

    # Ensure imports
    content = ensure_imports(content)

    # Write back
    with open(filepath, "w") as f:
        f.write(content)

    print(f"  Added {len(methods)} decorators")
    return len(methods)


def main():
    """Main function."""
    receipt_files = [
        "receipt_dynamo/data/_receipt.py",
        "receipt_dynamo/data/_receipt_chatgpt_validation.py",
        "receipt_dynamo/data/_receipt_field.py",
        "receipt_dynamo/data/_receipt_label_analysis.py",
        "receipt_dynamo/data/_receipt_letter.py",
        "receipt_dynamo/data/_receipt_line_item_analysis.py",
        "receipt_dynamo/data/_receipt_line.py",
        "receipt_dynamo/data/_receipt_metadata.py",
        "receipt_dynamo/data/_receipt_section.py",
        "receipt_dynamo/data/_receipt_structure_analysis.py",
        "receipt_dynamo/data/_receipt_validation_category.py",
        "receipt_dynamo/data/_receipt_validation_result.py",
        "receipt_dynamo/data/_receipt_validation_summary.py",
        "receipt_dynamo/data/_receipt_word_label.py",
        "receipt_dynamo/data/_receipt_word.py",
    ]

    total_added = 0
    for filepath in receipt_files:
        if os.path.exists(filepath):
            total_added += process_file(filepath)
        else:
            print(f"\nSkipping {filepath} - file not found")

    print(f"\n\nTotal decorators added: {total_added}")


if __name__ == "__main__":
    main()

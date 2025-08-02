#!/usr/bin/env python3
"""
Update job-related files to use shared DynamoDB utilities.
"""

import os

# Files that have duplicate _parse_dynamodb_map functions
FILES_TO_UPDATE = [
    "receipt_dynamo/entities/job.py",
    "receipt_dynamo/entities/instance_job.py",
    "receipt_dynamo/entities/job_metric.py",
    "receipt_dynamo/entities/job_resource.py",
    "receipt_dynamo/entities/job_checkpoint.py",
]

# Import to add at the top
IMPORT_STATEMENT = """from receipt_dynamo.entities.dynamodb_utils import (
    parse_dynamodb_map,
    parse_dynamodb_value,
    dict_to_dynamodb_map,
    to_dynamodb_value,
)
"""


def update_file(filepath):
    """Update a single file to use shared utilities."""
    with open(filepath, "r") as f:
        content = f.read()

    # Skip if already updated
    if "from receipt_dynamo.entities.dynamodb_utils import" in content:
        print(f"✓ {filepath} already updated")
        return

    # Find where to insert import (after other imports)
    lines = content.split("\n")
    import_index = 0
    for i, line in enumerate(lines):
        if line.startswith("from") or line.startswith("import"):
            import_index = i + 1
        elif import_index > 0 and line and not line.startswith(" "):
            # Found first non-import line
            break

    # Insert import
    lines.insert(import_index, IMPORT_STATEMENT)

    # Join back
    content = "\n".join(lines)

    # Remove the duplicate function definitions
    # Remove _parse_dynamodb_map
    start = content.find("def _parse_dynamodb_map(")
    if start != -1:
        # Find the end of the function (next def or class at same indent level)
        end = content.find("\ndef ", start + 1)
        if end == -1:
            end = content.find("\nclass ", start + 1)
        if end != -1:
            content = content[:start] + content[end + 1 :]

    # Remove _parse_dynamodb_value
    start = content.find("def _parse_dynamodb_value(")
    if start != -1:
        end = content.find("\ndef ", start + 1)
        if end == -1:
            end = content.find("\nclass ", start + 1)
        if end != -1:
            content = content[:start] + content[end + 1 :]

    # Update method calls from self._parse_dynamodb_map to parse_dynamodb_map
    content = content.replace("_parse_dynamodb_map(", "parse_dynamodb_map(")
    content = content.replace(
        "_parse_dynamodb_value(", "parse_dynamodb_value("
    )

    # For Job class methods that have self parameter
    # Remove _dict_to_dynamodb_map
    start = content.find("def _dict_to_dynamodb_map(self,")
    if start != -1:
        end = content.find("\n    def ", start + 1)
        if end == -1:
            end = content.find("\nclass ", start + 1)
        if end != -1:
            content = content[:start] + content[end + 1 :]

    # Remove _to_dynamodb_value
    start = content.find("def _to_dynamodb_value(self,")
    if start != -1:
        end = content.find("\n    def ", start + 1)
        if end == -1:
            end = content.find("\nclass ", start + 1)
        if end != -1:
            content = content[:start] + content[end + 1 :]

    # Update method calls
    content = content.replace(
        "self._dict_to_dynamodb_map(", "dict_to_dynamodb_map("
    )
    content = content.replace("self._to_dynamodb_value(", "to_dynamodb_value(")

    # Write back
    with open(filepath, "w") as f:
        f.write(content)

    print(f"✓ Updated {filepath}")


def main():
    print("=== Updating Job Files to Use Shared DynamoDB Utilities ===\n")

    for filepath in FILES_TO_UPDATE:
        if os.path.exists(filepath):
            update_file(filepath)
        else:
            print(f"✗ File not found: {filepath}")

    print("\nDone! Run pylint to check the improvement.")


if __name__ == "__main__":
    main()

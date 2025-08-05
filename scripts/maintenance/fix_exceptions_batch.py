#!/usr/bin/env python3
"""Batch fix exceptions in receipt_dynamo"""

import os
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Exception mapping based on error messages
EXCEPTION_MAPPINGS = {
    # Job-specific
    r"Job with ID .* does not exist": "EntityNotFoundError",
    r"Job with ID .* already exists": "EntityAlreadyExistsError",
    r"job.* does not exist": "EntityNotFoundError",
    r"jobs.* do not exist": "EntityNotFoundError",
    # DynamoDB errors
    r"Provisioned throughput exceeded": "DynamoDBThroughputError",
    r"Internal server error": "DynamoDBServerError",
    r"Access denied": "DynamoDBAccessError",
    r"Resource.*not found": "DynamoDBResourceNotFoundError",
    r"parameters.*invalid": "DynamoDBValidationError",
    r"Could not.*DynamoDB": "DynamoDBError",
    r"Could not.*database": "DynamoDBError",
    r"Error.*from.*database": "DynamoDBError",
    # General entity errors
    r"does not exist": "EntityNotFoundError",
    r"already exists": "EntityAlreadyExistsError",
    r"Error.*batch": "BatchOperationError",
    r"Error.*transaction": "TransactionError",
    # Default
    r"Error": "OperationError",
}


def determine_exception_type(message: str) -> str:
    """Determine the appropriate exception type based on the message"""
    for pattern, exc_type in EXCEPTION_MAPPINGS.items():
        if re.search(pattern, message, re.IGNORECASE):
            return exc_type
    return "ReceiptDynamoError"  # Default


def fix_file(filepath: Path) -> Tuple[int, int]:
    """Fix exceptions in a single file"""
    with open(filepath, "r") as f:
        content = f.read()

    lines = content.split("\n")
    new_lines = []
    in_except_block = False
    except_var = None
    changes = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        # Track except blocks
        except_match = re.match(r"(\s*)except .* as (\w+):", line)
        if except_match:
            in_except_block = True
            except_var = except_match.group(2)
            new_lines.append(line)
            i += 1
            continue

        # End of except block
        if (
            in_except_block
            and line
            and not line[0].isspace()
            and not line.strip().startswith("except")
        ):
            in_except_block = False
            except_var = None

        # Fix raise statements
        if "raise" in line and "Exception" in line:
            # Handle multi-line raise statements
            full_statement = line
            j = i + 1
            while j < len(lines) and (
                lines[j].strip() == "" or lines[j].startswith(" " * 16)
            ):
                if lines[j].strip():
                    full_statement += "\n" + lines[j]
                j += 1

            # Check if it's a broad exception
            exception_match = re.match(
                r"(\s*)raise Exception\((.*?)\)(?:\s*from\s+(\w+))?",
                full_statement,
                re.DOTALL,
            )
            if exception_match:
                indent = exception_match.group(1)
                message = exception_match.group(2).strip()
                from_var = exception_match.group(3)

                # Determine the right exception type
                exc_type = determine_exception_type(message)

                # Add 'from e' if in except block and not present
                if in_except_block and except_var and not from_var:
                    new_line = f"{indent}raise {exc_type}({message}) from {except_var}"
                elif from_var:
                    new_line = (
                        f"{indent}raise {exc_type}({message}) from {from_var}"
                    )
                else:
                    new_line = f"{indent}raise {exc_type}({message})"

                new_lines.append(new_line)
                changes += 1
                i = j  # Skip the lines we've already processed
                continue

        # Fix ValueError statements that should have 'from e'
        if (
            in_except_block
            and except_var
            and "raise ValueError" in line
            and "from" not in line
        ):
            # Don't add 'from e' to ValueError for parameter validation
            if any(
                keyword in line
                for keyword in ["parameter", "must be", "required"]
            ):
                new_lines.append(line)
            else:
                # This ValueError is likely related to the caught exception
                new_line = re.sub(
                    r"(\s*raise ValueError\(.*?\))$",
                    rf"\1 from {except_var}",
                    line,
                )
                new_lines.append(new_line)
                changes += 1
                i += 1
                continue

        new_lines.append(line)
        i += 1

    # Check if we need to add imports
    new_content = "\n".join(new_lines)
    if changes > 0:
        # Find which exceptions are used
        used_exceptions = set()
        for exc_type in EXCEPTION_MAPPINGS.values():
            if exc_type in new_content and exc_type not in [
                "ValueError",
                "Exception",
            ]:
                used_exceptions.add(exc_type)

        # Add imports if needed
        if (
            used_exceptions
            and "from receipt_dynamo.data.shared_exceptions" not in new_content
        ):
            import_idx = 0
            for i, line in enumerate(new_lines):
                if line.startswith("from receipt_dynamo"):
                    import_idx = i + 1
                elif line.startswith("import") or line.startswith("from"):
                    import_idx = i + 1

            # Check if we already have some imports from shared_exceptions
            existing_import_idx = None
            for i, line in enumerate(new_lines):
                if "from receipt_dynamo.data.shared_exceptions import" in line:
                    existing_import_idx = i
                    break

            if existing_import_idx:
                # Update existing import
                current_imports = []
                if "(" in new_lines[existing_import_idx]:
                    # Multi-line import
                    j = existing_import_idx
                    while ")" not in new_lines[j]:
                        j += 1
                    # Extract all imported names
                    import_text = "\n".join(
                        new_lines[existing_import_idx : j + 1]
                    )
                    current_imports = re.findall(
                        r"(\w+)(?:,|\s*\))", import_text
                    )
                else:
                    # Single line import
                    import_match = re.search(
                        r"import\s+(.+)$", new_lines[existing_import_idx]
                    )
                    if import_match:
                        current_imports = [
                            x.strip() for x in import_match.group(1).split(",")
                        ]

                # Add new exceptions
                all_imports = sorted(set(current_imports) | used_exceptions)

                if len(all_imports) > 3:
                    # Multi-line format
                    import_lines = [
                        "from receipt_dynamo.data.shared_exceptions import ("
                    ]
                    for imp in all_imports:
                        import_lines.append(f"    {imp},")
                    import_lines.append(")")

                    # Replace old import
                    if "(" in new_lines[existing_import_idx]:
                        # Remove old multi-line import
                        j = existing_import_idx
                        while ")" not in new_lines[j]:
                            j += 1
                        new_lines[existing_import_idx : j + 1] = import_lines
                    else:
                        new_lines[
                            existing_import_idx : existing_import_idx + 1
                        ] = import_lines
                else:
                    # Single line format
                    new_lines[existing_import_idx] = (
                        f"from receipt_dynamo.data.shared_exceptions import {', '.join(all_imports)}"
                    )
            else:
                # Add new import
                if len(used_exceptions) > 3:
                    import_lines = [
                        "from receipt_dynamo.data.shared_exceptions import ("
                    ]
                    for exc in sorted(used_exceptions):
                        import_lines.append(f"    {exc},")
                    import_lines.append(")")
                    new_lines[import_idx:import_idx] = import_lines
                else:
                    import_line = f"from receipt_dynamo.data.shared_exceptions import {', '.join(sorted(used_exceptions))}"
                    new_lines.insert(import_idx, import_line)

    # Write back if changed
    final_content = "\n".join(new_lines)
    if final_content != content:
        with open(filepath, "w") as f:
            f.write(final_content)

    return changes, changes


def main():
    """Fix exceptions in all data layer files"""
    data_dir = Path(
        "/Users/tnorlund/GitHub/example/receipt_dynamo/receipt_dynamo/data"
    )

    total_fixes = 0
    files_fixed = 0

    for filepath in data_dir.glob("*.py"):
        if (
            filepath.name == "__init__.py"
            or filepath.name == "shared_exceptions.py"
        ):
            continue

        fixes, _ = fix_file(filepath)
        if fixes > 0:
            print(f"Fixed {filepath.name}: {fixes} exceptions")
            total_fixes += fixes
            files_fixed += 1

    print(f"\nTotal: Fixed {total_fixes} exceptions in {files_fixed} files")


if __name__ == "__main__":
    main()

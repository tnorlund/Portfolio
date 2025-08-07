#!/usr/bin/env python3
"""Automated exception handling fixer for Phase 4"""

import os
import re
from pathlib import Path
from typing import List, Tuple

# Map generic exceptions to specific ones based on context
EXCEPTION_MAPPINGS = {
    # DynamoDB specific exceptions
    r"Could not .* (?:to|from|in) (?:the )?(?:database|DynamoDB)": "DynamoDBError",
    r"Provisioned throughput exceeded": "DynamoDBThroughputError",
    r"Internal server error": "DynamoDBServerError",
    r"Access denied": "DynamoDBAccessError",
    r"Resource.* not found": "DynamoDBResourceNotFoundError",
    r"One or more parameters.*invalid": "DynamoDBValidationError",
    # Job related exceptions
    r"Job .* does not exist": "JobNotFoundError",
    r"Job .* already exists": "JobAlreadyExistsError",
    r"Error.*job": "JobError",
    # General exceptions
    r".*validation.*": "ValidationError",
    r".*parameter.*": "ParameterError",
    r".*not found.*": "NotFoundError",
}


def create_custom_exceptions() -> str:
    """Generate custom exception classes"""
    return '''"""Custom exceptions for receipt_dynamo"""

class ReceiptDynamoError(Exception):
    """Base exception for all receipt_dynamo errors"""
    pass

# DynamoDB specific exceptions
class DynamoDBError(ReceiptDynamoError):
    """Base exception for DynamoDB operations"""
    pass

class DynamoDBThroughputError(DynamoDBError):
    """Raised when DynamoDB throughput is exceeded"""
    pass

class DynamoDBServerError(DynamoDBError):
    """Raised when DynamoDB has an internal server error"""
    pass

class DynamoDBAccessError(DynamoDBError):
    """Raised when access to DynamoDB is denied"""
    pass

class DynamoDBResourceNotFoundError(DynamoDBError):
    """Raised when a DynamoDB resource is not found"""
    pass

class DynamoDBValidationError(DynamoDBError):
    """Raised when DynamoDB validation fails"""
    pass

# Job specific exceptions
class JobError(ReceiptDynamoError):
    """Base exception for job operations"""
    pass

class JobNotFoundError(JobError):
    """Raised when a job is not found"""
    pass

class JobAlreadyExistsError(JobError):
    """Raised when attempting to create a job that already exists"""
    pass

# General exceptions
class ValidationError(ReceiptDynamoError):
    """Raised when validation fails"""
    pass

class ParameterError(ReceiptDynamoError):
    """Raised when invalid parameters are provided"""
    pass

class NotFoundError(ReceiptDynamoError):
    """Raised when a resource is not found"""
    pass
'''


def fix_broad_exceptions(content: str) -> Tuple[str, int]:
    """Fix W0719: Replace broad Exception with specific ones"""
    changes = 0
    lines = content.split("\n")
    new_lines = []

    for i, line in enumerate(lines):
        # Match raise Exception patterns
        match = re.match(r"(\s*)raise Exception\((.*)\)(.*)$", line)
        if match:
            indent, message, rest = match.groups()

            # Determine appropriate exception type
            exception_type = "ReceiptDynamoError"  # Default

            for pattern, exc_type in EXCEPTION_MAPPINGS.items():
                if re.search(pattern, message, re.IGNORECASE):
                    exception_type = exc_type
                    break

            # Check if this is part of an except block that needs 'from e'
            needs_from = False
            for j in range(max(0, i - 10), i):
                if re.match(r"\s*except .* as e:", lines[j]):
                    needs_from = True
                    break

            if needs_from and not rest.strip().startswith("from"):
                new_line = f"{indent}raise {exception_type}({message}) from e"
            else:
                new_line = f"{indent}raise {exception_type}({message}){rest}"

            new_lines.append(new_line)
            changes += 1
        else:
            new_lines.append(line)

    return "\n".join(new_lines), changes


def fix_missing_from(content: str) -> Tuple[str, int]:
    """Fix W0707: Add 'from e' to exception chains"""
    changes = 0
    lines = content.split("\n")
    new_lines = []
    in_except_block = False
    exception_var = None

    for line in lines:
        # Detect except blocks
        except_match = re.match(r"(\s*)except .* as (\w+):", line)
        if except_match:
            in_except_block = True
            exception_var = except_match.group(2)
            new_lines.append(line)
            continue

        # Reset when leaving except block
        if in_except_block and line and not line[0].isspace():
            in_except_block = False
            exception_var = None

        # Fix raise statements in except blocks
        if in_except_block and exception_var:
            raise_match = re.match(
                r"(\s*)raise (\w+Error)\((.*)\)(?! from)", line
            )
            if raise_match and "ValueError" not in raise_match.group(2):
                indent, exc_type, message = raise_match.groups()
                new_line = (
                    f"{indent}raise {exc_type}({message}) from {exception_var}"
                )
                new_lines.append(new_line)
                changes += 1
                continue

        new_lines.append(line)

    return "\n".join(new_lines), changes


def add_custom_imports(content: str) -> str:
    """Add imports for custom exceptions"""
    lines = content.split("\n")

    # Find the last import line
    last_import_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            last_import_idx = i

    # Check which custom exceptions are used
    used_exceptions = set()
    for exc_type in EXCEPTION_MAPPINGS.values():
        if exc_type in content and exc_type != "ValueError":
            used_exceptions.add(exc_type)

    if used_exceptions:
        import_line = f"from receipt_dynamo.data.custom_exceptions import {', '.join(sorted(used_exceptions))}"
        lines.insert(last_import_idx + 1, import_line)

    return "\n".join(lines)


def process_file(filepath: Path) -> Tuple[int, int]:
    """Process a single file and fix exceptions"""
    with open(filepath, "r") as f:
        content = f.read()

    original_content = content

    # Fix broad exceptions
    content, broad_fixes = fix_broad_exceptions(content)

    # Fix missing from
    content, from_fixes = fix_missing_from(content)

    # Add imports if needed
    if broad_fixes > 0:
        content = add_custom_imports(content)

    # Write back if changed
    if content != original_content:
        with open(filepath, "w") as f:
            f.write(content)

    return broad_fixes, from_fixes


def main():
    """Run the exception fixer"""
    receipt_dynamo_path = Path("/Users/tnorlund/GitHub/example/receipt_dynamo")

    # First, create custom exceptions file
    exceptions_file = (
        receipt_dynamo_path / "receipt_dynamo/data/custom_exceptions.py"
    )
    with open(exceptions_file, "w") as f:
        f.write(create_custom_exceptions())
    print(f"Created {exceptions_file}")

    # Process all Python files
    total_broad = 0
    total_from = 0

    for filepath in receipt_dynamo_path.rglob("*.py"):
        if "__pycache__" in str(filepath) or "test" in str(filepath):
            continue

        broad, from_fixes = process_file(filepath)
        if broad > 0 or from_fixes > 0:
            print(
                f"Fixed {filepath.relative_to(receipt_dynamo_path)}: "
                f"{broad} broad exceptions, {from_fixes} missing 'from'"
            )

        total_broad += broad
        total_from += from_fixes

    print(
        f"\nTotal fixes: {total_broad} broad exceptions, {total_from} missing 'from'"
    )


if __name__ == "__main__":
    main()

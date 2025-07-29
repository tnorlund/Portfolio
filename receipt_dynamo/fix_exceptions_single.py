#!/usr/bin/env python3
"""
Script to fix exception inconsistencies in a single receipt_dynamo data file.
Usage: python fix_exceptions_single.py <filename>
"""

import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Exception mapping rules
EXCEPTION_MAPPINGS = {
    "ValueError": {
        "parameter_validation": "EntityValidationError",
        "not_found": "EntityNotFoundError",
        "invalid_uuid": "EntityValidationError",
        "default": "EntityValidationError",
    },
    "RuntimeError": {
        "throughput": "DynamoDBThroughputError",
        "server_error": "DynamoDBServerError",
        "access_denied": "DynamoDBAccessError",
        "default": "OperationError",
    },
}

# Patterns to identify the type of error
ERROR_PATTERNS = {
    "not_found": [
        r"does not exist",
        r"not found",
        r"No .* found",
        r"cannot find",
    ],
    "parameter_validation": [
        r"must be",
        r"cannot be None",
        r"must contain",
        r"Invalid",
        r"required",
        r"must have",
        r"greater than",
        r"less than",
        r"positive integer",
        r"non-empty",
    ],
    "throughput": [
        r"[Tt]hroughput exceeded",
        r"ProvisionedThroughputExceededException",
    ],
    "server_error": [
        r"[Ii]nternal [Ss]erver [Ee]rror",
        r"InternalServerError",
    ],
    "access_denied": [r"[Aa]ccess [Dd]enied", r"AccessDeniedException"],
}


def analyze_file(filepath: Path) -> Dict:
    """Analyze a file and report what needs to be fixed."""
    with open(filepath, "r") as f:
        content = f.read()

    results = {
        "ValueError": [],
        "RuntimeError": [],
        "imports_needed": set(),
        "has_shared_exceptions_import": False,
    }

    # Check existing imports
    if "from receipt_dynamo.data.shared_exceptions import" in content:
        results["has_shared_exceptions_import"] = True

    # Find all ValueError instances
    for match in re.finditer(r"raise ValueError\((.*?)\)", content, re.DOTALL):
        line_num = content[: match.start()].count("\n") + 1
        error_msg = match.group(1).strip()
        error_type = identify_error_type(error_msg)

        if error_type == "not_found":
            replacement = "EntityNotFoundError"
            results["imports_needed"].add("EntityNotFoundError")
        else:
            replacement = "EntityValidationError"
            results["imports_needed"].add("EntityValidationError")

        results["ValueError"].append(
            {
                "line": line_num,
                "message": (
                    error_msg[:50] + "..."
                    if len(error_msg) > 50
                    else error_msg
                ),
                "type": error_type,
                "replacement": replacement,
            }
        )

    # Find all RuntimeError instances
    for match in re.finditer(
        r"raise RuntimeError\((.*?)\)", content, re.DOTALL
    ):
        line_num = content[: match.start()].count("\n") + 1
        error_msg = match.group(1).strip()
        error_type = identify_error_type(error_msg)

        if error_type == "throughput":
            replacement = "DynamoDBThroughputError"
            results["imports_needed"].add("DynamoDBThroughputError")
        elif error_type == "server_error":
            replacement = "DynamoDBServerError"
            results["imports_needed"].add("DynamoDBServerError")
        elif error_type == "access_denied":
            replacement = "DynamoDBAccessError"
            results["imports_needed"].add("DynamoDBAccessError")
        else:
            replacement = "OperationError"
            results["imports_needed"].add("OperationError")

        results["RuntimeError"].append(
            {
                "line": line_num,
                "message": (
                    error_msg[:50] + "..."
                    if len(error_msg) > 50
                    else error_msg
                ),
                "type": error_type,
                "replacement": replacement,
            }
        )

    return results


def identify_error_type(error_message: str) -> str:
    """Identify the type of error based on the error message."""
    for error_type, patterns in ERROR_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, error_message, re.IGNORECASE):
                return error_type
    return "default"


def print_analysis(filepath: Path, results: Dict):
    """Print analysis results."""
    print(f"\nAnalysis for {filepath.name}")
    print("=" * 60)

    if not results["ValueError"] and not results["RuntimeError"]:
        print("✅ No inconsistent exceptions found!")
        return

    print(f"\n📊 Summary:")
    print(f"  - ValueError instances: {len(results['ValueError'])}")
    print(f"  - RuntimeError instances: {len(results['RuntimeError'])}")
    print(
        f"  - Has shared_exceptions import: {'Yes' if results['has_shared_exceptions_import'] else 'No'}"
    )

    if results["ValueError"]:
        print(f"\n🔍 ValueError instances to fix:")
        for item in results["ValueError"]:
            print(f"  Line {item['line']:4d}: {item['message']}")
            print(f"           → Replace with: {item['replacement']}")

    if results["RuntimeError"]:
        print(f"\n🔍 RuntimeError instances to fix:")
        for item in results["RuntimeError"]:
            print(f"  Line {item['line']:4d}: {item['message']}")
            print(f"           → Replace with: {item['replacement']}")

    if results["imports_needed"]:
        print(f"\n📦 Required imports:")
        for imp in sorted(results["imports_needed"]):
            print(f"  - {imp}")


def update_tracker(filepath: Path, fixed: bool = False):
    """Update the exception tracker file."""
    tracker_path = filepath.parent.parent / "EXCEPTION_CONSISTENCY_TRACKER.md"

    if not tracker_path.exists():
        return

    with open(tracker_path, "r") as f:
        content = f.read()

    filename = filepath.name

    # Update the checkbox
    if fixed:
        content = content.replace(f"- [ ] `{filename}`", f"- [x] `{filename}`")

    with open(tracker_path, "w") as f:
        f.write(content)


def main():
    if len(sys.argv) != 2:
        print("Usage: python fix_exceptions_single.py <filename>")
        print("Example: python fix_exceptions_single.py _job.py")
        sys.exit(1)

    filename = sys.argv[1]
    data_dir = Path(__file__).parent / "receipt_dynamo" / "data"
    filepath = data_dir / filename

    if not filepath.exists():
        print(f"Error: File {filepath} not found")
        sys.exit(1)

    # Analyze the file
    results = analyze_file(filepath)
    print_analysis(filepath, results)

    if results["ValueError"] or results["RuntimeError"]:
        response = input("\n🔧 Do you want to apply these fixes? (y/n): ")
        if response.lower() == "y":
            # Apply fixes
            from fix_exceptions import process_file

            stats = process_file(filepath)
            print(
                f"\n✅ Fixed {stats['ValueError']} ValueError and {stats['RuntimeError']} RuntimeError"
            )
            update_tracker(filepath, fixed=True)
        else:
            print("❌ No changes made")
    else:
        update_tracker(filepath, fixed=True)


if __name__ == "__main__":
    main()

"""Test to ensure all Lambda functions use ARM64 architecture."""

import os
import re
from pathlib import Path

import pytest


def find_lambda_definitions(root_dir: Path) -> list[tuple[Path, int, str]]:
    """Find all Lambda function definitions in the infra directory.

    Returns:
        List of tuples containing (file_path, line_number, function_definition)
    """
    lambda_definitions = []

    # Pattern to match Lambda function definitions
    lambda_pattern = re.compile(
        r"aws\.lambda_\.Function\s*\([^)]+\)", re.DOTALL | re.MULTILINE
    )

    # Search all Python files in infra directory
    for py_file in root_dir.rglob("*.py"):
        # Skip test files and __pycache__
        if "test" in py_file.name or "__pycache__" in str(py_file):
            continue

        try:
            content = py_file.read_text()

            # Find all Lambda function definitions
            for match in lambda_pattern.finditer(content):
                # Get line number of the match
                line_no = content[: match.start()].count("\n") + 1
                lambda_definitions.append((py_file, line_no, match.group(0)))

        except Exception as e:
            print(f"Error reading {py_file}: {e}")

    return lambda_definitions


def test_all_lambdas_use_arm64():
    """Ensure all Lambda functions specify ARM64 architecture."""
    # Get the infra directory
    infra_dir = Path(__file__).parent.parent

    # Find all Lambda definitions
    lambda_definitions = find_lambda_definitions(infra_dir)

    # Check each Lambda definition
    missing_arm64 = []

    for file_path, line_no, definition in lambda_definitions:
        if 'architectures=["arm64"]' not in definition:
            # Extract function name for better error reporting
            name_match = re.search(
                r'Function\s*\(\s*["\']([^"\']+)', definition
            )
            func_name = name_match.group(1) if name_match else "unknown"

            missing_arm64.append(
                {
                    "file": str(file_path.relative_to(infra_dir)),
                    "line": line_no,
                    "function": func_name,
                }
            )

    # Report any Lambda functions missing ARM64 architecture
    if missing_arm64:
        error_msg = (
            "The following Lambda functions are missing ARM64 architecture:\n"
        )
        for item in missing_arm64:
            error_msg += (
                f"  - {item['file']}:{item['line']} - {item['function']}\n"
            )
        error_msg += (
            "\nAdd 'architectures=[\"arm64\"]' to each function definition."
        )

        pytest.fail(error_msg)


def test_all_lambdas_use_consistent_runtime():
    """Ensure all Lambda functions use consistent Python runtime."""
    # Get the infra directory
    infra_dir = Path(__file__).parent.parent

    # Find all Lambda definitions
    lambda_definitions = find_lambda_definitions(infra_dir)

    # Extract runtime versions
    runtime_usage = {}

    for file_path, line_no, definition in lambda_definitions:
        runtime_match = re.search(r'runtime\s*=\s*["\']([^"\']+)', definition)
        if runtime_match:
            runtime = runtime_match.group(1)

            # Extract function name
            name_match = re.search(
                r'Function\s*\(\s*["\']([^"\']+)', definition
            )
            func_name = name_match.group(1) if name_match else "unknown"

            if runtime not in runtime_usage:
                runtime_usage[runtime] = []

            runtime_usage[runtime].append(
                {
                    "file": str(file_path.relative_to(infra_dir)),
                    "line": line_no,
                    "function": func_name,
                }
            )

    # Check if all functions use the same runtime
    if len(runtime_usage) > 1:
        error_msg = (
            "Lambda functions are using inconsistent Python runtimes:\n"
        )
        for runtime, functions in runtime_usage.items():
            error_msg += f"\n{runtime}:\n"
            for func in functions:
                error_msg += (
                    f"  - {func['file']}:{func['line']} - {func['function']}\n"
                )
        error_msg += "\nStandardize all functions to use Python 3.12 runtime."

        pytest.fail(error_msg)

    # Also check that we're using a supported runtime
    if runtime_usage:
        runtime = list(runtime_usage.keys())[0]
        if runtime != "python3.12":
            pytest.fail(
                f"Lambda functions are using {runtime}. Use python3.12 for consistency across the project."
            )


if __name__ == "__main__":
    # Allow running directly for debugging
    test_all_lambdas_use_arm64()
    test_all_lambdas_use_consistent_runtime()
    print("All Lambda architecture tests passed!")

#!/usr/bin/env python3
"""
Optimized test runner for parallel execution with intelligent resource management.
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


def get_optimal_worker_count() -> int:
    """Determine optimal number of pytest-xdist workers based on system resources."""
    if HAS_PSUTIL:
        cpu_count = psutil.cpu_count(logical=True)
        memory_gb = psutil.virtual_memory().total / (1024**3)

        # Conservative scaling: use fewer workers for memory-intensive tests
        if memory_gb < 4:
            workers = max(1, cpu_count // 2)
        elif memory_gb < 8:
            workers = max(2, cpu_count - 1)
        else:
            workers = cpu_count
    else:
        # Fallback: use os.cpu_count() and conservative estimates
        cpu_count = os.cpu_count() or 2

        # Conservative approach without memory info
        if cpu_count <= 2:
            workers = 1
        elif cpu_count <= 4:
            workers = 2
        else:
            workers = cpu_count - 1

    # Cap at 4 for integration tests to avoid overwhelming DynamoDB local
    return min(workers, 4)


def run_tests(
    package: str,
    test_paths: List[str],
    test_type: str = "unit",
    verbose: bool = False,
    coverage: bool = False,
    fail_fast: bool = False,
    timeout: int = 600,
) -> bool:
    """
    Run tests with optimal configuration.

    Args:
        package: Package name (e.g., 'receipt_dynamo')
        test_paths: List of test paths or files
        test_type: Type of tests ('unit', 'integration', 'end_to_end')
        verbose: Enable verbose output
        coverage: Enable coverage reporting
        fail_fast: Stop on first failure
        timeout: Per-test timeout in seconds

    Returns:
        True if tests passed, False otherwise
    """

    # Change to package directory
    os.chdir(package)

    # Build pytest command
    cmd = ["python", "-m", "pytest"]

    # Add test paths
    cmd.extend(test_paths)

    # Optimize for test type - use auto parallelization for all except e2e
    if test_type == "end_to_end":
        # End-to-end tests: sequential execution (real AWS resources)
        cmd.extend(["--timeout", str(timeout * 2)])
    else:
        # All other tests: use pytest-xdist auto parallelization
        # This lets pytest-xdist determine optimal worker count and load balancing
        cmd.extend(["-n", "auto"])
        cmd.extend(["--timeout", str(timeout)])
        if test_type == "integration":
            # Use loadfile distribution for integration tests for better balance
            cmd.extend(["--dist", "loadfile"])

    # Add common options
    cmd.extend(
        [
            "-q",  # Quiet output
            "--tb=short",  # Short traceback format
            "--maxfail=3" if not fail_fast else "--maxfail=1",
            "--durations=10",  # Show 10 slowest tests
        ]
    )

    # Exclude slow/end-to-end tests unless specifically requested
    if test_type != "end_to_end":
        cmd.extend(["-m", "not end_to_end and not slow"])

    # Coverage options
    if coverage:
        cmd.extend(
            [
                "--cov",
                "--cov-report=term-missing",
                "--cov-report=xml",
                "--cov-fail-under=80",
            ]
        )

    # Verbose mode
    if verbose:
        cmd.extend(["-v", "--tb=long"])

    print(f"Running command: {' '.join(cmd)}")
    print(f"Working directory: {os.getcwd()}")
    print(f"Test type: {test_type}")

    # Display parallelization info
    if test_type == "end_to_end":
        print("Workers: 1 (sequential execution for E2E tests)")
    else:
        print("Workers: auto (pytest-xdist will determine optimal count)")
    print("-" * 50)

    # Run tests
    start_time = time.time()
    try:
        # Use reasonable total timeout (max 30 minutes)
        total_timeout = min(timeout * 3, 1800)  # Cap at 30 minutes
        result = subprocess.run(cmd, timeout=total_timeout)
        execution_time = time.time() - start_time

        print("-" * 50)
        print(f"Tests completed in {execution_time:.1f}s")

        return result.returncode == 0

    except subprocess.TimeoutExpired:
        print(f"Tests timed out after {total_timeout}s")
        return False
    except KeyboardInterrupt:
        print("Tests interrupted by user")
        return False
    except Exception as e:
        print(f"Error running tests: {e}")
        return False


def main():
    """Main entry point for test runner."""
    import argparse

    parser = argparse.ArgumentParser(description="Optimized test runner")
    parser.add_argument(
        "package", help="Package to test (e.g., receipt_dynamo)"
    )
    parser.add_argument(
        "test_paths",
        nargs="*",
        help="Test paths or files to run (space-separated)",
    )
    parser.add_argument(
        "--test-type",
        default="unit",
        choices=["unit", "integration", "end_to_end"],
        help="Type of tests to run",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )
    parser.add_argument(
        "--coverage",
        "-c",
        action="store_true",
        help="Enable coverage reporting",
    )
    parser.add_argument(
        "--fail-fast", "-x", action="store_true", help="Stop on first failure"
    )
    parser.add_argument(
        "--timeout", type=int, default=600, help="Per-test timeout in seconds"
    )

    args = parser.parse_args()

    # Validate package exists
    if not os.path.exists(args.package):
        print(f"Error: Package directory '{args.package}' not found")
        sys.exit(1)

    # Handle test paths - if empty, use default test directory
    if not args.test_paths:
        test_paths = ["tests"]
    else:
        # If we have exactly one argument that contains spaces, split it
        if len(args.test_paths) == 1 and " " in args.test_paths[0]:
            test_paths = args.test_paths[0].split()
        else:
            test_paths = args.test_paths

    # Validate test paths
    valid_paths = []
    for path in test_paths:
        full_path = os.path.join(args.package, path)
        if os.path.exists(full_path):
            valid_paths.append(path)
        else:
            print(f"Warning: Test path '{path}' not found, skipping")

    if not valid_paths:
        print("Error: No valid test paths found")
        sys.exit(1)

    # Run tests
    success = run_tests(
        package=args.package,
        test_paths=valid_paths,
        test_type=args.test_type,
        verbose=args.verbose,
        coverage=args.coverage,
        fail_fast=args.fail_fast,
        timeout=args.timeout,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

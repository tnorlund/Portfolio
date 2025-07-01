#!/usr/bin/env python3
"""
Smart test runner that can skip tests based on file changes and cached results.
"""

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Set


def get_file_hash(file_path: Path) -> str:
    """Get hash of a file's contents."""
    try:
        with open(file_path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except:
        return ""


def get_changed_files(base_ref: str = "origin/main") -> Set[str]:
    """Get list of files that have changed compared to base branch."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base_ref, "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return (
            set(result.stdout.strip().split("\n")) if result.stdout.strip() else set()
        )
    except:
        # If git diff fails, assume all files changed (safe fallback)
        return {"*"}


def get_test_dependencies(test_file: Path, package_dir: Path) -> Set[Path]:
    """
    Analyze a test file to determine which source files it depends on.
    This is a simplified dependency analysis.
    """
    dependencies = set()

    try:
        with open(test_file, "r") as f:
            content = f.read()

        # Look for imports that reference the package
        import re

        package_name = package_dir.name

        # Find imports like: from receipt_dynamo.models import X
        import_pattern = rf"from {package_name}\.([a-zA-Z0-9_.]+) import"
        imports = re.findall(import_pattern, content)

        for import_path in imports:
            # Convert import path to file path
            file_path = package_dir / (import_path.replace(".", "/") + ".py")
            if file_path.exists():
                dependencies.add(file_path)

            # Also check for __init__.py files in directories
            dir_path = package_dir / import_path.replace(".", "/")
            if dir_path.is_dir():
                init_file = dir_path / "__init__.py"
                if init_file.exists():
                    dependencies.add(init_file)

    except Exception as e:
        print(f"Warning: Could not analyze dependencies for {test_file}: {e}")
        # If analysis fails, assume it depends on all source files (safe fallback)
        for py_file in package_dir.rglob("*.py"):
            if not py_file.name.startswith("test_"):
                dependencies.add(py_file)

    return dependencies


def should_run_test(
    test_file: Path,
    package_dir: Path,
    changed_files: Set[str],
    cache_file: Path,
) -> bool:
    """
    Determine if a test should run based on:
    1. Whether its dependencies have changed
    2. Whether the test itself has changed
    3. Whether it's been recently run successfully
    """

    # If "*" in changed_files, it means we couldn't determine changes - run all tests
    if "*" in changed_files:
        return True

    # Check if test file itself has changed
    test_file_str = str(test_file)
    if any(changed_file in test_file_str for changed_file in changed_files):
        return True

    # Check if any dependencies have changed
    dependencies = get_test_dependencies(test_file, package_dir)
    for dep in dependencies:
        dep_str = str(dep)
        if any(changed_file in dep_str for changed_file in changed_files):
            return True

    # Check cache for recent successful runs
    if cache_file.exists():
        try:
            with open(cache_file, "r") as f:
                cache = json.load(f)

            test_key = str(test_file.relative_to(package_dir))
            if test_key in cache:
                test_info = cache[test_key]

                # Check if test was successful recently
                last_run = datetime.fromisoformat(
                    test_info.get("last_success", "2000-01-01")
                )
                if datetime.now() - last_run < timedelta(hours=24):
                    # Check if file hashes match (no changes since last successful run)
                    current_hash = get_file_hash(test_file)
                    if current_hash == test_info.get("file_hash", ""):
                        return False  # Skip test - no changes and recently passed
        except Exception as e:
            print(f"Warning: Could not read test cache: {e}")

    return True  # Default to running the test


def update_test_cache(
    test_file: Path, package_dir: Path, cache_file: Path, success: bool
):
    """Update the test result cache."""
    try:
        cache = {}
        if cache_file.exists():
            with open(cache_file, "r") as f:
                cache = json.load(f)

        test_key = str(test_file.relative_to(package_dir))

        if test_key not in cache:
            cache[test_key] = {}

        cache[test_key]["file_hash"] = get_file_hash(test_file)
        cache[test_key]["last_run"] = datetime.now().isoformat()

        if success:
            cache[test_key]["last_success"] = datetime.now().isoformat()

        # Ensure cache directory exists
        cache_file.parent.mkdir(parents=True, exist_ok=True)

        with open(cache_file, "w") as f:
            json.dump(cache, f, indent=2)

    except Exception as e:
        print(f"Warning: Could not update test cache: {e}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Smart test runner with change detection"
    )
    parser.add_argument("package", help="Package directory to test")
    parser.add_argument("test_paths", nargs="*", help="Specific test paths (optional)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show which tests would run"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run all tests regardless of changes",
    )
    parser.add_argument(
        "--base-ref",
        default="origin/main",
        help="Base git reference for change detection",
    )

    args = parser.parse_args()

    package_dir = Path(args.package)
    if not package_dir.exists():
        print(f"Error: Package directory {package_dir} does not exist")
        sys.exit(1)

    # Get changed files
    changed_files = get_changed_files(args.base_ref) if not args.force else {"*"}

    # Determine test files to check
    if args.test_paths:
        test_files = []
        for path in args.test_paths:
            if " " in path:  # Handle space-separated paths from GitHub Actions
                test_files.extend([Path(package_dir / p.strip()) for p in path.split()])
            else:
                test_files.append(Path(package_dir / path))
    else:
        test_files = list(package_dir.rglob("test_*.py"))

    # Filter tests that should run
    cache_file = Path(f".pytest_cache/{package_dir.name}_test_cache.json")
    tests_to_run = []
    tests_to_skip = []

    for test_file in test_files:
        if not test_file.exists():
            continue

        if should_run_test(test_file, package_dir, changed_files, cache_file):
            tests_to_run.append(test_file)
        else:
            tests_to_skip.append(test_file)

    # Report results
    print(f"📊 Smart Test Analysis for {package_dir.name}")
    print(
        f"   Changed files: {len(changed_files) if '*' not in changed_files else 'all'}"
    )
    print(f"   Total test files: {len(test_files)}")
    print(f"   Tests to run: {len(tests_to_run)}")
    print(f"   Tests to skip: {len(tests_to_skip)}")

    if args.dry_run:
        if tests_to_run:
            print("\n🏃 Tests that would run:")
            for test in tests_to_run:
                print(f"   • {test.relative_to(package_dir)}")

        if tests_to_skip:
            print("\n⏭️  Tests that would be skipped:")
            for test in tests_to_skip:
                print(f"   • {test.relative_to(package_dir)}")
        return

    if not tests_to_run:
        print("✅ No tests need to run based on changes!")
        return

    # Run the tests
    test_paths_str = " ".join(str(t.relative_to(package_dir)) for t in tests_to_run)
    print(f"\n🚀 Running optimized tests...")

    # Use the existing optimized test runner
    cmd = [
        "python",
        "scripts/run_tests_optimized.py",
        str(package_dir),
        test_paths_str,
        "--test-type",
        "integration" if "integration" in test_paths_str else "unit",
    ]

    result = subprocess.run(cmd, cwd=package_dir.parent)

    # Update cache based on results
    success = result.returncode == 0
    for test_file in tests_to_run:
        update_test_cache(test_file, package_dir, cache_file, success)

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()

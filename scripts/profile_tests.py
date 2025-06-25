#!/usr/bin/env python3
"""
Profile test execution to identify slow tests and optimization opportunities.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


def run_pytest_with_profiling(package: str, test_path: str = "tests") -> Dict[str, Any]:
    """Run pytest with profiling and duration reporting."""

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        f"{package}/{test_path}",
        "--durations=0",  # Show all test durations
        "--tb=no",
        "-q",
        "--collect-only",  # First, just collect to see how many tests
    ]

    print(f"\nCollecting tests from {package}/{test_path}...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    # Count tests
    test_count = 0
    if result.stdout:
        lines = result.stdout.split("\n")
        for line in lines:
            if "test session starts" in line or "collected" in line:
                match = re.search(r"(\d+) items?", line)
                if match:
                    test_count = int(match.group(1))

    print(f"Found {test_count} tests")

    # Now run with timing
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        f"{package}/{test_path}",
        "--durations=20",  # Show 20 slowest tests
        "-m",
        "not end_to_end and not slow",
        "--tb=line",
        "-v",
    ]

    print(f"Running tests with profiling...")
    start_time = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    total_time = time.time() - start_time

    # Parse durations from output
    durations = []
    lines = result.stdout.split("\n")
    in_durations = False

    for line in lines:
        if "slowest durations" in line.lower():
            in_durations = True
            continue
        if in_durations and line.strip():
            if line.startswith("="):
                break
            # Parse duration line: "0.12s call     test_file.py::test_name"
            parts = line.strip().split()
            if len(parts) >= 3 and parts[0].endswith("s"):
                duration = float(parts[0][:-1])
                test_name = parts[-1] if parts[-1].startswith("test_") else parts[2]
                durations.append(
                    {
                        "duration": duration,
                        "test": test_name,
                        "phase": parts[1] if len(parts) > 1 else "call",
                    }
                )

    return {
        "package": package,
        "test_path": test_path,
        "total_time": total_time,
        "test_count": test_count,
        "durations": durations,
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def analyze_test_distribution(results: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze test execution patterns."""
    durations = results["durations"]

    if not durations:
        return {"error": "No duration data available"}

    total_test_time = sum(d["duration"] for d in durations)
    avg_time = total_test_time / len(durations) if durations else 0

    # Find tests that could be marked as slow
    slow_threshold = max(1.0, avg_time * 3)
    slow_tests = [d for d in durations if d["duration"] > slow_threshold]

    # Group by test file
    file_times = {}
    for d in durations:
        test_file = d["test"].split("::")[0] if "::" in d["test"] else "unknown"
        if test_file not in file_times:
            file_times[test_file] = []
        file_times[test_file].append(d["duration"])

    # Calculate file-level stats
    file_stats = {}
    for file_name, times in file_times.items():
        file_stats[file_name] = {
            "count": len(times),
            "total_time": sum(times),
            "avg_time": sum(times) / len(times),
            "max_time": max(times),
        }

    return {
        "total_measured_time": total_test_time,
        "avg_test_time": avg_time,
        "slow_threshold": slow_threshold,
        "slow_tests": slow_tests,
        "file_stats": file_stats,
        "recommendations": generate_recommendations(results, slow_tests, file_stats),
    }


def generate_recommendations(
    results: Dict[str, Any], slow_tests: List[Dict], file_stats: Dict
) -> List[str]:
    """Generate optimization recommendations."""
    recommendations = []

    # Slow test recommendations
    if slow_tests:
        recommendations.append(
            f"Mark {len(slow_tests)} slow tests with @pytest.mark.slow:"
        )
        for test in slow_tests[:5]:  # Show top 5
            recommendations.append(f"  - {test['test']} ({test['duration']:.2f}s)")

    # File-level recommendations
    slowest_files = sorted(
        file_stats.items(), key=lambda x: x[1]["total_time"], reverse=True
    )[:3]
    if slowest_files:
        recommendations.append("Slowest test files to optimize:")
        for file_name, stats in slowest_files:
            recommendations.append(
                f"  - {file_name}: {stats['total_time']:.2f}s total, {stats['avg_time']:.2f}s avg"
            )

    # Parallelization recommendations
    test_count = results["test_count"]
    total_time = results["total_time"]
    if test_count > 50 and total_time > 30:
        theoretical_parallel_time = total_time / 4  # Assuming 4 cores
        recommendations.append(
            f"Parallel execution could reduce time from {total_time:.1f}s to ~{theoretical_parallel_time:.1f}s"
        )

    return recommendations


def main():
    """Profile tests for all packages."""
    packages = [
        ("receipt_dynamo", "tests/unit"),
        ("receipt_dynamo", "tests/integration"),
        ("receipt_label", "receipt_label/tests"),
    ]

    all_results = []

    for package, test_path in packages:
        if not Path(package).exists():
            print(f"Skipping {package} - directory not found")
            continue

        print(f"\n{'='*60}")
        print(f"Profiling {package}/{test_path}")
        print(f"{'='*60}")

        try:
            results = run_pytest_with_profiling(package, test_path)
            analysis = analyze_test_distribution(results)

            print(f"\nResults for {package}/{test_path}:")
            print(f"  Tests: {results['test_count']}")
            print(f"  Total time: {results['total_time']:.2f}s")
            print(f"  Success: {results['success']}")

            if "total_measured_time" in analysis:
                print(f"  Measured test time: {analysis['total_measured_time']:.2f}s")
                print(f"  Average per test: {analysis['avg_test_time']:.2f}s")
                print(
                    f"  Slow tests (>{analysis['slow_threshold']:.1f}s): {len(analysis['slow_tests'])}"
                )

            print("\nRecommendations:")
            for rec in analysis.get("recommendations", []):
                print(f"  {rec}")

            all_results.append({"results": results, "analysis": analysis})

        except Exception as e:
            print(f"Error profiling {package}/{test_path}: {e}")

    # Save detailed results
    output_file = "test_profile_results.json"
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    print(f"\n{'='*60}")
    print(f"Profile complete! Detailed results saved to {output_file}")
    print("Use this data to:")
    print("  1. Mark slow tests with @pytest.mark.slow")
    print("  2. Optimize the slowest test files")
    print("  3. Consider splitting large test files")
    print("  4. Improve test parallelization")


if __name__ == "__main__":
    main()

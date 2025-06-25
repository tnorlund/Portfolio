#!/usr/bin/env python3
"""
Benchmark test execution times with different configurations.
This helps measure the impact of pytest optimizations.
"""

import subprocess
import time
import sys
import os
from pathlib import Path
from typing import Dict, List, Tuple
import json


def run_tests(package: str, config: str, options: List[str]) -> Tuple[float, bool]:
    """Run tests and return execution time and success status."""
    start_time = time.time()
    
    cmd = [
        sys.executable, "-m", "pytest",
        f"{package}/tests",
        *options
    ]
    
    print(f"\nRunning: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent
        )
        success = result.returncode == 0
        execution_time = time.time() - start_time
        
        if not success:
            print(f"Tests failed with return code: {result.returncode}")
            print(f"STDERR: {result.stderr[:500]}")
        
        return execution_time, success
    except Exception as e:
        print(f"Error running tests: {e}")
        return time.time() - start_time, False


def benchmark_configurations():
    """Benchmark different pytest configurations."""
    
    packages = ["receipt_dynamo", "receipt_label"]
    
    configurations = {
        "baseline": [
            "-m", "not end_to_end",
            "--tb=short",
            "-v"
        ],
        "parallel_2_workers": [
            "-n", "2",
            "-m", "not end_to_end",
            "--tb=short",
            "-q"
        ],
        "parallel_auto": [
            "-n", "auto",
            "-m", "not end_to_end",
            "--tb=short",
            "-q"
        ],
        "parallel_fast": [
            "-n", "auto",
            "-m", "not end_to_end and not slow",
            "--tb=short",
            "-q",
            "--maxfail=5",
            "-x"
        ],
        "parallel_with_timeout": [
            "-n", "auto",
            "-m", "not end_to_end and not slow",
            "--tb=short",
            "-q",
            "--timeout=60",
            "--maxfail=5",
            "-x"
        ],
        "unit_only_parallel": [
            "-n", "auto",
            "-m", "unit",
            "--tb=short",
            "-q",
            "--timeout=60"
        ]
    }
    
    results = {}
    
    for package in packages:
        print(f"\n{'='*60}")
        print(f"Benchmarking {package}")
        print(f"{'='*60}")
        
        package_results = {}
        
        for config_name, options in configurations.items():
            print(f"\nConfiguration: {config_name}")
            
            # Clear pytest cache for fair comparison
            subprocess.run([
                sys.executable, "-m", "pytest",
                "--cache-clear"
            ], capture_output=True)
            
            # Run tests
            execution_time, success = run_tests(package, config_name, options)
            
            package_results[config_name] = {
                "time": round(execution_time, 2),
                "success": success
            }
            
            print(f"Execution time: {execution_time:.2f}s")
            print(f"Status: {'✓ PASSED' if success else '✗ FAILED'}")
        
        results[package] = package_results
    
    # Print summary
    print(f"\n{'='*60}")
    print("BENCHMARK SUMMARY")
    print(f"{'='*60}")
    
    for package, package_results in results.items():
        print(f"\n{package}:")
        baseline_time = package_results.get("baseline", {}).get("time", 0)
        
        for config_name, result in package_results.items():
            time_taken = result["time"]
            speedup = baseline_time / time_taken if time_taken > 0 else 0
            status = "✓" if result["success"] else "✗"
            
            print(f"  {config_name:25} {time_taken:6.2f}s  {speedup:5.2f}x  {status}")
    
    # Save results
    with open("test_benchmark_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\nResults saved to test_benchmark_results.json")


if __name__ == "__main__":
    print("Starting pytest benchmark...")
    print("This will run tests multiple times with different configurations.")
    print("It may take several minutes to complete.\n")
    
    benchmark_configurations()
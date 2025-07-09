#!/usr/bin/env python3
"""Analyze integration test failures and create a comprehensive fix strategy."""

import subprocess
import re
import json
from typing import Dict, List, Set


def run_tests_and_capture_failures():
    """Run receipt validation result tests and capture all failures."""
    cmd = [
        "python3", "-m", "pytest", 
        "tests/integration/test__receipt_validation_result.py",
        "-v", "--tb=short", "--no-header"
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, cwd="/Users/tnorlund/GitHub/Portfolio-dev-session/receipt_dynamo")
    return result.stdout + result.stderr


def parse_error_patterns(output: str) -> Dict[str, List[str]]:
    """Parse test output to extract error patterns."""
    patterns = {
        "parameter_validation": [],
        "client_error_mapping": [],
        "functional_failures": []
    }
    
    lines = output.split('\n')
    current_test = None
    
    for line in lines:
        # Capture test names
        if "FAILED tests/integration/test__receipt_validation_result.py::" in line:
            current_test = line.split("::")[-1].split()[0]
        
        # Capture assertion errors showing expected vs actual
        if "AssertionError:" in line and "Regex pattern did not match" in line:
            patterns["parameter_validation"].append(f"{current_test}: {line.strip()}")
        elif "Input:" in line and current_test:
            patterns["parameter_validation"].append(f"  Actual: {line.strip()}")
        elif "Regex:" in line and current_test:
            patterns["parameter_validation"].append(f"  Expected: {line.strip()}")
            
    return patterns


def main():
    """Analyze failures and create fix strategy."""
    print("🔍 Analyzing receipt validation result test failures...")
    
    output = run_tests_and_capture_failures()
    patterns = parse_error_patterns(output)
    
    print("\n📋 Error Analysis:")
    print("=" * 60)
    
    # Count failures by type
    failed_tests = [line for line in output.split('\n') if 'FAILED' in line and '::' in line]
    print(f"Total failed tests: {len(failed_tests)}")
    
    print("\n🏷️  Error Categories:")
    
    # Parameter validation errors
    param_errors = [t for t in failed_tests if 'invalid_parameters' in t]
    print(f"Parameter validation errors: {len(param_errors)}")
    
    # Client error mapping errors  
    client_errors = [t for t in failed_tests if 'client_errors' in t]
    print(f"Client error mapping errors: {len(client_errors)}")
    
    # Functional errors
    functional_errors = [t for t in failed_tests if 'invalid_parameters' not in t and 'client_errors' not in t]
    print(f"Functional errors: {len(functional_errors)}")
    
    print("\n🔧 Common Error Patterns Found:")
    
    # Extract common error patterns from output
    error_lines = [line for line in output.split('\n') if 'Input:' in line or 'Regex:' in line]
    
    expected_patterns = set()
    actual_patterns = set()
    
    for line in error_lines:
        if 'Regex:' in line:
            pattern = line.split('Regex:')[1].strip().strip("'\"")
            expected_patterns.add(pattern)
        elif 'Input:' in line:
            pattern = line.split('Input:')[1].strip().strip("'\"")
            actual_patterns.add(pattern)
    
    print("\n Expected error message patterns:")
    for pattern in sorted(expected_patterns):
        print(f"   - {pattern}")
        
    print("\n Actual error message patterns:")
    for pattern in sorted(actual_patterns):
        print(f"   - {pattern}")
    
    print("\n🎯 Fix Strategy:")
    print("1. Update base_operations.py to add missing operation mappings")
    print("2. Fix custom error handling in _receipt_validation_result.py")
    print("3. Ensure consistent parameter validation messages")
    print("4. Add comprehensive error message mappings for all operations")
    
    # Look for specific operation types
    operations = set()
    for line in failed_tests:
        if 'client_errors' in line:
            # Extract operation from test name
            parts = line.split('::')[1].split('_')
            if len(parts) >= 2:
                operation = parts[0] + '_' + '_'.join([p for p in parts[1:] if p not in ['client', 'errors', 'success', 'invalid', 'parameters']])
                operations.add(operation.replace('_client_errors', '').replace('_invalid_parameters', ''))
    
    print(f"\n📝 Operations needing error mappings:")
    for op in sorted(operations):
        print(f"   - {op}")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""Script to analyze and fix integration test failures related to error messages."""

import subprocess
import re
from typing import Dict, List, Tuple
import sys


def run_test(test_path: str) -> Tuple[bool, str]:
    """Run a specific test and capture output."""
    cmd = ["python3", "-m", "pytest", test_path, "-xvs", "--tb=short"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


def extract_error_info(output: str) -> Dict[str, str]:
    """Extract expected vs actual error messages from test output."""
    info = {}
    
    # Look for assertion errors showing expected vs actual
    assertion_pattern = r"AssertionError: assert '(.+?)' in '(.+?)'"
    match = re.search(assertion_pattern, output)
    if match:
        info['expected'] = match.group(1)
        info['actual'] = match.group(2)
    
    # Look for ValueError patterns
    value_error_pattern = r"with pytest\.raises\(ValueError, match=['\"](.+?)['\"]\)"
    match = re.search(value_error_pattern, output)
    if match:
        info['expected_pattern'] = match.group(1)
    
    return info


def analyze_failures():
    """Analyze all test failures and categorize them."""
    
    failing_tests = [
        ("test__word.py", "test_updateWords_raises_value_error_words_not_list_of_words"),
        ("test__image.py", "test_addImage_raises_value_error_for_none_image"),
        ("test__image.py", "test_addImage_raises_value_error_for_invalid_type"),
        ("test__image.py", "test_updateImages_raises_value_error_images_none"),
        ("test__image.py", "test_updateImages_raises_value_error_images_not_list"),
        ("test__job.py", "test_addJob_raises_value_error_job_not_instance"),
        ("test__job.py", "test_addJobs_raises_value_error_jobs_not_list"),
        ("test__job.py", "test_updateJob_raises_value_error_job_not_instance"),
        ("test__job.py", "test_deleteJob_raises_value_error_job_not_instance"),
    ]
    
    error_mappings = {}
    
    for test_file, test_name in failing_tests:
        test_path = f"tests/integration/{test_file}::{test_name}"
        print(f"\nAnalyzing {test_path}")
        
        success, output = run_test(test_path)
        if not success:
            error_info = extract_error_info(output)
            if error_info:
                print(f"  Expected: {error_info.get('expected', 'N/A')}")
                print(f"  Actual: {error_info.get('actual', 'N/A')}")
                
                # Categorize the error
                if 'Job' in test_name:
                    entity = 'Job'
                    param = 'job' if 'Jobs' not in test_name else 'jobs'
                elif 'Image' in test_name:
                    entity = 'Image'
                    param = 'image' if 'Images' not in test_name else 'images'
                elif 'Word' in test_name:
                    entity = 'Word'
                    param = 'words'
                
                error_mappings[f"{entity}:{param}"] = error_info


def main():
    """Main function to analyze and suggest fixes."""
    print("Analyzing integration test failures...")
    
    # Common error patterns we're seeing:
    # 1. Parameter validation messages don't match expected format
    # 2. Entity class name capitalization issues
    # 3. Missing special handling for certain parameters
    
    print("\n=== Error Message Patterns ===")
    print("\nParameter validation errors need special handling for:")
    print("- 'image' parameter -> 'Image parameter is required and cannot be None.'")
    print("- 'images' parameter -> 'Images parameter is required and cannot be None.'")
    print("- 'job' parameter -> 'Job parameter is required and cannot be None.'")
    print("- 'jobs' parameter -> 'Jobs parameter is required and cannot be None.'")
    print("- 'words' parameter -> 'Words must be provided as a list.'")
    
    print("\n=== Suggested base_operations.py Updates ===")
    print("""
Update _validate_entity method to add:
    if param_name == "image":
        raise ValueError("Image parameter is required and cannot be None.")
    elif param_name == "job":
        raise ValueError("Job parameter is required and cannot be None.")
        
Update _validate_entity_list method to add:
    if param_name == "images":
        raise ValueError("Images parameter is required and cannot be None.")
    elif param_name == "jobs":
        raise ValueError("Jobs parameter is required and cannot be None.")
    elif param_name == "words":
        raise ValueError("Words must be provided as a list.")
""")
    
    print("\n=== Next Steps ===")
    print("1. Update base_operations.py with the special parameter handling")
    print("2. Add missing operation mappings for error messages")
    print("3. Ensure entity class validation messages match expected format")
    print("4. Run tests again to verify fixes")


if __name__ == "__main__":
    main()
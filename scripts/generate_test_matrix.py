#!/usr/bin/env python3
"""
Generate dynamic GitHub Actions matrix based on current test structure.
This allows for automatic optimization as tests are added/removed.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any

# Import our test analysis functions
sys.path.append(os.path.dirname(__file__))
from analyze_tests import analyze_integration_tests, create_test_groups


def generate_dynamic_matrix() -> Dict[str, Any]:
    """Generate GitHub Actions matrix based on current test structure."""
    matrix_include = []
    
    # Receipt DynamoDB - split integration tests into groups
    receipt_dynamo_analysis = analyze_integration_tests('/Users/tnorlund/GitHub/example-pytest-optimization/receipt_dynamo')
    
    if receipt_dynamo_analysis and receipt_dynamo_analysis['files']:
        # Add unit tests (single job)
        matrix_include.append({
            'package': 'receipt_dynamo',
            'test_type': 'unit', 
            'test_path': 'tests/unit'
        })
        
        # Create optimal test groups for integration tests
        test_groups = create_test_groups(receipt_dynamo_analysis['files'], target_groups=4)
        
        for i, group in enumerate(test_groups):
            if not group:  # Skip empty groups
                continue
                
            # Convert file info to test paths
            test_paths = []
            for file_info in group:
                test_paths.append(file_info['path'])
            
            matrix_include.append({
                'package': 'receipt_dynamo',
                'test_type': 'integration',
                'test_group': f'group-{i+1}',
                'test_path': ' '.join(test_paths),
                'estimated_time': sum(f['estimated_time'] for f in group),
                'test_count': sum(f['test_count'] for f in group)
            })
    
    # Receipt Label - smaller package, single job for all tests
    receipt_label_dir = '/Users/tnorlund/GitHub/example-pytest-optimization/receipt_label'
    if os.path.exists(receipt_label_dir):
        matrix_include.append({
            'package': 'receipt_label',
            'test_type': 'all',
            'test_path': 'tests'
        })
    
    return {'include': matrix_include}


def print_matrix_summary(matrix: Dict[str, Any]) -> None:
    """Print a human-readable summary of the generated matrix."""
    print("Generated Test Matrix Summary:")
    print("=" * 50)
    
    total_jobs = len(matrix['include'])
    total_estimated_time = 0
    
    for job in matrix['include']:
        job_name = f"{job['package']}-{job['test_type']}"
        if 'test_group' in job:
            job_name += f"-{job['test_group']}"
        
        estimated_time = job.get('estimated_time', 0)
        test_count = job.get('test_count', 'unknown')
        
        print(f"Job: {job_name:35} | Tests: {str(test_count):>6} | Time: ~{estimated_time:5.1f}s")
        total_estimated_time += estimated_time
    
    print("=" * 50)
    print(f"Total Jobs: {total_jobs}")
    print(f"Estimated Sequential Time: {total_estimated_time:.1f}s ({total_estimated_time/60:.1f}m)")
    
    # Calculate parallel execution time (longest job)
    max_time = max((job.get('estimated_time', 0) for job in matrix['include']), default=0)
    print(f"Estimated Parallel Time: {max_time:.1f}s ({max_time/60:.1f}m)")
    print(f"Speedup: {total_estimated_time/max_time:.1f}x" if max_time > 0 else "Speedup: N/A")


def save_matrix_for_github(matrix: Dict[str, Any], output_file: str = None) -> None:
    """Save matrix in format suitable for GitHub Actions."""
    if output_file is None:
        output_file = '/Users/tnorlund/GitHub/example-pytest-optimization/.github/test_matrix.json'
    
    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    with open(output_file, 'w') as f:
        json.dump(matrix, f, indent=2)
    
    print(f"\nMatrix saved to: {output_file}")


def generate_workflow_yaml_snippet(matrix: Dict[str, Any]) -> str:
    """Generate YAML snippet for GitHub Actions workflow."""
    yaml_lines = ["      matrix:"]
    yaml_lines.append("        include:")
    
    for job in matrix['include']:
        yaml_lines.append(f"          - package: {job['package']}")
        yaml_lines.append(f"            test_type: {job['test_type']}")
        yaml_lines.append(f"            test_path: {job['test_path']}")
        
        if 'test_group' in job:
            yaml_lines.append(f"            test_group: {job['test_group']}")
        
        if len(matrix['include']) > 1:  # Add separator between jobs
            yaml_lines.append("")
    
    return "\n".join(yaml_lines)


def main():
    """Main function to generate and display test matrix."""
    print("Analyzing current test structure...")
    
    # Generate dynamic matrix
    matrix = generate_dynamic_matrix()
    
    if not matrix['include']:
        print("No test jobs found!")
        sys.exit(1)
    
    # Display summary
    print_matrix_summary(matrix)
    
    # Save matrix file
    save_matrix_for_github(matrix)
    
    # Generate workflow YAML snippet
    print("\nWorkflow YAML snippet:")
    print("-" * 30)
    print(generate_workflow_yaml_snippet(matrix))
    
    # Print full JSON for debugging
    if '--verbose' in sys.argv:
        print("\nFull Matrix JSON:")
        print(json.dumps(matrix, indent=2))


if __name__ == '__main__':
    main()
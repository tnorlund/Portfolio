#!/usr/bin/env python3
"""
Analyze test files to understand their structure and execution times
for optimal parallelization.
"""

import os
import subprocess
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple


def get_test_files(test_dir: str) -> List[str]:
    """Get all test files in a directory."""
    test_files = []
    for root, dirs, files in os.walk(test_dir):
        for file in files:
            if file.startswith('test_') and file.endswith('.py'):
                test_files.append(os.path.join(root, file))
    return test_files


def count_tests_in_file(file_path: str, package_dir: str) -> int:
    """Count the number of tests in a file."""
    try:
        result = subprocess.run([
            'python', '-m', 'pytest', file_path, 
            '--collect-only', '-q'
        ], capture_output=True, text=True, cwd=package_dir, timeout=30)
        
        if result.returncode == 0:
            return result.stdout.count('::test_')
        else:
            print(f"Warning: Could not collect tests from {file_path}")
            return 0
    except subprocess.TimeoutExpired:
        print(f"Warning: Timeout collecting tests from {file_path}")
        return 0
    except Exception as e:
        print(f"Error collecting tests from {file_path}: {e}")
        return 0


def estimate_test_time(file_path: str, package_dir: str) -> float:
    """Estimate test execution time by running a dry-run."""
    try:
        start_time = time.time()
        result = subprocess.run([
            'python', '-m', 'pytest', file_path, 
            '--collect-only', '--quiet'
        ], capture_output=True, text=True, cwd=package_dir, timeout=60)
        collection_time = time.time() - start_time
        
        # Rough estimate: collection time * 50 (empirical factor)
        # Plus base time per test
        test_count = result.stdout.count('::test_')
        estimated_time = collection_time * 50 + test_count * 2
        
        return estimated_time
    except:
        return 60.0  # Default estimate


def analyze_integration_tests(package_dir: str) -> Dict:
    """Analyze integration tests for optimal splitting."""
    integration_dir = os.path.join(package_dir, 'tests', 'integration')
    
    if not os.path.exists(integration_dir):
        return {}
    
    test_files = get_test_files(integration_dir)
    
    file_analysis = []
    
    for file_path in test_files:
        rel_path = os.path.relpath(file_path, package_dir)
        test_count = count_tests_in_file(file_path, package_dir)
        estimated_time = estimate_test_time(file_path, package_dir)
        
        file_analysis.append({
            'file': os.path.basename(file_path),
            'path': rel_path,
            'test_count': test_count,
            'estimated_time': estimated_time,
            'size_bytes': os.path.getsize(file_path)
        })
    
    # Sort by estimated time (descending)
    file_analysis.sort(key=lambda x: x['estimated_time'], reverse=True)
    
    return {
        'total_files': len(file_analysis),
        'total_tests': sum(f['test_count'] for f in file_analysis),
        'total_estimated_time': sum(f['estimated_time'] for f in file_analysis),
        'files': file_analysis
    }


def create_test_groups(files: List[Dict], target_groups: int = 4) -> List[List[Dict]]:
    """Create balanced test groups for parallel execution."""
    if not files:
        return []
    
    # Sort files by estimated time (descending)
    sorted_files = sorted(files, key=lambda x: x['estimated_time'], reverse=True)
    
    # Initialize groups
    groups = [[] for _ in range(target_groups)]
    group_times = [0.0] * target_groups
    
    # Assign files to groups using a greedy approach
    for file_info in sorted_files:
        # Find the group with the least total time
        min_group_idx = group_times.index(min(group_times))
        groups[min_group_idx].append(file_info)
        group_times[min_group_idx] += file_info['estimated_time']
    
    return groups


def generate_github_matrix(groups: List[List[Dict]], package_name: str) -> Dict:
    """Generate GitHub Actions matrix configuration."""
    matrix_include = []
    
    for i, group in enumerate(groups):
        if not group:  # Skip empty groups
            continue
            
        # Create test paths for this group
        test_paths = [f['path'] for f in group]
        
        matrix_include.append({
            'package': package_name,
            'test_type': 'integration',
            'test_group': f'group-{i+1}',
            'test_paths': ' '.join(test_paths),
            'estimated_time': sum(f['estimated_time'] for f in group),
            'test_count': sum(f['test_count'] for f in group)
        })
    
    return {
        'include': matrix_include
    }


def main():
    """Main analysis function."""
    # Analyze receipt_dynamo integration tests
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    receipt_dynamo_dir = os.path.join(project_root, 'receipt_dynamo')
    
    print("Analyzing receipt_dynamo integration tests...")
    analysis = analyze_integration_tests(receipt_dynamo_dir)
    
    if not analysis:
        print("No integration tests found.")
        return
    
    print(f"\nTest Analysis Summary:")
    print(f"Total files: {analysis['total_files']}")
    print(f"Total tests: {analysis['total_tests']}")
    print(f"Estimated total time: {analysis['total_estimated_time']:.1f}s")
    
    print(f"\nTop 10 slowest test files:")
    for i, file_info in enumerate(analysis['files'][:10]):
        print(f"{i+1:2d}. {file_info['file']:40} - {file_info['test_count']:3d} tests, ~{file_info['estimated_time']:5.1f}s")
    
    # Create test groups
    groups = create_test_groups(analysis['files'], target_groups=4)
    
    print(f"\nOptimal Test Groups (4 parallel jobs):")
    for i, group in enumerate(groups):
        if group:
            total_time = sum(f['estimated_time'] for f in group)
            total_tests = sum(f['test_count'] for f in group)
            print(f"Group {i+1}: {len(group)} files, {total_tests} tests, ~{total_time:.1f}s")
            for file_info in group:
                print(f"  - {file_info['file']} ({file_info['test_count']} tests)")
    
    # Generate GitHub Actions matrix
    matrix = generate_github_matrix(groups, 'receipt_dynamo')
    
    # Save matrix to file
    matrix_file = os.path.join(script_dir, 'test_matrix.json')
    with open(matrix_file, 'w') as f:
        json.dump(matrix, f, indent=2)
    
    print(f"\nGitHub Actions matrix saved to: {matrix_file}")
    print(f"Matrix preview:")
    print(json.dumps(matrix, indent=2))


if __name__ == '__main__':
    main()
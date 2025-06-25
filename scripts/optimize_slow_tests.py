#!/usr/bin/env python3
"""
Analyze and optimize slow test files by identifying bottlenecks
and suggesting improvements.
"""

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional


def profile_test_file(package_dir: str, test_file: str) -> Dict:
    """Profile a specific test file to identify slow tests."""
    try:
        start_time = time.time()
        
        # Run with durations to see slowest tests
        result = subprocess.run([
            'python', '-m', 'pytest', test_file,
            '--durations=0',  # Show all durations
            '--tb=no',        # No traceback
            '-v'              # Verbose to see individual test names
        ], capture_output=True, text=True, cwd=package_dir, timeout=300)
        
        execution_time = time.time() - start_time
        
        # Parse durations from output
        durations = []
        for line in result.stdout.split('\n'):
            if '::test_' in line and 's' in line:
                # Extract duration and test name
                match = re.search(r'(\d+\.\d+)s.*?(test_\w+)', line)
                if match:
                    duration = float(match.group(1))
                    test_name = match.group(2)
                    durations.append((duration, test_name))
        
        # Sort by duration (slowest first)
        durations.sort(reverse=True)
        
        return {
            'file': test_file,
            'total_time': execution_time,
            'test_durations': durations[:10],  # Top 10 slowest
            'exit_code': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr
        }
        
    except subprocess.TimeoutExpired:
        return {
            'file': test_file,
            'total_time': 300,
            'test_durations': [],
            'exit_code': -1,
            'error': 'Timeout'
        }
    except Exception as e:
        return {
            'file': test_file,
            'total_time': 0,
            'test_durations': [],
            'exit_code': -1,
            'error': str(e)
        }


def analyze_test_patterns(package_dir: str, test_file: str) -> Dict:
    """Analyze test file for common performance anti-patterns."""
    test_path = os.path.join(package_dir, test_file)
    
    if not os.path.exists(test_path):
        return {'file': test_file, 'patterns': [], 'suggestions': []}
    
    with open(test_path, 'r') as f:
        content = f.read()
    
    patterns = []
    suggestions = []
    
    # Check for common performance issues
    if 'time.sleep' in content:
        patterns.append('Uses time.sleep() - consider mocking')
        suggestions.append('Replace time.sleep() with mock.patch for faster tests')
    
    if re.search(r'range\(\d{3,}\)', content):  # Loops with 100+ iterations
        patterns.append('Large loops detected')
        suggestions.append('Consider parametrizing tests or reducing loop iterations')
    
    if 'requests.get' in content or 'requests.post' in content:
        patterns.append('Makes HTTP requests - should be mocked')
        suggestions.append('Mock HTTP requests with responses library')
    
    if '@pytest.fixture' in content and 'scope=' not in content:
        patterns.append('Fixtures without explicit scope')
        suggestions.append('Add scope="session" or scope="module" to expensive fixtures')
    
    # Count test functions
    test_functions = re.findall(r'def test_\w+', content)
    if len(test_functions) > 50:
        patterns.append(f'Large test file ({len(test_functions)} tests)')
        suggestions.append('Consider splitting into multiple files')
    
    # Check for database operations
    if 'dynamo' in content.lower() or 'boto3' in content:
        patterns.append('Database operations detected')
        suggestions.append('Use moto for AWS mocking or consider test data fixtures')
    
    return {
        'file': test_file,
        'patterns': patterns,
        'suggestions': suggestions,
        'test_count': len(test_functions)
    }


def generate_optimization_report(package_dir: str, slow_files: List[str]) -> str:
    """Generate a comprehensive optimization report."""
    report = []
    report.append("# Test Performance Optimization Report")
    report.append("=" * 50)
    report.append("")
    
    total_optimizations = 0
    
    for test_file in slow_files:
        report.append(f"## {test_file}")
        report.append("")
        
        # Profile the file
        profile_result = profile_test_file(package_dir, test_file)
        pattern_analysis = analyze_test_patterns(package_dir, test_file)
        
        if profile_result.get('error'):
            report.append(f"❌ Error profiling: {profile_result['error']}")
            report.append("")
            continue
        
        # Execution time
        total_time = profile_result['total_time']
        report.append(f"**Total execution time:** {total_time:.1f}s")
        report.append(f"**Test count:** {pattern_analysis['test_count']}")
        report.append("")
        
        # Slowest tests
        if profile_result['test_durations']:
            report.append("**Slowest tests:**")
            for duration, test_name in profile_result['test_durations'][:5]:
                report.append(f"- {test_name}: {duration:.2f}s")
            report.append("")
        
        # Performance patterns
        if pattern_analysis['patterns']:
            report.append("**Performance issues found:**")
            for pattern in pattern_analysis['patterns']:
                report.append(f"- ⚠️  {pattern}")
            report.append("")
        
        # Suggestions
        if pattern_analysis['suggestions']:
            report.append("**Optimization suggestions:**")
            for suggestion in pattern_analysis['suggestions']:
                report.append(f"- 💡 {suggestion}")
                total_optimizations += 1
            report.append("")
        
        report.append("---")
        report.append("")
    
    report.append(f"**Total optimization opportunities:** {total_optimizations}")
    
    return "\n".join(report)


def main():
    """Analyze the slowest test files and generate optimization report."""
    package_dir = '/Users/tnorlund/GitHub/example-pytest-optimization/receipt_dynamo'
    
    # Top 10 slowest integration test files based on our analysis
    slow_files = [
        'tests/integration/test__receipt_word_label.py',
        'tests/integration/test__receipt_field.py',
        'tests/integration/test__receipt_letter.py',
        'tests/integration/test__receipt_validation_result.py',
        'tests/integration/test__receipt_label_analysis.py',
        'tests/integration/test__receipt_line_item_analysis.py',
        'tests/integration/test__receipt_chatgpt_validation.py',
        'tests/integration/test__receipt.py',
        'tests/integration/test__receipt_validation_category.py',
        'tests/integration/test__job.py'
    ]
    
    print("Analyzing slow test files for optimization opportunities...")
    print("This may take a few minutes...")
    print()
    
    # Generate report
    report = generate_optimization_report(package_dir, slow_files)
    
    # Save report
    report_file = '/Users/tnorlund/GitHub/example-pytest-optimization/TEST_OPTIMIZATION_REPORT.md'
    with open(report_file, 'w') as f:
        f.write(report)
    
    print(f"Optimization report saved to: {report_file}")
    print()
    print("Report preview:")
    print("-" * 30)
    print(report[:1000] + "..." if len(report) > 1000 else report)


if __name__ == '__main__':
    main()
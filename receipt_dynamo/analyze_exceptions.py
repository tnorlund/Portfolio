#!/usr/bin/env python3
"""
Script to analyze exception inconsistencies in receipt_dynamo data files.
"""

import re
from pathlib import Path
from typing import Dict

# Patterns to identify the type of error
ERROR_PATTERNS = {
    'not_found': [
        r'does not exist',
        r'not found',
        r'No .* found',
        r'cannot find',
        r'table not found'
    ],
    'parameter_validation': [
        r'must be',
        r'cannot be None',
        r'must contain',
        r'Invalid',
        r'required',
        r'must have',
        r'greater than',
        r'less than',
        r'positive integer',
        r'non-empty',
        r'between \d+ and \d+'
    ],
    'throughput': [
        r'[Tt]hroughput exceeded',
        r'ProvisionedThroughputExceededException'
    ],
    'server_error': [
        r'[Ii]nternal [Ss]erver [Ee]rror',
        r'InternalServerError'
    ],
    'access_denied': [
        r'[Aa]ccess [Dd]enied',
        r'AccessDeniedException'
    ]
}


def identify_error_type(error_message: str) -> str:
    """Identify the type of error based on the error message."""
    for error_type, patterns in ERROR_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, error_message, re.IGNORECASE):
                return error_type
    return 'default'


def analyze_all_files():
    """Analyze all data files and generate a report."""
    data_dir = Path(__file__).parent / 'receipt_dynamo' / 'data'
    
    # Get all Python files in data directory (excluding __init__.py and base_operations)
    files = [f for f in data_dir.glob('_*.py') if f.is_file()]
    
    print(f"Analyzing {len(files)} files for exception inconsistencies...\n")
    
    total_stats = {
        'ValueError': 0,
        'RuntimeError': 0,
        'files_with_issues': 0
    }
    
    detailed_report = []
    
    for filepath in sorted(files):
        with open(filepath, 'r') as f:
            content = f.read()
        
        file_stats = {
            'name': filepath.name,
            'ValueError': 0,
            'RuntimeError': 0,
            'ValueError_types': {},
            'RuntimeError_types': {}
        }
        
        # Count ValueError instances
        for match in re.finditer(r'raise ValueError\((.*?)\)', content, re.DOTALL):
            file_stats['ValueError'] += 1
            error_type = identify_error_type(match.group(1))
            file_stats['ValueError_types'][error_type] = file_stats['ValueError_types'].get(error_type, 0) + 1
        
        # Count RuntimeError instances
        for match in re.finditer(r'raise RuntimeError\((.*?)\)', content, re.DOTALL):
            file_stats['RuntimeError'] += 1
            error_type = identify_error_type(match.group(1))
            file_stats['RuntimeError_types'][error_type] = file_stats['RuntimeError_types'].get(error_type, 0) + 1
        
        if file_stats['ValueError'] > 0 or file_stats['RuntimeError'] > 0:
            total_stats['ValueError'] += file_stats['ValueError']
            total_stats['RuntimeError'] += file_stats['RuntimeError']
            total_stats['files_with_issues'] += 1
            detailed_report.append(file_stats)
    
    # Print summary report
    print("=" * 70)
    print("EXCEPTION INCONSISTENCY REPORT")
    print("=" * 70)
    print(f"\nTotal files analyzed: {len(files)}")
    print(f"Files with issues: {total_stats['files_with_issues']}")
    print(f"Total ValueError instances: {total_stats['ValueError']}")
    print(f"Total RuntimeError instances: {total_stats['RuntimeError']}")
    
    # Print detailed breakdown
    print("\n" + "=" * 70)
    print("DETAILED BREAKDOWN BY FILE")
    print("=" * 70)
    
    for file_stats in sorted(detailed_report, key=lambda x: x['ValueError'] + x['RuntimeError'], reverse=True):
        total = file_stats['ValueError'] + file_stats['RuntimeError']
        print(f"\n{file_stats['name']} ({total} issues)")
        print("-" * 40)
        
        if file_stats['ValueError'] > 0:
            print(f"  ValueError: {file_stats['ValueError']}")
            for error_type, count in file_stats['ValueError_types'].items():
                if error_type == 'not_found':
                    replacement = 'EntityNotFoundError'
                else:
                    replacement = 'EntityValidationError'
                print(f"    - {error_type}: {count} → {replacement}")
        
        if file_stats['RuntimeError'] > 0:
            print(f"  RuntimeError: {file_stats['RuntimeError']}")
            for error_type, count in file_stats['RuntimeError_types'].items():
                if error_type == 'throughput':
                    replacement = 'DynamoDBThroughputError'
                elif error_type == 'server_error':
                    replacement = 'DynamoDBServerError'
                elif error_type == 'access_denied':
                    replacement = 'DynamoDBAccessError'
                else:
                    replacement = 'OperationError'
                print(f"    - {error_type}: {count} → {replacement}")
    
    # Generate fix commands
    print("\n" + "=" * 70)
    print("RECOMMENDED FIX ORDER (by issue count)")
    print("=" * 70)
    
    for i, file_stats in enumerate(sorted(detailed_report, key=lambda x: x['ValueError'] + x['RuntimeError'], reverse=True)):
        total = file_stats['ValueError'] + file_stats['RuntimeError']
        print(f"{i+1:2d}. python fix_exceptions_single.py {file_stats['name']} # {total} issues")


if __name__ == '__main__':
    analyze_all_files()
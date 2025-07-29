#!/usr/bin/env python3
"""
Script to systematically fix exception inconsistencies in receipt_dynamo data files.
"""

import re
import os
from typing import List, Tuple, Dict
from pathlib import Path

# Exception mapping rules
EXCEPTION_MAPPINGS = {
    'ValueError': {
        'parameter_validation': 'EntityValidationError',
        'not_found': 'EntityNotFoundError',
        'invalid_uuid': 'EntityValidationError',
        'default': 'EntityValidationError'
    },
    'RuntimeError': {
        'throughput': 'DynamoDBThroughputError',
        'server_error': 'DynamoDBServerError',
        'access_denied': 'DynamoDBAccessError',
        'default': 'OperationError'
    }
}

# Patterns to identify the type of error
ERROR_PATTERNS = {
    'not_found': [
        r'does not exist',
        r'not found',
        r'No .* found',
        r'cannot find'
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
        r'non-empty'
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


def get_required_imports(file_content: str) -> List[str]:
    """Determine which exceptions need to be imported based on file content."""
    required_imports = set()
    
    # Check for ValueError replacements
    if 'raise ValueError' in file_content:
        required_imports.add('EntityValidationError')
        # Check if any are "not found" errors
        for match in re.finditer(r'raise ValueError\((.*?)\)', file_content, re.DOTALL):
            error_msg = match.group(1)
            if identify_error_type(error_msg) == 'not_found':
                required_imports.add('EntityNotFoundError')
    
    # Check for RuntimeError replacements
    if 'raise RuntimeError' in file_content:
        runtime_errors = re.findall(r'raise RuntimeError\((.*?)\)', file_content, re.DOTALL)
        for error_msg in runtime_errors:
            error_type = identify_error_type(error_msg)
            if error_type == 'throughput':
                required_imports.add('DynamoDBThroughputError')
            elif error_type == 'server_error':
                required_imports.add('DynamoDBServerError')
            elif error_type == 'access_denied':
                required_imports.add('DynamoDBAccessError')
            else:
                required_imports.add('OperationError')
    
    return sorted(list(required_imports))


def update_imports(file_content: str, required_imports: List[str]) -> str:
    """Update the imports section to include required exceptions."""
    if not required_imports:
        return file_content
    
    # Check if shared_exceptions is already imported
    import_match = re.search(
        r'from receipt_dynamo\.data\.shared_exceptions import \((.*?)\)',
        file_content,
        re.DOTALL
    )
    
    if import_match:
        # Add to existing import
        existing_imports = import_match.group(1).strip()
        existing_list = [imp.strip() for imp in existing_imports.split(',')]
        
        # Add new imports that aren't already there
        for imp in required_imports:
            if imp not in existing_list:
                existing_list.append(imp)
        
        # Sort and format
        sorted_imports = sorted(existing_list)
        if len(sorted_imports) == 1:
            new_import = f"from receipt_dynamo.data.shared_exceptions import {sorted_imports[0]}"
        else:
            formatted_imports = ',\n    '.join(sorted_imports)
            new_import = f"from receipt_dynamo.data.shared_exceptions import (\n    {formatted_imports},\n)"
        
        file_content = file_content.replace(
            f"from receipt_dynamo.data.shared_exceptions import ({existing_imports})",
            new_import
        )
    else:
        # Add new import after other imports
        # Find the last import statement
        import_lines = []
        lines = file_content.split('\n')
        last_import_idx = 0
        
        for i, line in enumerate(lines):
            if line.startswith('import ') or line.startswith('from '):
                last_import_idx = i
        
        # Insert the new import after the last import
        if len(required_imports) == 1:
            new_import = f"from receipt_dynamo.data.shared_exceptions import {required_imports[0]}"
        else:
            formatted_imports = ',\n    '.join(required_imports)
            new_import = f"from receipt_dynamo.data.shared_exceptions import (\n    {formatted_imports},\n)"
        
        lines.insert(last_import_idx + 1, new_import)
        file_content = '\n'.join(lines)
    
    return file_content


def fix_valueerror(file_content: str) -> Tuple[str, int]:
    """Replace ValueError with appropriate exceptions."""
    count = 0
    
    # Find all ValueError raises
    pattern = r'raise ValueError\((.*?)\)(?=\s|$)'
    
    def replace_error(match):
        nonlocal count
        count += 1
        error_msg = match.group(1)
        error_type = identify_error_type(error_msg)
        
        if error_type == 'not_found':
            return f'raise EntityNotFoundError({error_msg})'
        else:
            return f'raise EntityValidationError({error_msg})'
    
    # Handle single-line raises
    file_content = re.sub(pattern, replace_error, file_content)
    
    # Handle multi-line raises
    pattern_multiline = r'raise ValueError\(([\s\S]*?)\n\s*\)'
    
    def replace_error_multiline(match):
        nonlocal count
        count += 1
        error_msg = match.group(1)
        error_type = identify_error_type(error_msg)
        
        if error_type == 'not_found':
            return f'raise EntityNotFoundError({error_msg}\n            )'
        else:
            return f'raise EntityValidationError({error_msg}\n            )'
    
    file_content = re.sub(pattern_multiline, replace_error_multiline, file_content)
    
    return file_content, count


def fix_runtimeerror(file_content: str) -> Tuple[str, int]:
    """Replace RuntimeError with appropriate exceptions."""
    count = 0
    
    # Find all RuntimeError raises
    pattern = r'raise RuntimeError\((.*?)\)(?=\s|$)'
    
    def replace_error(match):
        nonlocal count
        count += 1
        error_msg = match.group(1)
        error_type = identify_error_type(error_msg)
        
        if error_type == 'throughput':
            return f'raise DynamoDBThroughputError({error_msg})'
        elif error_type == 'server_error':
            return f'raise DynamoDBServerError({error_msg})'
        elif error_type == 'access_denied':
            return f'raise DynamoDBAccessError({error_msg})'
        else:
            return f'raise OperationError({error_msg})'
    
    file_content = re.sub(pattern, replace_error, file_content)
    
    return file_content, count


def process_file(filepath: Path) -> Dict[str, int]:
    """Process a single file and fix exceptions."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    original_content = content
    
    # Get required imports before making changes
    required_imports = get_required_imports(content)
    
    # Fix exceptions
    content, valueerror_count = fix_valueerror(content)
    content, runtimeerror_count = fix_runtimeerror(content)
    
    # Update imports if changes were made
    if valueerror_count > 0 or runtimeerror_count > 0:
        content = update_imports(content, required_imports)
    
    # Write back if changed
    if content != original_content:
        with open(filepath, 'w') as f:
            f.write(content)
    
    return {
        'ValueError': valueerror_count,
        'RuntimeError': runtimeerror_count
    }


def main():
    """Main function to process all data files."""
    data_dir = Path(__file__).parent / 'receipt_dynamo' / 'data'
    
    # Get all Python files in data directory (excluding __init__.py and base_operations)
    files = [f for f in data_dir.glob('_*.py') if f.is_file()]
    
    print(f"Found {len(files)} files to process")
    
    total_stats = {'ValueError': 0, 'RuntimeError': 0}
    
    for filepath in sorted(files):
        print(f"\nProcessing {filepath.name}...")
        stats = process_file(filepath)
        
        if stats['ValueError'] > 0 or stats['RuntimeError'] > 0:
            print(f"  Fixed: {stats['ValueError']} ValueError, {stats['RuntimeError']} RuntimeError")
            total_stats['ValueError'] += stats['ValueError']
            total_stats['RuntimeError'] += stats['RuntimeError']
        else:
            print("  No changes needed")
    
    print(f"\n{'='*50}")
    print(f"Total fixes: {total_stats['ValueError']} ValueError, {total_stats['RuntimeError']} RuntimeError")


if __name__ == '__main__':
    main()
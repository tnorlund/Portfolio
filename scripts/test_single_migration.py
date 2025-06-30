#!/usr/bin/env python3
"""
Test migration on a single file first.
"""

import re
import sys
from pathlib import Path


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def get_dynamo_client_methods():
    """Get all actual methods from DynamoClient."""
    from receipt_dynamo import DynamoClient
    methods = set()
    for method_name in dir(DynamoClient):
        if not method_name.startswith('_'):
            methods.add(method_name)
    return methods


def test_migration_on_file(file_path: str):
    """Test migration on a single file."""
    test_file = Path(file_path)
    if not test_file.exists():
        print(f"File not found: {file_path}")
        return
    
    # Get actual methods
    actual_methods = get_dynamo_client_methods()
    
    # Read file
    content = test_file.read_text()
    original_content = content
    
    # Find camelCase methods
    camel_methods = re.findall(r'\.(add[A-Z][a-zA-Z]*|get[A-Z][a-zA-Z]*|update[A-Z][a-zA-Z]*|delete[A-Z][a-zA-Z]*|list[A-Z][a-zA-Z]*|query[A-Z][a-zA-Z]*)', content)
    
    print(f"Found {len(set(camel_methods))} unique camelCase methods:")
    for method in sorted(set(camel_methods)):
        snake_method = camel_to_snake(method)
        exists = snake_method in actual_methods
        print(f"  {method} -> {snake_method} {'✅' if exists else '❌'}")
    
    # Create mapping for valid methods only
    mapping = {}
    for camel_method in set(camel_methods):
        snake_method = camel_to_snake(camel_method)
        if snake_method in actual_methods:
            mapping[camel_method] = snake_method
    
    print(f"\nApplying {len(mapping)} valid mappings...")
    
    # Apply migration
    changes_made = 0
    for camel_method, snake_method in mapping.items():
        pattern = rf'(\.)({re.escape(camel_method)})(?![a-zA-Z_])'
        replacement = rf'\1{snake_method}'
        
        new_content, count = re.subn(pattern, replacement, content)
        if count > 0:
            changes_made += count
            content = new_content
            print(f"  {camel_method} -> {snake_method}: {count} changes")
    
    if changes_made > 0:
        # Write to a new file for testing
        test_output = test_file.with_suffix('.migrated.py')
        test_output.write_text(content)
        print(f"\n✅ Migrated version saved to: {test_output}")
        print(f"Total changes: {changes_made}")
        
        # Show a diff sample
        print("\n📋 Sample of changes:")
        original_lines = original_content.split('\n')
        new_lines = content.split('\n')
        
        for i, (old, new) in enumerate(zip(original_lines, new_lines)):
            if old != new and any(method in old for method in mapping.keys()):
                print(f"  Line {i+1}:")
                print(f"    - {old.strip()}")
                print(f"    + {new.strip()}")
                break
        
        return True
    else:
        print("No changes needed.")
        return False


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python test_single_migration.py <test_file.py>")
        print("Example: python test_single_migration.py tests/integration/test__job.py")
        sys.exit(1)
    
    test_migration_on_file(sys.argv[1])
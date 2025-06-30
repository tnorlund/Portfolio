#!/usr/bin/env python3
"""
Script to migrate camelCase method calls to snake_case in test files.
"""

import re
from pathlib import Path
from typing import Dict, List, Set


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    # Insert an underscore before any uppercase letter that follows a lowercase letter
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    # Insert an underscore before any uppercase letter that follows a lowercase letter or digit
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def get_dynamo_client_methods() -> Set[str]:
    """Get all actual methods from DynamoClient."""
    try:
        from receipt_dynamo import DynamoClient
        methods = set()
        for method_name in dir(DynamoClient):
            if not method_name.startswith('_'):
                methods.add(method_name)
        return methods
    except ImportError:
        print("Warning: Could not import DynamoClient, using manual method list")
        return set()


def build_camel_to_snake_mapping() -> Dict[str, str]:
    """Build mapping from camelCase to snake_case method names."""
    actual_methods = get_dynamo_client_methods()
    
    # Find all camelCase patterns in test files
    test_dir = Path("/Users/tnorlund/GitHub/example/receipt_dynamo/tests/integration")
    camel_patterns = set()
    
    for test_file in test_dir.glob("*.py"):
        content = test_file.read_text()
        # Find patterns like .addJob, .getReceipt, etc.
        matches = re.findall(r'\.(add[A-Z][a-zA-Z]*|get[A-Z][a-zA-Z]*|update[A-Z][a-zA-Z]*|delete[A-Z][a-zA-Z]*|list[A-Z][a-zA-Z]*|query[A-Z][a-zA-Z]*)', content)
        for match in matches:
            camel_patterns.add(match)
    
    # Create mapping
    mapping = {}
    for camel_method in camel_patterns:
        snake_method = camel_to_snake(camel_method)
        
        # Verify the snake_case method exists
        if snake_method in actual_methods:
            mapping[camel_method] = snake_method
        else:
            print(f"Warning: {camel_method} -> {snake_method} not found in DynamoClient")
    
    return mapping


def analyze_test_files() -> Dict[str, List[str]]:
    """Analyze test files to see which methods they use."""
    test_dir = Path("/Users/tnorlund/GitHub/example/receipt_dynamo/tests/integration")
    file_analysis = {}
    
    for test_file in test_dir.glob("*.py"):
        content = test_file.read_text()
        camel_methods = re.findall(r'\.(add[A-Z][a-zA-Z]*|get[A-Z][a-zA-Z]*|update[A-Z][a-zA-Z]*|delete[A-Z][a-zA-Z]*|list[A-Z][a-zA-Z]*|query[A-Z][a-zA-Z]*)', content)
        if camel_methods:
            file_analysis[str(test_file)] = list(set(camel_methods))
    
    return file_analysis


def migrate_file(file_path: Path, mapping: Dict[str, str]) -> bool:
    """Migrate a single test file from camelCase to snake_case."""
    try:
        content = file_path.read_text()
        original_content = content
        changes_made = 0
        
        for camel_method, snake_method in mapping.items():
            # Create pattern that matches .camelMethod but not .camelMethod_something
            pattern = rf'(\.)({re.escape(camel_method)})(?![a-zA-Z_])'
            replacement = rf'\1{snake_method}'
            
            new_content, count = re.subn(pattern, replacement, content)
            if count > 0:
                changes_made += count
                content = new_content
                print(f"  {camel_method} -> {snake_method}: {count} changes")
        
        if content != original_content:
            file_path.write_text(content)
            print(f"✅ Updated {file_path}: {changes_made} total changes")
            return True
        else:
            print(f"⏭️  No changes needed for {file_path}")
            return False
            
    except Exception as e:
        print(f"❌ Error processing {file_path}: {e}")
        return False


def preview_migration(file_path: Path, mapping: Dict[str, str]) -> List[str]:
    """Preview what changes would be made to a file."""
    content = file_path.read_text()
    changes = []
    
    for camel_method, snake_method in mapping.items():
        pattern = rf'(\.)({re.escape(camel_method)})(?![a-zA-Z_])'
        matches = re.findall(pattern, content)
        if matches:
            count = len(matches)
            changes.append(f"  {camel_method} -> {snake_method}: {count} occurrences")
    
    return changes


def main():
    """Main migration function."""
    print("🔍 Analyzing camelCase to snake_case migration for receipt_dynamo tests...")
    
    # Build mapping
    print("\n📋 Building method mapping...")
    mapping = build_camel_to_snake_mapping()
    print(f"Found {len(mapping)} method mappings")
    
    # Show mapping
    print("\n📊 Method Mapping:")
    for camel, snake in sorted(mapping.items())[:10]:
        print(f"  {camel} -> {snake}")
    if len(mapping) > 10:
        print(f"  ... and {len(mapping) - 10} more")
    
    # Analyze files
    print("\n🔍 Analyzing test files...")
    file_analysis = analyze_test_files()
    print(f"Found {len(file_analysis)} files with camelCase methods")
    
    # Preview changes
    print("\n🔍 Preview of changes:")
    test_dir = Path("/Users/tnorlund/GitHub/example/receipt_dynamo/tests/integration")
    total_files_to_change = 0
    
    for test_file in test_dir.glob("*.py"):
        changes = preview_migration(test_file, mapping)
        if changes:
            total_files_to_change += 1
            print(f"\n{test_file.name}:")
            for change in changes[:5]:  # Show first 5 changes
                print(change)
            if len(changes) > 5:
                print(f"  ... and {len(changes) - 5} more changes")
    
    print(f"\n📊 Summary: {total_files_to_change} files need migration")
    
    # Perform migration
    print("\n🔧 Performing migration...")
    updated_files = 0
    
    for test_file in sorted(test_dir.glob("*.py")):
        if migrate_file(test_file, mapping):
            updated_files += 1
    
    print(f"\n✨ Migration complete! Updated {updated_files} files")
    print("\n🧪 Recommended next steps:")
    print("1. Run a few tests to verify the migration worked")
    print("2. Check for any remaining failures")
    print("3. Commit the changes")


if __name__ == "__main__":
    main()
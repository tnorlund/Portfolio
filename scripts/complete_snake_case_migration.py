#!/usr/bin/env python3
"""
Complete the camelCase to snake_case migration by updating test function names and comments.
"""

import re
from pathlib import Path
from typing import Dict, List, Set, Tuple


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
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
        print("Warning: Could not import DynamoClient, using empty set")
        return set()


def find_camel_case_patterns(content: str) -> Dict[str, str]:
    """Find all camelCase patterns that need to be migrated."""
    patterns = {}
    
    # Test function names: def test_addJob_success -> def test_add_job_success
    test_func_matches = re.findall(r'def (test_[a-z][a-zA-Z0-9_]*[A-Z][a-zA-Z0-9_]*)', content)
    for match in test_func_matches:
        snake_version = camel_to_snake(match)
        patterns[match] = snake_version
    
    # Comment headers: # addJob -> # add_job
    comment_matches = re.findall(r'#\s+(add[A-Z][a-zA-Z]*|get[A-Z][a-zA-Z]*|update[A-Z][a-zA-Z]*|delete[A-Z][a-zA-Z]*|list[A-Z][a-zA-Z]*|query[A-Z][a-zA-Z]*)', content)
    for match in comment_matches:
        snake_version = camel_to_snake(match)
        patterns[match] = snake_version
    
    return patterns


def migrate_file_comprehensive(file_path: Path) -> bool:
    """Comprehensively migrate a test file from camelCase to snake_case."""
    try:
        content = file_path.read_text()
        original_content = content
        changes_made = 0
        
        # Find all camelCase patterns
        patterns = find_camel_case_patterns(content)
        
        if not patterns:
            return False
        
        print(f"\n📝 Processing {file_path.name}...")
        
        # Apply each pattern replacement
        for camel_pattern, snake_pattern in patterns.items():
            # For test function names, replace the entire function name
            if camel_pattern.startswith('test_'):
                pattern = rf'\bdef {re.escape(camel_pattern)}\b'
                replacement = f'def {snake_pattern}'
                new_content, count = re.subn(pattern, replacement, content)
                if count > 0:
                    changes_made += count
                    content = new_content
                    print(f"  Function: {camel_pattern} -> {snake_pattern}: {count} changes")
            
            # For comments, replace the method name
            else:
                # Look for the pattern in comments
                pattern = rf'(#\s+){re.escape(camel_pattern)}\b'
                replacement = rf'\1{snake_pattern}'
                new_content, count = re.subn(pattern, replacement, content)
                if count > 0:
                    changes_made += count
                    content = new_content
                    print(f"  Comment: {camel_pattern} -> {snake_pattern}: {count} changes")
        
        if content != original_content:
            file_path.write_text(content)
            print(f"✅ Updated {file_path.name}: {changes_made} total changes")
            return True
        else:
            return False
            
    except Exception as e:
        print(f"❌ Error processing {file_path}: {e}")
        return False


def analyze_migration_needed() -> List[Tuple[Path, Dict[str, str]]]:
    """Analyze which files need migration and what patterns they contain."""
    test_dir = Path("/Users/tnorlund/GitHub/example/receipt_dynamo/tests/integration")
    files_needing_migration = []
    
    for test_file in test_dir.glob("*.py"):
        if test_file.name.endswith('.migrated.py'):
            continue  # Skip previously migrated files
            
        content = test_file.read_text()
        patterns = find_camel_case_patterns(content)
        
        if patterns:
            files_needing_migration.append((test_file, patterns))
    
    return files_needing_migration


def main():
    """Main migration function."""
    print("🔍 Analyzing remaining camelCase patterns in test files...")
    
    # Analyze what needs migration
    files_to_migrate = analyze_migration_needed()
    
    if not files_to_migrate:
        print("✅ No camelCase patterns found - migration already complete!")
        return
    
    print(f"\n📊 Found {len(files_to_migrate)} files with camelCase patterns")
    
    # Show preview
    print("\n🔍 Preview of patterns to migrate:")
    total_patterns = 0
    for file_path, patterns in files_to_migrate:
        print(f"\n{file_path.name}:")
        for camel, snake in list(patterns.items())[:5]:  # Show first 5
            print(f"  {camel} -> {snake}")
        if len(patterns) > 5:
            print(f"  ... and {len(patterns) - 5} more")
        total_patterns += len(patterns)
    
    print(f"\n📊 Total patterns to migrate: {total_patterns}")
    
    # Proceed with migration automatically
    print("\n🚀 Proceeding with migration...")
    
    # Perform migration
    print("\n🔧 Performing comprehensive migration...")
    updated_files = 0
    
    for file_path, patterns in files_to_migrate:
        if migrate_file_comprehensive(file_path):
            updated_files += 1
    
    print(f"\n✨ Migration complete! Updated {updated_files} files")
    print("\n🧪 Recommended next steps:")
    print("1. Run tests to verify the migration worked")
    print("2. Check for any import or reference issues")
    print("3. Commit the changes")


if __name__ == "__main__":
    main()
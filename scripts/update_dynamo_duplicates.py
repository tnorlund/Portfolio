#!/usr/bin/env python3
"""
Script to update duplicate code in receipt_dynamo package to use shared utilities.
"""

import os
import re
from pathlib import Path
from typing import List, Tuple


def find_files_with_validate_last_evaluated_key(base_path: Path) -> List[Path]:
    """Find all files that have the duplicate validate_last_evaluated_key function."""
    files = []
    pattern = re.compile(r'def validate_last_evaluated_key\(lek: dict\) -> None:')
    
    for file_path in base_path.rglob("*.py"):
        if file_path.name == "dynamo_helpers.py":
            continue
            
        try:
            content = file_path.read_text()
            if pattern.search(content):
                files.append(file_path)
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
    
    return files


def update_file_imports(file_path: Path, content: str) -> str:
    """Add the necessary imports from dynamo_helpers."""
    # Check if already has the import
    if "from receipt_dynamo.utils.dynamo_helpers import" in content:
        return content
    
    # Find the last import statement
    import_lines = []
    lines = content.split('\n')
    last_import_idx = -1
    
    for i, line in enumerate(lines):
        if line.startswith('from ') or line.startswith('import '):
            last_import_idx = i
    
    # Insert the new import after the last import
    if last_import_idx >= 0:
        lines.insert(last_import_idx + 1, """from receipt_dynamo.utils.dynamo_helpers import (
    validate_last_evaluated_key,
    batch_write_items,
    handle_conditional_check_failed,
)""")
    
    return '\n'.join(lines)


def remove_duplicate_function(content: str) -> str:
    """Remove the duplicate validate_last_evaluated_key function."""
    # Pattern to match the entire function
    pattern = re.compile(
        r'def validate_last_evaluated_key\(lek: dict\) -> None:.*?'
        r'(?=\n(?:def|class|\Z))',
        re.DOTALL | re.MULTILINE
    )
    
    # Remove the function and any trailing empty lines
    content = pattern.sub('', content)
    content = re.sub(r'\n\n\n+', '\n\n', content)
    
    return content


def update_batch_write_pattern(content: str, entity_type: str) -> str:
    """Update batch write patterns to use the shared helper."""
    # Pattern to find batch write methods
    pattern = re.compile(
        rf'def add_{entity_type}s\(self, {entity_type}s: list\[.*?\]\):.*?'
        r'for i in range\(0, len\(.*?\), 25\):.*?'
        r'while unprocessed\.get\(self\.table_name\):.*?'
        r'(?=\n    def )',
        re.DOTALL | re.MULTILINE
    )
    
    # Find the method and extract key parts
    match = pattern.search(content)
    if not match:
        return content
    
    method_text = match.group(0)
    
    # Extract method signature
    sig_match = re.search(
        rf'(def add_{entity_type}s\(self, {entity_type}s: list\[.*?\]\):.*?""".*?""")',
        method_text,
        re.DOTALL
    )
    
    if not sig_match:
        return content
    
    signature = sig_match.group(1)
    
    # Extract validation logic
    validation_lines = []
    for line in method_text.split('\n'):
        if 'if ' in line and ('is None' in line or 'isinstance' in line):
            validation_lines.append(line)
        elif validation_lines and line.strip().startswith('raise'):
            validation_lines.append(line)
    
    # Build the new method
    new_method = f"""{signature}
{chr(10).join(validation_lines)}
        try:
            batch_write_items(self._client, self.table_name, {entity_type}s)
        except Exception as e:
            # Map generic exceptions to specific DynamoDB errors
            if "ProvisionedThroughputExceededException" in str(e):
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {{e}}"
                ) from e
            elif "InternalServerError" in str(e):
                raise DynamoDBServerError(f"Internal server error: {{e}}") from e
            elif "ValidationException" in str(e):
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {{e}}"
                ) from e
            elif "AccessDeniedException" in str(e):
                raise DynamoDBAccessError(f"Access denied: {{e}}") from e
            else:
                raise ValueError(f"Error adding {entity_type}s: {{e}}") from e"""
    
    content = pattern.sub(new_method, content)
    
    return content


def process_file(file_path: Path) -> bool:
    """Process a single file to remove duplicates and use shared utilities."""
    try:
        content = file_path.read_text()
        original_content = content
        
        # Add imports
        content = update_file_imports(file_path, content)
        
        # Remove duplicate function
        content = remove_duplicate_function(content)
        
        # Update batch write patterns - try to detect entity type
        # Look for class names like _Job, _Receipt, etc.
        class_match = re.search(r'class _(\w+)\(', content)
        if class_match:
            entity_type = class_match.group(1).lower()
            content = update_batch_write_pattern(content, entity_type)
        
        # Write back if changed
        if content != original_content:
            file_path.write_text(content)
            print(f"✅ Updated {file_path}")
            return True
        else:
            print(f"⏭️  No changes needed for {file_path}")
            return False
            
    except Exception as e:
        print(f"❌ Error processing {file_path}: {e}")
        return False


def main():
    """Main function to update all files with duplicate code."""
    base_path = Path("/Users/tnorlund/GitHub/example/receipt_dynamo")
    
    print("🔍 Finding files with duplicate validate_last_evaluated_key...")
    files = find_files_with_validate_last_evaluated_key(base_path)
    
    print(f"\nFound {len(files)} files with duplicate code:")
    for f in files:
        print(f"  - {f.relative_to(base_path)}")
    
    print("\n🔧 Updating files...")
    updated_count = 0
    
    for file_path in files:
        if process_file(file_path):
            updated_count += 1
    
    print(f"\n✨ Updated {updated_count}/{len(files)} files")
    
    # Find files that might have batch write patterns
    print("\n🔍 Looking for additional batch write patterns...")
    batch_pattern = re.compile(r'for i in range\(0, len\(.*?\), 25\):')
    
    additional_files = []
    for file_path in base_path.rglob("*.py"):
        if file_path in files or file_path.name == "dynamo_helpers.py":
            continue
            
        try:
            content = file_path.read_text()
            if batch_pattern.search(content):
                additional_files.append(file_path)
        except Exception:
            pass
    
    if additional_files:
        print(f"\nFound {len(additional_files)} additional files with batch patterns:")
        for f in additional_files:
            print(f"  - {f.relative_to(base_path)}")
    
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
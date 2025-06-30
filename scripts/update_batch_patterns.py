#!/usr/bin/env python3
"""
Script to update batch write patterns in receipt_dynamo package to use shared utilities.
"""

import os
import re
from pathlib import Path
from typing import List, Optional, Tuple


def detect_entity_type_from_class(content: str) -> Optional[str]:
    """Detect entity type from class name."""
    # Look for class names like _Job, _Receipt, etc.
    class_match = re.search(r'class _(\w+)\(', content)
    if class_match:
        return class_match.group(1).lower()
    return None


def detect_entity_type_from_method(content: str) -> Optional[str]:
    """Detect entity type from method names."""
    # Look for methods like add_jobs, add_receipts, etc.
    method_match = re.search(r'def add_(\w+)s\(self,', content)
    if method_match:
        return method_match.group(1)
    return None


def has_batch_write_pattern(content: str) -> bool:
    """Check if file has batch write pattern."""
    return bool(re.search(r'for i in range\(0, len\(.*?\), 25\):', content))


def add_batch_write_import(content: str) -> str:
    """Add batch_write_items import if not present."""
    # Check if already has the import
    if "batch_write_items" in content:
        return content
    
    # Check if already has dynamo_helpers import
    if "from receipt_dynamo.utils.dynamo_helpers import" in content:
        # Add to existing import
        import_match = re.search(
            r'(from receipt_dynamo\.utils\.dynamo_helpers import \()(.*?)(\))',
            content,
            re.DOTALL
        )
        if import_match:
            imports = import_match.group(2).strip().split(',')
            imports = [i.strip() for i in imports]
            if "batch_write_items" not in imports:
                imports.append("batch_write_items")
                imports.sort()
                new_import = f"{import_match.group(1)}\n    {',\n    '.join(imports)}\n{import_match.group(3)}"
                content = content.replace(import_match.group(0), new_import)
        return content
    
    # Find the last import statement
    lines = content.split('\n')
    last_import_idx = -1
    
    for i, line in enumerate(lines):
        if line.startswith('from ') or line.startswith('import '):
            last_import_idx = i
    
    # Insert the new import after the last import
    if last_import_idx >= 0:
        lines.insert(last_import_idx + 1, """from receipt_dynamo.utils.dynamo_helpers import batch_write_items""")
    
    return '\n'.join(lines)


def update_batch_write_method(content: str, entity_type: str) -> str:
    """Update batch write method to use shared helper."""
    # Find the batch write method
    pattern = re.compile(
        rf'(def add_{entity_type}s\(self, {entity_type}s: list\[.*?\]\):.*?'
        r'""".*?""".*?)'
        r'(.*?)'
        r'(try:.*?)'
        r'for i in range\(0, len\(.*?\), 25\):.*?'
        r'while unprocessed\.get\(self\.table_name\):.*?'
        r'unprocessed = response\.get\("UnprocessedItems", \{\}\).*?'
        r'(except ClientError as e:.*?)'
        r'(?=\n    def |\Z)',
        re.DOTALL | re.MULTILINE
    )
    
    match = pattern.search(content)
    if not match:
        return content
    
    signature = match.group(1)
    validation = match.group(2).strip()
    exception_handling = match.group(4).strip()
    
    # Build the new method
    new_method = f"""{signature}
        {validation}
        try:
            batch_write_items(self._client, self.table_name, {entity_type}s)
        {exception_handling}"""
    
    # Clean up the exception handling to map to generic exceptions
    new_method = re.sub(
        r'except ClientError as e:',
        'except Exception as e:',
        new_method
    )
    
    # Update error code checks to string checks
    new_method = re.sub(
        r'error_code = e\.response\.get\("Error", \{\}\)\.get\("Code", ""\)',
        '# Map generic exceptions to specific DynamoDB errors',
        new_method
    )
    
    new_method = re.sub(
        r'if error_code == "(\w+)":',
        r'if "\1" in str(e):',
        new_method
    )
    
    new_method = re.sub(
        r'elif error_code == "(\w+)":',
        r'elif "\1" in str(e):',
        new_method
    )
    
    content = pattern.sub(new_method, content)
    
    return content


def process_file(file_path: Path) -> bool:
    """Process a single file to update batch patterns."""
    try:
        content = file_path.read_text()
        original_content = content
        
        # Skip if no batch pattern
        if not has_batch_write_pattern(content):
            return False
        
        # Detect entity type
        entity_type = detect_entity_type_from_class(content)
        if not entity_type:
            entity_type = detect_entity_type_from_method(content)
        
        if not entity_type:
            print(f"⚠️  Could not detect entity type for {file_path}")
            return False
        
        # Add imports
        content = add_batch_write_import(content)
        
        # Update batch write method
        content = update_batch_write_method(content, entity_type)
        
        # Write back if changed
        if content != original_content:
            file_path.write_text(content)
            print(f"✅ Updated {file_path} (entity: {entity_type})")
            return True
        else:
            print(f"⏭️  No changes needed for {file_path}")
            return False
            
    except Exception as e:
        print(f"❌ Error processing {file_path}: {e}")
        return False


def main():
    """Main function to update batch write patterns."""
    base_path = Path("/Users/tnorlund/GitHub/example/receipt_dynamo/receipt_dynamo/data")
    
    # List of files to process (from the previous script output)
    files_to_process = [
        "_receipt_validation_category.py",
        "_label_count_cache.py",
        "_ocr_routing_decision.py",
        "_receipt_line_item_analysis.py",
        "_ocr_job.py",
        "_receipt_validation_summary.py",
        "_receipt_chatgpt_validation.py",
        "_receipt_metadata.py",
        "_receipt_letter.py",
        "_receipt_structure_analysis.py",
        "_receipt_word.py",
        "_receipt_validation_result.py",
        "_ai_usage_metric.py",
    ]
    
    print("🔧 Updating batch write patterns...")
    updated_count = 0
    
    for file_name in files_to_process:
        file_path = base_path / file_name
        if file_path.exists():
            if process_file(file_path):
                updated_count += 1
        else:
            print(f"⚠️  File not found: {file_path}")
    
    print(f"\n✨ Updated {updated_count}/{len(files_to_process)} files")
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
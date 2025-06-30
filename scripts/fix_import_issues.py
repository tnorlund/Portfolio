#!/usr/bin/env python3
"""
Fix import placement issues in receipt_dynamo data files.
"""

import re
from pathlib import Path


def fix_import_placement(file_path: Path) -> bool:
    """Fix misplaced imports in a file."""
    try:
        content = file_path.read_text()
        original_content = content
        
        # Pattern to find misplaced imports
        pattern = re.compile(
            r'(from receipt_dynamo\.entities\.\w+ import \()\n'
            r'(from receipt_dynamo\.utils\.dynamo_helpers import .*?\))\n'
            r'(\s+.*?\))',
            re.DOTALL
        )
        
        # Fix the pattern
        def replace_func(match):
            entities_import_start = match.group(1)
            dynamo_helpers_import = match.group(2)
            entities_import_rest = match.group(3)
            
            # Reconstruct properly
            return f"{entities_import_start}\n{entities_import_rest}\n{dynamo_helpers_import}"
        
        content = pattern.sub(replace_func, content)
        
        # Write back if changed
        if content != original_content:
            file_path.write_text(content)
            print(f"✅ Fixed imports in {file_path}")
            return True
        else:
            return False
            
    except Exception as e:
        print(f"❌ Error processing {file_path}: {e}")
        return False


def main():
    """Main function to fix import issues."""
    base_path = Path("/Users/tnorlund/GitHub/example/receipt_dynamo/receipt_dynamo/data")
    
    print("🔍 Finding files with import issues...")
    
    fixed_count = 0
    
    for file_path in base_path.glob("*.py"):
        if fix_import_placement(file_path):
            fixed_count += 1
    
    print(f"\n✨ Fixed {fixed_count} files")
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
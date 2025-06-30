#!/usr/bin/env python3
"""
Fix the bugs identified by the bug bot in the PR.
"""

import re
from pathlib import Path


def fix_undefined_exception_variables():
    """Fix 'from e' clauses where e is not defined."""
    files_to_fix = [
        "receipt_dynamo/receipt_dynamo/data/_receipt_metadata.py",
        "receipt_dynamo/receipt_dynamo/data/_ocr_routing_decision.py",
    ]
    
    for file_path in files_to_fix:
        full_path = Path(f"/Users/tnorlund/GitHub/example/{file_path}")
        if full_path.exists():
            content = full_path.read_text()
            
            # Remove ' from e' patterns where e is not defined in scope
            # This pattern matches ValueError statements with ' from e'
            original_content = content
            content = re.sub(r'raise ValueError\(([^)]+)\) from e', r'raise ValueError(\1)', content)
            
            if content != original_content:
                full_path.write_text(content)
                print(f"✅ Fixed undefined 'from e' clauses in {file_path}")
            else:
                print(f"⏭️  No undefined 'from e' clauses found in {file_path}")


def fix_dynamodb_query_typos():
    """Fix DynamoDB query typos with stray 'f' characters."""
    file_path = "receipt_dynamo/receipt_dynamo/data/_receipt_line_item_analysis.py"
    full_path = Path(f"/Users/tnorlund/GitHub/example/{file_path}")
    
    if full_path.exists():
        content = full_path.read_text()
        original_content = content
        changes_made = []
        
        # Fix: begins_with(SK, :skPrefix)f -> begins_with(SK, :skPrefix)
        new_content = re.sub(r'begins_with\(SK, :skPrefix\)f', 'begins_with(SK, :skPrefix)', content)
        if new_content != content:
            changes_made.append("Fixed KeyConditionExpression trailing 'f'")
            content = new_content
        
        # Fix: "SKf" -> "SK"
        new_content = re.sub(r'"SKf"', '"SK"', content)
        if new_content != content:
            changes_made.append("Fixed 'SKf' -> 'SK'")
            content = new_content
        
        # Fix: ExpressionAttributeValuesf -> ExpressionAttributeValues
        new_content = re.sub(r'ExpressionAttributeValuesf', 'ExpressionAttributeValues', content)
        if new_content != content:
            changes_made.append("Fixed 'ExpressionAttributeValuesf'")
            content = new_content
        
        # Fix: ":skPrefixf" -> ":skPrefix"
        new_content = re.sub(r'":skPrefixf"', '":skPrefix"', content)
        if new_content != content:
            changes_made.append("Fixed ':skPrefixf' -> ':skPrefix'")
            content = new_content
        
        if content != original_content:
            full_path.write_text(content)
            print(f"✅ Fixed DynamoDB typos in {file_path}:")
            for change in changes_made:
                print(f"    - {change}")
        else:
            print(f"⏭️  No DynamoDB typos found in {file_path}")


def main():
    """Main function to fix all bug bot issues."""
    print("🔧 Fixing bugs identified by bug bot...")
    
    print("\n1. Fixing undefined exception variables...")
    fix_undefined_exception_variables()
    
    print("\n2. Fixing DynamoDB query typos...")
    fix_dynamodb_query_typos()
    
    print("\n✨ Bug fixes complete!")


if __name__ == "__main__":
    main()
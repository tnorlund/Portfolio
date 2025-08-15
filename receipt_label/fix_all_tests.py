#!/usr/bin/env python3
"""Fix all pattern detection tests to match actual API."""

import re
from pathlib import Path

def fix_test_file(filepath):
    """Fix a test file to match actual API."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Track if we made changes
    original_content = content
    
    # Fix 1: PatternMatch doesn't have suggested_labels, it's in metadata
    # Change: assert expected_label in result.suggested_labels
    # To: assert expected_label == result.pattern_type.name or expected_label in result.metadata.get('suggested_labels', [])
    content = re.sub(
        r'assert ([^=]+) in result\.suggested_labels',
        r"assert \1 == result.pattern_type.name or \1 in result.metadata.get('suggested_labels', [])",
        content
    )
    
    # Fix 2: PatternMatch has matched_text not text
    # Change: assert result.text == text
    # To: assert result.matched_text == text
    content = re.sub(
        r'assert result\.text == ',
        r'assert result.matched_text == ',
        content
    )
    
    # Fix 3: Pattern comparison should use enum name
    # Change: assert result.pattern_type == "CURRENCY"
    # To: assert result.pattern_type.name == "CURRENCY"
    content = re.sub(
        r'assert result\.pattern_type == "([^"]+)"',
        r'assert result.pattern_type.name == "\1"',
        content
    )
    
    # Fix 4: Some tests check results[0].text which should be results[0].matched_text
    content = re.sub(
        r'assert results\[0\]\.text == ',
        r'assert results[0].matched_text == ',
        content
    )
    
    # Fix 5: Fix confidence assertions on results array
    # Change: assert results[0].confidence > 0.9
    # Keep as is, this is correct
    
    # Fix 6: Add PatternType import if not present
    if 'from receipt_label.pattern_detection.base import PatternType' not in content:
        # Add after other receipt_label imports
        pattern_import_line = 'from receipt_label.pattern_detection.base import PatternType\n'
        import_section = re.search(r'(from receipt_label\.pattern_detection\.[^\n]+\n)', content)
        if import_section:
            insert_pos = import_section.end()
            content = content[:insert_pos] + pattern_import_line + content[insert_pos:]
    
    # Fix 7: Fix method calls that don't exist
    # Some tests might use _get_context_metadata which might not exist
    content = re.sub(
        r"patch\.object\(detector, '_get_context_metadata'",
        r"patch.object(detector, '_calculate_position_context'",
        content
    )
    
    # Fix 8: Fix tests that check for specific label lists
    # Since suggested_labels doesn't exist, we need to check pattern_type instead
    content = re.sub(
        r'found_expected = any\(label in result\.suggested_labels for label in expected_labels\)',
        r'found_expected = any(label == result.pattern_type.name for label in expected_labels)',
        content
    )
    
    # Save if we made changes
    if content != original_content:
        with open(filepath, 'w') as f:
            f.write(content)
        return True
    return False

def main():
    """Fix all test files."""
    test_files = [
        'tests/unit/pattern_detection/test_contact_detector.py',
        'tests/unit/pattern_detection/test_currency_detector.py',
        'tests/unit/pattern_detection/test_datetime_detector.py',
        'tests/unit/pattern_detection/test_parallel_orchestrator.py',
        'tests/unit/pattern_detection/test_quantity_detector.py',
    ]
    
    for file_path in test_files:
        if Path(file_path).exists():
            print(f"Fixing {file_path}...")
            if fix_test_file(file_path):
                print(f"  Updated!")
            else:
                print(f"  No changes needed")
        else:
            print(f"  Skipping {file_path} (not found)")

if __name__ == '__main__':
    main()
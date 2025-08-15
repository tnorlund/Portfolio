#!/usr/bin/env python3
"""Fix ReceiptWord constructor usage in test files."""

import re
import sys
from pathlib import Path

def fix_receipt_word_usage(file_path):
    """Fix ReceiptWord constructor usage in a file."""
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Check if file already imports the helper
    has_helper_import = 'from tests.helpers import create_test_receipt_word' in content
    
    # Add import if not present
    if not has_helper_import and 'ReceiptWord(' in content:
        # Find where to add the import
        import_lines = []
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if line.startswith('from receipt_dynamo.entities import'):
                # Add helper import after this line
                import_lines.append(i + 1)
                break
        
        if import_lines:
            lines.insert(import_lines[0], 'from tests.helpers import create_test_receipt_word')
            content = '\n'.join(lines)
    
    # Replace ReceiptWord constructor patterns
    # Pattern 1: Simple constructor with x1, y1, x2, y2
    pattern1 = r'ReceiptWord\(\s*' \
              r'image_id="([^"]+)",?\s*' \
              r'receipt_id=(\d+),?\s*' \
              r'line_id=(\d+),?\s*' \
              r'word_id=(\d+),?\s*' \
              r'text="([^"]+)",?\s*' \
              r'x1=([\d.]+),?\s*y1=([\d.]+),?\s*x2=([\d.]+),?\s*y2=([\d.]+)\s*\)'
    
    replacement1 = r'create_test_receipt_word(\n            text="\5",\n            image_id="\1",\n            receipt_id=\2,\n            line_id=\3,\n            word_id=\4,\n            x1=\6, y1=\7, x2=\8, y2=\9\n        )'
    
    content = re.sub(pattern1, replacement1, content)
    
    # Pattern 2: Constructor with text first
    pattern2 = r'ReceiptWord\(\s*' \
              r'text="([^"]+)",?\s*' \
              r'image_id="([^"]+)",?\s*' \
              r'receipt_id=(\d+),?\s*' \
              r'line_id=(\d+),?\s*' \
              r'word_id=(\d+),?\s*' \
              r'x1=([\d.]+),?\s*y1=([\d.]+),?\s*x2=([\d.]+),?\s*y2=([\d.]+)\s*\)'
    
    replacement2 = r'create_test_receipt_word(\n            text="\1",\n            image_id="\2",\n            receipt_id=\3,\n            line_id=\4,\n            word_id=\5,\n            x1=\6, y1=\7, x2=\8, y2=\9\n        )'
    
    content = re.sub(pattern2, replacement2, content)
    
    # Save the file
    with open(file_path, 'w') as f:
        f.write(content)
    
    return True

def main():
    """Main function."""
    test_files = [
        'tests/unit/pattern_detection/test_contact_detector.py',
        'tests/unit/pattern_detection/test_currency_detector.py', 
        'tests/unit/pattern_detection/test_datetime_detector.py',
        'tests/unit/pattern_detection/test_parallel_orchestrator.py',
        'tests/unit/pattern_detection/test_quantity_detector.py',
        'tests/unit/embedding/test_word_poll.py',
        'tests/unit/embedding/test_word_submit.py',
        'tests/end_to_end/test_receipt_processing_workflow.py'
    ]
    
    for file_path in test_files:
        if Path(file_path).exists():
            print(f"Fixing {file_path}...")
            fix_receipt_word_usage(file_path)
            print(f"  Done!")
        else:
            print(f"  Skipping {file_path} (not found)")

if __name__ == '__main__':
    main()
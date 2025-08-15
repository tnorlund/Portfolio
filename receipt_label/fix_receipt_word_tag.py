#!/usr/bin/env python3
"""Fix ReceiptWordTag to ReceiptWordLabel in test files."""

import re
from pathlib import Path

def fix_receipt_word_tag(file_path):
    """Fix ReceiptWordTag references in a file."""
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Replace ReceiptWordTag with ReceiptWordLabel
    content = content.replace('ReceiptWordTag', 'ReceiptWordLabel')
    
    # Also fix any updateReceiptWordTags or addReceiptWordTags method names
    content = content.replace('updateReceiptWordTags', 'updateReceiptWordLabels')
    content = content.replace('addReceiptWordTags', 'addReceiptWordLabels')
    content = content.replace('getReceiptWordTagsToValidate', 'getReceiptWordLabelsToValidate')
    
    # Save the file
    with open(file_path, 'w') as f:
        f.write(content)
    
    return True

def main():
    """Main function."""
    test_files = [
        'tests/unit/embedding/test_word_poll.py',
        'tests/unit/embedding/test_word_submit.py',
        'tests/unit/completion/test_completion_poll.py',
        'tests/unit/completion/test_completion_submit.py',
        'tests/integration/test_chroma_integration.py',
        'tests/integration/test_end_to_end.py'
    ]
    
    for file_path in test_files:
        if Path(file_path).exists():
            print(f"Fixing {file_path}...")
            fix_receipt_word_tag(file_path)
            print(f"  Done!")
        else:
            print(f"  Skipping {file_path} (not found)")

if __name__ == '__main__':
    main()
#!/usr/bin/env python3
"""Fix protected access warnings in test file."""

import re

# Read the file
with open('tests/integration/test__receipt.py', 'r') as f:
    content = f.read()

# Fix protected-access for mocker.patch.object calls
lines = content.split('\n')
fixed_lines = []
i = 0

while i < len(lines):
    line = lines[i]
    
    # Check if next few lines contain mocker.patch.object with client._client
    if 'mock_' in line and '= mocker.patch.object(' in line:
        # Check if next line has client._client
        if i + 1 < len(lines) and 'client._client' in lines[i + 1]:
            # Add pylint disable if not already there
            indent = len(line) - len(line.lstrip())
            fixed_lines.append(' ' * indent + '# pylint: disable=protected-access')
            fixed_lines.append(line)
        else:
            fixed_lines.append(line)
    
    # Fix the batch_writer()._queue.put case  
    elif 'batch_writer()._queue.put' in line:
        indent = len(line) - len(line.lstrip())
        fixed_lines.append(' ' * indent + '# pylint: disable=protected-access')
        fixed_lines.append(line)
    
    # Fix the unnecessary else after return
    elif line.strip() == 'else:' and i > 0 and 'return' in lines[i-1]:
        # Skip the else and dedent the following block
        i += 1
        base_indent = len(line) - len(line.lstrip()) - 4
        while i < len(lines) and lines[i].strip():
            if lines[i].strip():
                fixed_lines.append(' ' * base_indent + lines[i].strip())
            else:
                fixed_lines.append('')
            i += 1
        continue
    else:
        fixed_lines.append(line)
    
    i += 1

# Fix the long lines
content = '\n'.join(fixed_lines)
content = content.replace(
    '    Tests list_receipt_details returns a page of receipt summaries with words and labels.',
    '    Tests list_receipt_details returns a page of receipt summaries with\n    words and labels.'
)
content = content.replace(
    '    """Tests that list_receipt_and_words raises EntityNotFoundError when receipt does not exist."""',
    '    """Tests that list_receipt_and_words raises EntityNotFoundError when\n    receipt does not exist."""'
)

# Write back
with open('tests/integration/test__receipt.py', 'w') as f:
    f.write(content)

print("Fixed protected access warnings")
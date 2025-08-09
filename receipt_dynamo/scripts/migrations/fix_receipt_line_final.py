#!/usr/bin/env python3
"""Final fix for test__receipt_line_parameterized.py"""

# Read the file
with open('tests/integration/test__receipt_line_parameterized.py', 'r') as f:
    content = f.read()

# Fix the import mess
lines = content.split('\n')
fixed_lines = []

i = 0
while i < len(lines):
    line = lines[i]
    
    # Fix the import section
    if line == 'from receipt_dynamo.constants import EMBEDDING_STATUS':
        fixed_lines.append('from receipt_dynamo.constants import EmbeddingStatus')
        # Skip the next line if it's the bad alias
        if i + 1 < len(lines) and lines[i + 1] == 'from receipt_dynamo.data.shared_exceptions import (':
            i += 1
            fixed_lines.append('from receipt_dynamo.data.shared_exceptions import (')
        elif i + 1 < len(lines) and lines[i + 1] == 'EmbeddingStatus = EMBEDDING_STATUS':
            i += 1  # Skip this line
    elif line == 'EmbeddingStatus = EMBEDDING_STATUS':
        # Skip this line
        pass
    else:
        fixed_lines.append(line)
    i += 1

content = '\n'.join(fixed_lines)

# Fix EmbeddingStatus usage that was mistakenly changed
content = content.replace('embedding_status=EmbeddingStatus.NONE,', 'embedding_status=EmbeddingStatus.NONE,')
content = content.replace('embedding_status=EmbeddingStatus.SUCCESS,', 'embedding_status=EmbeddingStatus.SUCCESS,')
content = content.replace('embedding_status=EmbeddingStatus.FAILED,', 'embedding_status=EmbeddingStatus.FAILED,')

# Add final newline
if not content.endswith('\n'):
    content += '\n'

# Write back
with open('tests/integration/test__receipt_line_parameterized.py', 'w') as f:
    f.write(content)

print("Fixed test__receipt_line_parameterized.py")
#!/usr/bin/env python3
"""Final pylint fixes for test__receipt_line_parameterized.py"""

# Read the file
with open('tests/integration/test__receipt_line_parameterized.py', 'r') as f:
    lines = f.readlines()

# Fix trailing whitespace
fixed_lines = [line.rstrip() + '\n' for line in lines]

# Fix line 69 (too long)
for i, line in enumerate(fixed_lines):
    if i == 68 and 'ResourceNotFoundException' in line:
        fixed_lines[i] = '    ("ResourceNotFoundException", OperationError,\n'
        fixed_lines.insert(i + 1, '     "DynamoDB resource not found"),\n')
        break

# Fix line 1204 if it exists
for i, line in enumerate(fixed_lines):
    if i >= 1203 and 'assert any(l.embedding_status == EmbeddingStatus.NONE' in line:
        if len(line.strip()) > 79:
            fixed_lines[i] = '    assert any(\n'
            fixed_lines.insert(i + 1, '        l.embedding_status == EmbeddingStatus.NONE for l in not_embedded\n')
            fixed_lines.insert(i + 2, '    )\n')
            # Remove the original continuation
            if i + 3 < len(fixed_lines) and 'for l in not_embedded' in fixed_lines[i + 3]:
                del fixed_lines[i + 3]
        break

# Add pylint disable for redefined-outer-name on fixture definitions
for i, line in enumerate(fixed_lines):
    if line.strip().startswith('@pytest.fixture') and i + 1 < len(fixed_lines):
        # Check if the next line defines a function
        next_line = fixed_lines[i + 1]
        if next_line.strip().startswith('def '):
            # Add comment before fixture if not already there
            if i > 0 and 'pylint: disable=redefined-outer-name' not in fixed_lines[i - 1]:
                fixed_lines.insert(i, '# pylint: disable=redefined-outer-name\n')

# Remove the final newline if it's duplicated
if len(fixed_lines) > 1 and fixed_lines[-1].strip() == '' and fixed_lines[-2].strip() == '':
    fixed_lines = fixed_lines[:-1]

# Ensure file ends with exactly one newline
if fixed_lines and not fixed_lines[-1].endswith('\n'):
    fixed_lines[-1] += '\n'

# Write back
with open('tests/integration/test__receipt_line_parameterized.py', 'w') as f:
    f.writelines(fixed_lines)

print("Applied final pylint fixes")
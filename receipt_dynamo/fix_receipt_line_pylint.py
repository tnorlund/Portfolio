#!/usr/bin/env python3
"""Script to fix pylint issues in test__receipt_line_parameterized.py"""

import re

# Read the file
with open('tests/integration/test__receipt_line_parameterized.py', 'r') as f:
    content = f.read()

# Fix the import issue for EmbeddingStatus
content = content.replace(
    'from receipt_dynamo.constants import EmbeddingStatus',
    'from receipt_dynamo.constants import EMBEDDING_STATUS as EmbeddingStatus'
)

# Then check if we need to use the correct import
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'from receipt_dynamo.constants import' in line and 'EmbeddingStatus' in line:
        # Check what's actually available
        lines[i] = 'from receipt_dynamo.constants import EMBEDDING_STATUS'
        # Add alias after imports
        for j in range(i+1, min(i+10, len(lines))):
            if lines[j].strip() == '' or not lines[j].startswith(('from ', 'import ')):
                lines.insert(j, 'EmbeddingStatus = EMBEDDING_STATUS')
                break
        break

content = '\n'.join(lines)

# Don't change the import - EmbeddingStatus is correct
content = content.replace(
    'from receipt_dynamo.constants import EMBEDDING_STATUS as EmbeddingStatus',
    'from receipt_dynamo.constants import EmbeddingStatus'
)

# Fix EmbeddingStatus member access - use the correct enum values
content = content.replace('EmbeddingStatus.NOT_EMBEDDED', 'EmbeddingStatus.NONE')
content = content.replace('EmbeddingStatus.EMBEDDED', 'EmbeddingStatus.SUCCESS')
content = content.replace('EmbeddingStatus.EMBEDDING_FAILED', 'EmbeddingStatus.FAILED')

# Remove unused datetime import
lines = content.split('\n')
for i, line in enumerate(lines):
    if line == 'from datetime import datetime':
        lines[i] = ''
        break
content = '\n'.join(lines)

# Fix trailing whitespace
lines = content.split('\n')
fixed_lines = [line.rstrip() for line in lines]
content = '\n'.join(fixed_lines)

# Fix long lines
lines = content.split('\n')
fixed_lines = []

for i, line in enumerate(lines):
    if len(line) <= 79:
        fixed_lines.append(line)
        continue
    
    # Handle long parameter lines
    if '"ProvisionedThroughputExceededException", DynamoDBThroughputError, "Throughput exceeded"' in line:
        indent = len(line) - len(line.lstrip())
        fixed_lines.append(line[:line.index('"Throughput')] + '(')
        fixed_lines.append(' ' * (indent + 4) + '"Throughput exceeded"')
        fixed_lines.append(' ' * indent + '),')
    elif '"ConditionalCheckFailedException", EntityNotFoundError, "Cannot update receipt lines"' in line:
        indent = len(line) - len(line.lstrip())
        fixed_lines.append(line[:line.index('"Cannot')] + '(')
        fixed_lines.append(' ' * (indent + 4) + '"Cannot update receipt lines"')
        fixed_lines.append(' ' * indent + '),')
    elif 'EmbeddingStatus.NOT_EMBEDDED.value if hasattr(EmbeddingStatus, "NOT_EMBEDDED") else "NOT_EMBEDDED"' in line:
        fixed_lines.append(line)  # Will fix later
    elif '@pytest.mark.parametrize("error_code,expected_exception,error_match", ERROR_SCENARIOS)' in line:
        fixed_lines.append('@pytest.mark.parametrize(')
        fixed_lines.append('    "error_code,expected_exception,error_match", ERROR_SCENARIOS')
        fixed_lines.append(')')
    elif '@pytest.mark.parametrize("method_name,invalid_input,error_match"' in line:
        # This is likely part of a multi-line parametrize
        fixed_lines.append(line)
    elif '"add_receipt_line", "not-a-line", "line must be an instance of ReceiptLine"' in line:
        indent = len(line) - len(line.lstrip())
        fixed_lines.append(line[:line.index('"line must')])
        fixed_lines.append(' ' * (indent + 4) + '"line must be an instance of ReceiptLine"),')
    elif '"update_receipt_line", "not-a-line", "line must be an instance of ReceiptLine"' in line:
        indent = len(line) - len(line.lstrip())
        fixed_lines.append(line[:line.index('"line must')])
        fixed_lines.append(' ' * (indent + 4) + '"line must be an instance of ReceiptLine"),')
    elif 'None, FIXED_UUIDS[0], 1, EntityValidationError, "receipt_id must be an integer"' in line:
        indent = len(line) - len(line.lstrip())
        fixed_lines.append(line[:line.index('EntityValidationError')])
        fixed_lines.append(' ' * (indent + 4) + 'EntityValidationError, "receipt_id must be an integer"),')
    elif '"not-an-int", FIXED_UUIDS[0], 1, EntityValidationError, "receipt_id must be an integer"' in line:
        indent = len(line) - len(line.lstrip())
        fixed_lines.append(line[:line.index('EntityValidationError')])
        fixed_lines.append(' ' * (indent + 4) + 'EntityValidationError, "receipt_id must be an integer"),')
    elif '-1, FIXED_UUIDS[0], 1, EntityValidationError, "receipt_id must be a positive integer"' in line:
        indent = len(line) - len(line.lstrip())
        fixed_lines.append(line[:line.index('EntityValidationError')])
        fixed_lines.append(' ' * (indent + 4) + 'EntityValidationError, "receipt_id must be a positive integer"),')
    elif '1, FIXED_UUIDS[0], "not-an-int", EntityValidationError, "line_id must be an integer"' in line:
        indent = len(line) - len(line.lstrip())
        fixed_lines.append(line[:line.index('EntityValidationError')])
        fixed_lines.append(' ' * (indent + 4) + 'EntityValidationError, "line_id must be an integer"),')
    elif '1, FIXED_UUIDS[0], -1, EntityValidationError, "line_id must be a positive integer"' in line:
        indent = len(line) - len(line.lstrip())
        fixed_lines.append(line[:line.index('EntityValidationError')])
        fixed_lines.append(' ' * (indent + 4) + 'EntityValidationError, "line_id must be a positive integer"),')
    else:
        # For other long lines, try to split at commas
        if ',' in line and line.strip().startswith('('):
            parts = line.split(', ')
            if len(parts) > 1:
                indent = len(line) - len(line.lstrip())
                fixed_lines.append(parts[0] + ',')
                for part in parts[1:]:
                    fixed_lines.append(' ' * (indent + 4) + part + (',' if part != parts[-1] else ''))
            else:
                fixed_lines.append(line)
        else:
            fixed_lines.append(line)

content = '\n'.join(fixed_lines)

# Add pylint disable for redefined-outer-name (normal for pytest fixtures)
lines = content.split('\n')
fixed_lines = []
for i, line in enumerate(lines):
    if line.strip().startswith('def test_') and i < len(lines) - 1:
        # Check if next lines have fixture parameters
        func_def = line
        j = i + 1
        while j < len(lines) and not lines[j].strip().endswith(':'):
            func_def += ' ' + lines[j].strip()
            j += 1
        
        if 'sample_receipt_line:' in func_def or 'unique_image_id:' in func_def:
            # Add pylint disable before the function
            indent = len(line) - len(line.lstrip())
            if i > 0 and 'pylint: disable=' not in lines[i-1]:
                fixed_lines.append(' ' * indent + '# pylint: disable=redefined-outer-name')
    
    fixed_lines.append(line)

content = '\n'.join(fixed_lines)

# Fix unused variable
content = content.replace(
    'lines, last_key = client.list_receipt_lines()',
    'lines, _ = client.list_receipt_lines()'
)

# Write back
with open('tests/integration/test__receipt_line_parameterized.py', 'w') as f:
    f.write(content)

print("Fixed pylint issues in test__receipt_line_parameterized.py")
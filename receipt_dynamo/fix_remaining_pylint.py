#!/usr/bin/env python3
"""Fix remaining pylint issues in test__receipt.py"""

# Read the file
with open('tests/integration/test__receipt.py', 'r') as f:
    lines = f.readlines()

# Fix line 1309 (line too long)
if len(lines) > 1308 and "Throughput exceeded for receipt validation/update operations" in lines[1308]:
    lines[1308] = '            "Throughput exceeded for receipt "\n'
    lines.insert(1309, '            "validation/update operations"\n')

# Fix line 1469 (line too long) 
if len(lines) > 1468 and "Unexpected error during list_receipt_and_words: uuid must be a valid UUIDv4" in lines[1468]:
    lines[1468] = '            "Unexpected error during list_receipt_and_words: uuid "\n'
    lines.insert(1469, '            "must be a valid UUIDv4",\n')

# Fix protected access on line 987
for i in range(len(lines)):
    if i >= 986 and "dynamo_client._client" in lines[i] and "# pylint: disable=protected-access" not in lines[i-1]:
        # Add pylint disable comment
        indent = len(lines[i]) - len(lines[i].lstrip())
        lines.insert(i, ' ' * indent + '# pylint: disable=protected-access\n')
        break

# Fix unnecessary else after return on line 994
for i in range(len(lines)):
    if i >= 993 and lines[i].strip() == "else:" and i > 0 and "return" in lines[i-1]:
        # Remove the else and dedent the block
        lines[i] = ""  # Remove else line
        # Dedent the following block
        j = i + 1
        while j < len(lines) and lines[j].strip():
            if lines[j].startswith("        "):
                lines[j] = lines[j][4:]  # Remove 4 spaces
            j += 1
        break

# Fix protected access on line 1006
for i in range(len(lines)):
    if i >= 1005 and "batch_writer()._queue.put" in lines[i] and "# pylint: disable=protected-access" not in lines[i-1]:
        indent = len(lines[i]) - len(lines[i].lstrip())
        lines.insert(i, ' ' * indent + '# pylint: disable=protected-access\n')
        break

# Write back
with open('tests/integration/test__receipt.py', 'w') as f:
    f.writelines(lines)

print("Fixed remaining pylint issues")
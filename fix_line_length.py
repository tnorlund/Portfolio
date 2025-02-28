#!/usr/bin/env python3

import re

file_path = "receipt_dynamo/tests/integration/test__gpt.py"

# Read the file
with open(file_path, 'r') as f:
    lines = f.readlines()

# Process each line
fixed_lines = []
for line in lines:
    if len(line) > 79:
        # Check if line contains a string in quotes that can be broken
        matches = re.findall(r'(".*?")', line)
        if matches:
            # Found quoted strings, try to break after a comma or space
            for match in matches:
                if len(match) > 30:  # Only split long strings
                    parts = match.split(", ")
                    if len(parts) > 1:
                        # Replace with line breaks after commas
                        replacement = ',\n    '.join(parts)
                        line = line.replace(match, replacement)
        
        # If still too long, try to break at reasonable points
        if len(line) > 79:
            # Try to break error messages and similar strings
            if "match=" in line:
                line = line.replace("match=", "match=\n    ")
            elif " with " in line:
                line = line.replace(" with ", "\n    with ")
            
            # If still too long, just break at 79 chars
            if len(line) > 79:
                indent = len(line) - len(line.lstrip())
                if indent >= 4:  # If there's indentation, respect it
                    part1 = line[:79]
                    part2 = " " * indent + line[79:].lstrip()
                    line = part1 + "\n" + part2
    
    fixed_lines.append(line)

# Write the file back
with open(file_path, 'w') as f:
    f.writelines(fixed_lines)

print("Line length fixes attempted.") 
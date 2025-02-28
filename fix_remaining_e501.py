#!/usr/bin/env python3

import re
import sys

def fix_line(line, line_number, file_path):
    """Fix a specific line that has an E501 error."""
    # Check if it's a string literal that can be split
    if '"' in line or "'" in line:
        # Try to break at logical points
        matches = re.findall(r'"([^"]*)"', line)
        if matches:
            for match in matches:
                if len(match) > 30:  # Only modify long strings
                    if ", " in match:
                        # Break at commas
                        replaced = match.replace(", ", '",\n' + ' ' * (line.find('"')) + '"')
                        line = line.replace(match, replaced)
                    elif " " in match:
                        # Break at spaces, preserving indentation
                        indent = line.find('"')
                        parts = match.split(" ")
                        middle = len(parts) // 2
                        replaced = " ".join(parts[:middle]) + '"\n' + ' ' * indent + '"' + " ".join(parts[middle:])
                        line = line.replace(match, replaced)
    
    # If still too long, try other strategies
    if len(line) > 79:
        # Handle with statement (common in error expectations)
        if "with pytest.raises" in line:
            parts = line.split("with pytest.raises")
            line = parts[0] + "with pytest.raises" + "\n" + " " * len(parts[0]) + parts[1]
        # Check if it's a function call with arguments
        elif "(" in line and ")" in line and "," in line:
            open_paren = line.find("(")
            # Add line breaks after commas
            parts = []
            current_part = line[:open_paren+1]
            rest = line[open_paren+1:]
            in_string = False
            string_char = None
            for char in rest:
                if char in ['"', "'"]:
                    if not in_string:
                        in_string = True
                        string_char = char
                    elif char == string_char:
                        in_string = False
                        string_char = None
                
                if char == ',' and not in_string:
                    current_part += char
                    parts.append(current_part)
                    # Calculate indent based on open paren
                    indent = open_paren + 1
                    current_part = '\n' + ' ' * indent
                else:
                    current_part += char
                    
            parts.append(current_part)
            line = ''.join(parts)
        # Last resort: Just insert a newline at character 79
        elif len(line) > 79:
            # Find a good break point near char 79
            break_at = 79
            while break_at > 0 and line[break_at] not in [' ', ',', '.']:
                break_at -= 1
            
            if break_at > 0:
                indent = len(line) - len(line.lstrip())
                line = line[:break_at] + '\n' + ' ' * indent + line[break_at:].lstrip()

    return line

def fix_file(file_path, lines_to_fix):
    """Fix specific lines in a file."""
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    modified = False
    for line_no in lines_to_fix:
        # Line numbers are 1-indexed but list is 0-indexed
        if 0 <= line_no-1 < len(lines):
            original = lines[line_no-1]
            fixed = fix_line(original, line_no, file_path)
            if fixed != original:
                lines[line_no-1] = fixed
                modified = True
                print(f"Fixed line {line_no}")
    
    if modified:
        with open(file_path, 'w') as f:
            f.writelines(lines)

if __name__ == "__main__":
    # These are the remaining lines with E501 errors from the previous flake8 output
    line_numbers = [535, 722, 817]
    fix_file("receipt_dynamo/tests/integration/test__gpt.py", line_numbers)
    print("Done. Check with flake8 to see if any E501 errors remain.") 
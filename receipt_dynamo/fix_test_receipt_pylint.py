#!/usr/bin/env python3
"""Script to fix pylint issues in test__receipt.py"""

import re

def fix_line_length(content):
    """Fix lines that are too long."""
    lines = content.split('\n')
    fixed_lines = []
    
    for line in lines:
        if len(line) <= 79:
            fixed_lines.append(line)
            continue
            
        # Handle docstrings
        if '"""' in line and line.strip().startswith('"""'):
            # Split long docstrings
            stripped = line.strip()
            if stripped.endswith('"""'):
                # Full docstring on one line
                doc_content = stripped[3:-3].strip()
                indent = len(line) - len(line.lstrip())
                indent_str = ' ' * indent
                
                if len(doc_content) > 70:
                    # Split into multiple lines
                    words = doc_content.split()
                    current_line = []
                    result_lines = [f'{indent_str}"""']
                    
                    for word in words:
                        test_line = ' '.join(current_line + [word])
                        if len(test_line) <= 70:
                            current_line.append(word)
                        else:
                            if current_line:
                                result_lines.append(f'{indent_str}{" ".join(current_line)}')
                            current_line = [word]
                    
                    if current_line:
                        result_lines.append(f'{indent_str}{" ".join(current_line)}')
                    result_lines.append(f'{indent_str}"""')
                    
                    fixed_lines.extend(result_lines)
                else:
                    fixed_lines.append(f'{indent_str}"""{doc_content}"""')
            else:
                fixed_lines.append(line)
        
        # Handle comments with UUIDs
        elif "# Fixed UUID for test consistency" in line:
            # Split before the comment
            code_part = line[:line.index("#")].rstrip()
            comment_part = "  # Fixed UUID for test consistency"
            fixed_lines.append(code_part)
            fixed_lines.append(" " * (len(code_part) - len(code_part.lstrip())) + comment_part)
        
        # Handle long string literals in test parameters
        elif "Unexpected error during" in line and line.strip().endswith('",'):
            # Find the string content
            match = re.search(r'"([^"]+)"', line)
            if match:
                string_content = match.group(1)
                indent = len(line) - len(line.lstrip())
                indent_str = ' ' * indent
                
                # Split the string if it's too long
                if "must be" in string_content:
                    parts = string_content.split("must be", 1)
                    fixed_lines.append(f'{indent_str}"{parts[0]}"')
                    fixed_lines.append(f'{indent_str}"must be{parts[1]}",')
                else:
                    fixed_lines.append(line)
            else:
                fixed_lines.append(line)
        else:
            fixed_lines.append(line)
    
    return '\n'.join(fixed_lines)

def fix_protected_access(content):
    """Fix protected member access by adding pylint disable comments."""
    lines = content.split('\n')
    fixed_lines = []
    
    for line in lines:
        if 'client._client' in line and 'mocker.patch.object' in line:
            # Add pylint disable comment
            fixed_lines.append(line)
            if not any('pylint: disable' in l for l in fixed_lines[-5:]):
                # Insert disable comment before the statement
                indent = len(line) - len(line.lstrip())
                fixed_lines.insert(-1, ' ' * indent + '# pylint: disable=protected-access')
        elif 'batch_writer()._queue.put' in line:
            fixed_lines.append(line)
            if not any('pylint: disable' in l for l in fixed_lines[-5:]):
                indent = len(line) - len(line.lstrip())
                fixed_lines.insert(-1, ' ' * indent + '# pylint: disable=protected-access')
        else:
            fixed_lines.append(line)
    
    return '\n'.join(fixed_lines)

def fix_too_many_args(content):
    """Add disable comments for functions with too many args."""
    lines = content.split('\n')
    fixed_lines = []
    
    functions_with_many_args = [
        'test_add_receipt_client_errors',
        'test_update_receipt_client_errors',
        'test_delete_receipt_client_errors',
        'test_get_receipt_client_errors',
        'test_add_receipts_client_errors',
        'test_update_receipts_client_errors',
        'test_delete_receipts_client_errors',
    ]
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Check if this is one of the functions with many args
        if any(f'def {func}(' in line for func in functions_with_many_args):
            # Find the function definition start
            func_start = i
            # Add disable comment before the function
            if func_start > 0 and 'pylint: disable' not in lines[func_start - 1]:
                indent = len(line) - len(line.lstrip())
                fixed_lines.append(' ' * indent + '# pylint: disable=too-many-positional-arguments')
            
        fixed_lines.append(line)
        i += 1
    
    return '\n'.join(fixed_lines)

def fix_unused_variable(content):
    """Fix unused variable warning."""
    return content.replace(
        'receipt, lines, words, letters, labels = dynamo_client.get_receipt_details(',
        'receipt, lines, words, letters, _ = dynamo_client.get_receipt_details('
    )

def fix_no_else_return(content):
    """Fix unnecessary else after return."""
    lines = content.split('\n')
    fixed_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        if 'else:' in line and i > 0 and 'return' in lines[i-1]:
            # Check if this is the specific case we need to fix
            if i + 1 < len(lines) and 'with pytest.raises' in lines[i + 1]:
                # Remove the else and dedent the block
                indent = len(line) - len(line.lstrip()) - 4
                fixed_lines.append(' ' * indent + '# Test the error case')
                i += 1
                # Process the indented block
                while i < len(lines) and (lines[i].strip() == '' or len(lines[i]) - len(lines[i].lstrip()) > indent):
                    if lines[i].strip():
                        # Dedent by 4 spaces
                        dedented = ' ' * indent + lines[i].lstrip()
                        fixed_lines.append(dedented)
                    else:
                        fixed_lines.append(lines[i])
                    i += 1
                continue
        
        fixed_lines.append(line)
        i += 1
    
    return '\n'.join(fixed_lines)

# Read the file
with open('tests/integration/test__receipt.py', 'r') as f:
    content = f.read()

# Apply fixes
content = fix_line_length(content)
content = fix_protected_access(content)
content = fix_too_many_args(content)
content = fix_unused_variable(content)
content = fix_no_else_return(content)

# Write the fixed content
with open('tests/integration/test__receipt.py', 'w') as f:
    f.write(content)

print("Fixed pylint issues in test__receipt.py")
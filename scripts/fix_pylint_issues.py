#!/usr/bin/env python3
"""Script to fix common pylint issues in handler files."""

import re
import sys
from pathlib import Path


def fix_logging_format(content: str) -> str:
    """Fix W1203: Use lazy % formatting in logging functions."""
    # Match logger calls with f-strings
    pattern = r'(logger\.\w+)\(f"([^"]+)"\)'
    
    def replace_fstring(match):
        method = match.group(1)
        msg = match.group(2)
        
        # Extract variables from f-string
        vars_pattern = r'\{([^}]+)\}'
        vars_found = re.findall(vars_pattern, msg)
        
        if not vars_found:
            # No variables, just return as regular string
            return f'{method}("{msg}")'
        
        # Replace {var} with %s
        new_msg = re.sub(vars_pattern, '%s', msg)
        
        # Build the argument list
        args = ', '.join(vars_found)
        
        return f'{method}("{new_msg}", {args})'
    
    return re.sub(pattern, replace_fstring, content)


def fix_unused_arguments(content: str) -> str:
    """Fix W0613: Unused argument by adding pylint disable comments."""
    lines = content.split('\n')
    new_lines = []
    
    for line in lines:
        # Look for function definitions with 'context' parameter
        if 'def ' in line and 'context' in line and '# pylint: disable=' not in line:
            # Add pylint disable if not already present
            if 'context: Any' in line or 'context)' in line:
                line = line.rstrip() + '  # pylint: disable=unused-argument'
        new_lines.append(line)
    
    return '\n'.join(new_lines)


def fix_line_length(content: str) -> str:
    """Fix C0301: Line too long by breaking long lines."""
    lines = content.split('\n')
    new_lines = []
    
    for line in lines:
        if len(line) > 79:
            # Try to break at logical points
            if 'logger.' in line and '", ' in line:
                # Break logging lines after the message
                parts = line.split('", ', 1)
                if len(parts) == 2:
                    indent = len(line) - len(line.lstrip())
                    new_lines.append(parts[0] + '",')
                    new_lines.append(' ' * (indent + 4) + parts[1])
                else:
                    new_lines.append(line)
            elif '# ' in line and len(line.split('# ')[0]) < 79:
                # Move comment to next line
                code, comment = line.split('# ', 1)
                new_lines.append(code.rstrip())
                indent = len(code) - len(code.lstrip())
                new_lines.append(' ' * indent + '# ' + comment)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
    
    return '\n'.join(new_lines)


def process_file(filepath: Path) -> bool:
    """Process a single file to fix pylint issues."""
    try:
        content = filepath.read_text()
        original = content
        
        # Apply fixes
        content = fix_logging_format(content)
        content = fix_unused_arguments(content)
        content = fix_line_length(content)
        
        if content != original:
            filepath.write_text(content)
            print(f"Fixed: {filepath}")
            return True
        else:
            print(f"No changes: {filepath}")
            return False
    except Exception as e:
        print(f"Error processing {filepath}: {e}")
        return False


def main():
    """Main function to process all handler files."""
    handlers_dir = Path("infra/embedding_step_functions/unified_embedding/handlers")
    
    if not handlers_dir.exists():
        print(f"Directory not found: {handlers_dir}")
        sys.exit(1)
    
    files_fixed = 0
    for py_file in handlers_dir.glob("*.py"):
        if py_file.name != "__init__.py":
            if process_file(py_file):
                files_fixed += 1
    
    print(f"\nFixed {files_fixed} files")


if __name__ == "__main__":
    main()
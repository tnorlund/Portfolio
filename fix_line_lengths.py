#!/usr/bin/env python3
"""Fix line length issues in Python files."""

import re
import sys
from pathlib import Path


def fix_line_length(line: str, max_length: int = 79) -> str:
    """Fix a line that's too long by splitting it appropriately."""
    if len(line) <= max_length:
        return line
    
    # Check if it's a dictionary entry with a long string
    dict_match = re.match(r'^(\s*"[^"]+": )"(.+)",?$', line)
    if dict_match:
        indent = dict_match.group(1)
        content = dict_match.group(2)
        trailing = "," if line.rstrip().endswith(",") else ""
        
        # Split the content at a reasonable point
        if len(indent) + len(content) + 3 > max_length:
            # Find a good split point
            split_point = max_length - len(indent) - 10
            
            # Try to split at a word boundary
            if " " in content[:split_point]:
                split_idx = content[:split_point].rfind(" ")
            else:
                split_idx = split_point
            
            part1 = content[:split_idx]
            part2 = content[split_idx:].lstrip()
            
            # Return the split line
            return f'{indent}"{part1}"\n{" " * len(indent)}" {part2}"{trailing}'
    
    # Check if it's a comment line
    if line.strip().startswith("#"):
        indent = len(line) - len(line.lstrip())
        content = line.strip()
        
        if len(content) > max_length - indent:
            # Split comment at word boundary
            split_point = max_length - indent - 2
            if " " in content[:split_point]:
                split_idx = content[:split_point].rfind(" ")
                part1 = content[:split_idx]
                part2 = content[split_idx + 1:]
                return f'{" " * indent}{part1}\n{" " * indent}# {part2}'
    
    return line


def process_file(file_path: Path):
    """Process a single Python file to fix line lengths."""
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    modified = False
    new_lines = []
    
    for line in lines:
        if len(line.rstrip()) > 79:
            fixed = fix_line_length(line.rstrip())
            if fixed != line.rstrip():
                modified = True
                new_lines.append(fixed + '\n')
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
    
    if modified:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print(f"Fixed line lengths in {file_path}")


def main():
    """Main function."""
    if len(sys.argv) > 1:
        files = [Path(f) for f in sys.argv[1:]]
    else:
        # Process all Python files in receipt_label
        files = Path('receipt_label').rglob('*.py')
    
    for file_path in files:
        if file_path.exists() and file_path.suffix == '.py':
            process_file(file_path)


if __name__ == '__main__':
    main()
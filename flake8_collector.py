#!/usr/bin/env python3

import subprocess
import os
import re
import argparse
from collections import defaultdict
import glob
import sys


def run_flake8(file_path='.', exclude=None, config_file=None):
    """
    Run flake8 on the specified file or directory and return the output.
    
    Args:
        file_path (str): File or directory to run flake8 on
        exclude (list): List of patterns to exclude
        config_file (str): Path to flake8 config file
    
    Returns:
        str: Output from flake8
    """
    cmd = ['flake8']
    
    # Add file path
    cmd.append(file_path)
    
    # Add exclude patterns if specified
    if exclude:
        exclude_arg = '--exclude=' + ','.join(exclude)
        cmd.append(exclude_arg)
    
    # Add config file if specified
    if config_file:
        cmd.extend(['--config', config_file])
    
    # Run flake8 and capture output
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False  # Don't raise exception on flake8 errors
        )
        return result.stdout
    except Exception as e:
        print(f"Error running flake8: {e}")
        return ""


def parse_flake8_output(output):
    """
    Parse flake8 output and group by file.
    
    Args:
        output (str): Output from flake8
    
    Returns:
        dict: Dictionary with file paths as keys and lists of errors as values
    """
    errors_by_file = defaultdict(list)
    
    # Regular expression to parse flake8 output lines
    # Format: file_path:line:column: error_code error_message
    pattern = r'^(.+?):(\d+):(\d+): ([A-Z]\d+) (.+)$'
    
    for line in output.strip().split('\n'):
        if not line:
            continue
            
        match = re.match(pattern, line)
        if match:
            file_path, line_num, col_num, error_code, error_msg = match.groups()
            
            error_info = {
                'line': int(line_num),
                'column': int(col_num),
                'code': error_code,
                'message': error_msg
            }
            
            errors_by_file[file_path].append(error_info)
    
    return errors_by_file


def format_errors_for_chatgpt_prompt(file_path, errors, file_content=None):
    """
    Format errors for a single file as a ChatGPT prompt that will return only the fixed code.
    
    Args:
        file_path (str): Path to the file
        errors (list): List of errors for the file
        file_content (str, optional): Content of the file
        
    Returns:
        str: Formatted string for ChatGPT prompt
    """
    output_lines = []
    
    # Add a prompt header
    output_lines.append("# Fix Flake8 Errors - Code Only Response")
    output_lines.append("")
    
    # Add clear instructions for ChatGPT
    output_lines.append("## Instructions")
    output_lines.append("Fix all the Flake8 errors in the file below and return ONLY the corrected code. Do not include explanations, comments about what you changed, or any text other than the properly formatted Python code. The fixed code should be returned as a single code block.")
    output_lines.append("")
    
    # Add file info
    output_lines.append(f"## File: {file_path}")
    output_lines.append(f"Total errors: {len(errors)}")
    output_lines.append("")
    
    # Add error list
    output_lines.append("## Errors to Fix")
    for error in sorted(errors, key=lambda e: (e['line'], e['column'])):
        output_lines.append(f"Line {error['line']}, Column {error['column']}: {error['code']} - {error['message']}")
    output_lines.append("")
    
    # Add file content (required for this use case)
    if file_content:
        output_lines.append("## Original Code")
        output_lines.append("```python")
        output_lines.append(file_content)
        output_lines.append("```")
        output_lines.append("")
        output_lines.append("## Return ONLY the fixed code below")
    else:
        output_lines.append("ERROR: File content is required to fix Flake8 errors.")
    
    return "\n".join(output_lines)


def read_file_content(file_path):
    """
    Read the content of a file.
    
    Args:
        file_path (str): Path to the file
        
    Returns:
        str: Content of the file or None if file doesn't exist
    """
    try:
        with open(file_path, 'r') as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Could not read file {file_path}: {e}")
        return None


def get_python_files(directory):
    """
    Get all Python files in a directory (recursively).
    
    Args:
        directory (str): Directory to search in
        
    Returns:
        list: List of Python file paths
    """
    if os.path.isfile(directory) and directory.endswith('.py'):
        return [directory]
    
    python_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                python_files.append(os.path.join(root, file))
    
    return python_files


def get_script_directory():
    """
    Get the directory where this script is located.
    
    Returns:
        str: Directory path
    """
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def main():
    parser = argparse.ArgumentParser(description='Generate ChatGPT prompts for fixing Flake8 errors - code only response')
    parser.add_argument('-d', '--directory', default='.', help='Directory to scan for Python files')
    parser.add_argument('-e', '--exclude', nargs='*', help='Patterns to exclude')
    parser.add_argument('-c', '--config', help='Path to flake8 config file')
    parser.add_argument('-f', '--file_list', help='Path to a file containing a list of files to check, one per line')
    parser.add_argument('-o', '--output_dir', help='Output directory for ChatGPT prompts (default: same directory as this script)')
    parser.add_argument('-s', '--suffix', default='_fix', help='Suffix to add to markdown filenames (default: _fix)')
    
    args = parser.parse_args()
    
    # Determine files to check
    files_to_check = []
    if args.file_list:
        try:
            with open(args.file_list, 'r') as f:
                files_to_check = [line.strip() for line in f if line.strip()]
        except Exception as e:
            print(f"Error reading file list: {e}")
            return
    else:
        # Get all Python files in the directory
        files_to_check = get_python_files(args.directory)
    
    if not files_to_check:
        print(f"No Python files found in {args.directory}")
        return
    
    print(f"Checking {len(files_to_check)} Python files for Flake8 errors...")
    
    # Determine output directory
    if args.output_dir:
        output_dir = args.output_dir
    else:
        # Use the directory where this script is located
        output_dir = get_script_directory()
    
    # Create output directory if needed
    os.makedirs(output_dir, exist_ok=True)
    
    # Process each file individually
    files_with_errors = 0
    total_errors = 0
    
    for file_path in files_to_check:
        flake8_output = run_flake8(file_path, args.exclude, args.config)
        
        if not flake8_output:
            continue  # No errors for this file
        
        errors_by_file = parse_flake8_output(flake8_output)
        
        if not errors_by_file or file_path not in errors_by_file:
            continue  # No errors for this file
        
        errors = errors_by_file[file_path]
        
        # Explicitly read the file content
        file_content = read_file_content(file_path)
        
        if not file_content:
            print(f"Warning: Could not read content of {file_path}, skipping...")
            continue
        
        # Create the prompt with the file content
        prompt = format_errors_for_chatgpt_prompt(file_path, errors, file_content)
        
        # Create a clean filename based on the Python file path
        file_name = os.path.basename(file_path)
        base_name, _ = os.path.splitext(file_name)
        output_file = os.path.join(output_dir, f"{base_name}{args.suffix}.md")
        
        # Write the prompt to the output file
        with open(output_file, 'w') as f:
            f.write(prompt)
        
        files_with_errors += 1
        total_errors += len(errors)
        print(f"Created prompt for {file_path} with {len(errors)} errors -> {output_file}")
    
    if files_with_errors == 0:
        print("No Flake8 errors found in any files!")
    else:
        print(f"Created prompts for {files_with_errors} files with a total of {total_errors} errors.")
        print(f"All prompts are saved in: {output_dir}")


if __name__ == '__main__':
    main() 
#!/usr/bin/env python3

import subprocess
import os
import re
import argparse
from collections import defaultdict
import glob
import sys
import json
import time
import requests
from typing import Optional, Dict, List, Any, Tuple

# Constants for API
DEFAULT_MODEL = "gpt-4"
DEFAULT_TEMPERATURE = 0.2
API_URL = "https://api.openai.com/v1/chat/completions"


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


def call_chatgpt_api(prompt: str, api_key: str, model: str = DEFAULT_MODEL, 
                     temperature: float = DEFAULT_TEMPERATURE) -> Optional[str]:
    """
    Call the ChatGPT API with the given prompt.
    
    Args:
        prompt (str): The prompt to send to the API
        api_key (str): The OpenAI API key
        model (str): The model to use (default: "gpt-4")
        temperature (float): The temperature to use (default: 0.2)
        
    Returns:
        str: The fixed code, or None if an error occurred
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    messages = [
        {"role": "system", "content": "You are a helpful assistant that fixes Flake8 errors in Python code. Return only the fixed code as a Python code block without any additional explanations."},
        {"role": "user", "content": prompt}
    ]
    
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature
    }
    
    try:
        response = requests.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()
        
        result = response.json()
        if "choices" in result and len(result["choices"]) > 0:
            content = result["choices"][0]["message"]["content"]
            
            # Extract code from markdown code block
            code_pattern = r"```(?:python)?\s*([\s\S]*?)\s*```"
            match = re.search(code_pattern, content)
            
            if match:
                return match.group(1).strip()
            else:
                # If no code block found, return the raw content
                # (in case the model didn't use code blocks)
                return content.strip()
        else:
            print("Error: Unexpected API response format")
            return None
    except requests.exceptions.RequestException as e:
        print(f"API request error: {e}")
        return None
    except json.JSONDecodeError:
        print("Error decoding API response")
        return None
    except Exception as e:
        print(f"Unexpected error calling API: {e}")
        return None


def validate_fixed_code(code: str, file_path: str, exclude=None, config_file=None) -> bool:
    """
    Validate the fixed code by running flake8 on it.
    
    Args:
        code (str): The fixed code
        file_path (str): The path to the original file (for error messages)
        exclude (list): List of patterns to exclude
        config_file (str): Path to a Flake8 config file
        
    Returns:
        bool: True if validation passed (no errors), False otherwise
    """
    # Create a temporary file
    temp_file = f"{file_path}.temp"
    try:
        with open(temp_file, 'w') as f:
            f.write(code)
            
        # Run flake8 on the temporary file
        output = run_flake8(temp_file, exclude, config_file)
        
        if not output:
            return True
        else:
            print(f"Warning: Fixed code for {file_path} still has Flake8 errors:")
            print(output)
            return False
    finally:
        # Clean up the temporary file
        if os.path.exists(temp_file):
            os.remove(temp_file)


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
    
    # API-related arguments
    parser.add_argument('--api', action='store_true', help='Use ChatGPT API to automatically fix errors')
    parser.add_argument('--api-key', help='OpenAI API key (if not set, uses OPENAI_API_KEY environment variable)')
    parser.add_argument('--model', default=DEFAULT_MODEL, help=f'OpenAI model to use (default: {DEFAULT_MODEL})')
    parser.add_argument('--temperature', type=float, default=DEFAULT_TEMPERATURE, 
                      help=f'Temperature for API requests (default: {DEFAULT_TEMPERATURE})')
    parser.add_argument('--apply', action='store_true', help='Apply the fixes to the original files (requires --api)')
    parser.add_argument('--fixed-suffix', default='_fixed', help='Suffix for fixed Python files (default: _fixed)')
    parser.add_argument('--validate', action='store_true', help='Validate the fixed code by running flake8 on it again')
    
    args = parser.parse_args()
    
    # Check if --apply requires --api
    if args.apply and not args.api:
        print("Error: --apply requires --api")
        sys.exit(1)
    
    # Get API key if using API
    api_key = None
    if args.api:
        api_key = args.api_key or os.environ.get('OPENAI_API_KEY')
        if not api_key:
            print("Error: OpenAI API key is required. Set it with --api-key or OPENAI_API_KEY environment variable.")
            sys.exit(1)
    
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
    fixed_files = 0
    
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
        base_name, ext = os.path.splitext(file_name)
        
        if args.api:
            print(f"Sending {file_path} to ChatGPT API for fixing ({len(errors)} errors)...")
            fixed_code = call_chatgpt_api(prompt, api_key, args.model, args.temperature)
            
            if fixed_code:
                # Validate the fixed code if requested
                if args.validate:
                    print(f"Validating fixed code for {file_path}...")
                    valid = validate_fixed_code(fixed_code, file_path, args.exclude, args.config)
                    if not valid:
                        print(f"Warning: Fixed code for {file_path} still has Flake8 errors.")
                
                # Save the fixed code
                if args.apply:
                    # Backup the original file
                    backup_file = f"{file_path}.bak"
                    try:
                        with open(backup_file, 'w') as f:
                            f.write(file_content)
                        
                        # Apply the fix to the original file
                        with open(file_path, 'w') as f:
                            f.write(fixed_code)
                        
                        print(f"Applied fixes to {file_path} (backup saved as {backup_file})")
                    except Exception as e:
                        print(f"Error applying fixes to {file_path}: {e}")
                else:
                    # Save to a new file
                    fixed_file = os.path.join(
                        os.path.dirname(file_path), 
                        f"{base_name}{args.fixed_suffix}{ext}"
                    )
                    
                    try:
                        with open(fixed_file, 'w') as f:
                            f.write(fixed_code)
                        
                        print(f"Saved fixed code to {fixed_file}")
                    except Exception as e:
                        print(f"Error saving fixed code to {fixed_file}: {e}")
                
                fixed_files += 1
            else:
                print(f"Error: Failed to fix {file_path} using the API")
        
        # Always save the prompt file
        output_file = os.path.join(output_dir, f"{base_name}{args.suffix}.md")
        
        # Write the prompt to the output file
        with open(output_file, 'w') as f:
            f.write(prompt)
        
        files_with_errors += 1
        total_errors += len(errors)
        
        if not args.api:
            print(f"Created prompt for {file_path} with {len(errors)} errors -> {output_file}")
    
    if files_with_errors == 0:
        print("No Flake8 errors found in any files!")
    else:
        print(f"\nSummary:")
        print(f"- Files with errors: {files_with_errors}")
        print(f"- Total errors found: {total_errors}")
        
        if args.api:
            print(f"- Files successfully fixed: {fixed_files}")
        
        print(f"- All prompts are saved in: {output_dir}")
        
        if args.api and args.apply:
            print("\nNote: Original files have been modified. Backups with .bak extension were created.")


if __name__ == '__main__':
    main() 